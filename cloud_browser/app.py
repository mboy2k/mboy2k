"""Cloud browser API: a 24/7 Playwright browser your AI agent can call over HTTP.

Runs on Modal (https://modal.com) with `modal deploy app.py`, or standalone
with `python app.py` on any machine that has Playwright installed.
"""

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "8"))
SESSION_IDLE_TIMEOUT = int(os.environ.get("SESSION_IDLE_TIMEOUT", "900"))
MAX_TEXT_CHARS = int(os.environ.get("MAX_TEXT_CHARS", "200000"))

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]


@dataclass
class Session:
    context: object = None
    page: object = None
    last_used: float = field(default_factory=time.monotonic)


class BrowserPool:
    def __init__(self):
        self._pw = None
        self._browser = None
        self.sessions: dict[str, Session] = {}

    async def _ensure_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return
        if self._pw is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True, args=LAUNCH_ARGS)

    def _touch(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, f"unknown session_id: {session_id}")
        session.last_used = time.monotonic()
        return session

    async def _reap(self):
        now = time.monotonic()
        stale = [s for s, v in self.sessions.items() if now - v.last_used > SESSION_IDLE_TIMEOUT]
        for sid in stale:
            await self.close(sid)

    async def open(self, url: str, session_id: Optional[str] = None,
                   wait_until: str = "domcontentloaded", timeout_ms: int = 30000) -> dict:
        if not (url.startswith(("http://", "https://", "data:", "about:"))):
            raise HTTPException(400, "url must be http(s), data:, or about:blank")
        await self._reap()
        await self._ensure_browser()
        session = self.sessions.get(session_id) if session_id else None
        if session is None:
            if session_id is None:
                session_id = uuid.uuid4().hex[:12]
            elif len(self.sessions) >= MAX_SESSIONS:
                oldest = min(self.sessions, key=lambda s: self.sessions[s].last_used)
                await self.close(oldest)
            context = await self._browser.new_context(
                user_agent=USER_AGENT, viewport={"width": 1280, "height": 800}, locale="en-US",
            )
            page = await context.new_page()
            session = Session(context=context, page=page)
            self.sessions[session_id] = session
        session.last_used = time.monotonic()
        try:
            response = await session.page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        except Exception as exc:
            raise HTTPException(504, f"navigation failed: {exc}")
        return {
            "session_id": session_id,
            "url": session.page.url,
            "http_status": response.status if response else None,
        }

    async def snapshot(self, session_id: str) -> dict:
        page = self._touch(session_id).page
        title = await page.title()
        text = await page.evaluate("document.body ? document.body.innerText : ''")
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({text: (e.innerText || '').trim().slice(0, 120), href: e.href}))"
            ".filter(l => l.text || l.href)",
        )
        return {
            "session_id": session_id,
            "title": title,
            "url": page.url,
            "text": text[:MAX_TEXT_CHARS],
            "links": (links or [])[:300],
        }

    async def screenshot(self, session_id: str, full_page: bool = False) -> bytes:
        page = self._touch(session_id).page
        return await page.screenshot(full_page=full_page, type="png")

    async def click(self, session_id: str, selector: str, timeout_ms: int = 15000):
        page = self._touch(session_id).page
        try:
            await page.click(selector, timeout=timeout_ms)
        except Exception as exc:
            raise HTTPException(400, f"click failed: {exc}")

    async def type(self, session_id: str, selector: str, text: str,
                   submit: bool = False, timeout_ms: int = 15000):
        page = self._touch(session_id).page
        try:
            await page.fill(selector, text, timeout=timeout_ms)
            if submit:
                await page.keyboard.press("Enter")
        except Exception as exc:
            raise HTTPException(400, f"type failed: {exc}")

    async def press(self, session_id: str, key: str):
        page = self._touch(session_id).page
        try:
            await page.keyboard.press(key)
        except Exception as exc:
            raise HTTPException(400, f"press failed: {exc}")

    async def evaluate(self, session_id: str, script: str):
        page = self._touch(session_id).page
        try:
            return await page.evaluate(script)
        except Exception as exc:
            raise HTTPException(400, f"evaluate failed: {exc}")

    async def wait_for(self, session_id: str, selector: str, timeout_ms: int = 15000):
        page = self._touch(session_id).page
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
        except Exception as exc:
            raise HTTPException(504, f"wait failed: {exc}")

    async def close(self, session_id: str):
        session = self.sessions.pop(session_id, None)
        if session:
            try:
                await session.context.close()
            except Exception:
                pass


def build_mcp(pool: BrowserPool):
    """MCP (Model Context Protocol) server so MCP-capable agents can use the browser natively."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("cloud-browser")

    @mcp.tool()
    async def browser_open(url: str, session_id: str | None = None) -> dict:
        """Open a URL in the cloud browser (new or existing session). Returns session_id."""
        return await pool.open(url, session_id)

    @mcp.tool()
    async def browser_snapshot(session_id: str) -> dict:
        """Get title, rendered text and links of the current page."""
        return await pool.snapshot(session_id)

    @mcp.tool()
    async def browser_click(session_id: str, selector: str) -> dict:
        """Click an element matching a CSS selector."""
        await pool.click(session_id, selector)
        return {"ok": True}

    @mcp.tool()
    async def browser_type(session_id: str, selector: str, text: str, submit: bool = False) -> dict:
        """Fill a text field; submit=True presses Enter after typing."""
        await pool.type(session_id, selector, text, submit)
        return {"ok": True}

    @mcp.tool()
    async def browser_eval(session_id: str, script: str) -> dict:
        """Run JavaScript in the page and return the result."""
        return {"result": await pool.evaluate(session_id, script)}

    @mcp.tool()
    async def browser_screenshot(session_id: str, full_page: bool = False):
        """Take a PNG screenshot of the current page."""
        from mcp.server.fastmcp import Image

        png = await pool.screenshot(session_id, full_page)
        return Image(data=png, format="png")

    @mcp.tool()
    async def browser_close(session_id: str) -> dict:
        """Close a browser session."""
        await pool.close(session_id)
        return {"ok": True}

    return mcp.streamable_http_app(), mcp.session_manager


def create_app() -> FastAPI:
    import secrets as pysecrets
    from contextlib import AsyncExitStack, asynccontextmanager

    api_key = os.environ.get("BROWSER_API_KEY", "")
    pool = BrowserPool()

    mcp_app = None
    mcp_session_manager = None
    try:
        mcp_app, mcp_session_manager = build_mcp(pool)
    except Exception:
        mcp_app = None  # REST keeps working if the MCP SDK is absent

    @asynccontextmanager
    async def lifespan(_app):
        async with AsyncExitStack() as stack:
            if mcp_session_manager is not None:
                await stack.enter_async_context(mcp_session_manager.run())
            yield

    app = FastAPI(title="Cloud Browser", version="1.1.0",
                  description="Headless Chromium controlled over HTTP, built for AI agents.",
                  lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def mcp_auth(request, call_next):
        if mcp_app is not None and api_key and request.url.path.startswith("/mcp"):
            key = request.headers.get("X-API-Key")
            if not key or not pysecrets.compare_digest(key, api_key):
                return JSONResponse({"detail": "invalid or missing X-API-Key header"}, status_code=401)
        return await call_next(request)

    def auth(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
        if not api_key:
            return
        if not x_api_key or not pysecrets.compare_digest(x_api_key, api_key):
            raise HTTPException(401, "invalid or missing X-API-Key header")

    class OpenReq(BaseModel):
        url: str
        session_id: Optional[str] = None
        wait_until: str = "domcontentloaded"
        timeout_ms: int = 30000

    class SessionReq(BaseModel):
        session_id: str

    class ShotReq(SessionReq):
        full_page: bool = False

    class ClickReq(SessionReq):
        selector: str
        timeout_ms: int = 15000

    class TypeReq(ClickReq):
        text: str
        submit: bool = False

    class PressReq(SessionReq):
        key: str

    class EvalReq(SessionReq):
        script: str

    class WaitReq(ClickReq):
        pass

    @app.get("/")
    async def root():
        return {
            "service": "cloud-browser",
            "docs": "/docs",
            "actions": ["open", "snapshot", "screenshot", "click", "type", "press", "eval", "wait", "close"],
            "mcp": "/mcp" if mcp_app is not None else None,
        }

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "auth_required": bool(api_key),
            "active_sessions": len(pool.sessions),
            "max_sessions": MAX_SESSIONS,
        }

    @app.post("/open", dependencies=[Depends(auth)])
    async def open_url(req: OpenReq):
        return await pool.open(req.url, req.session_id, req.wait_until, req.timeout_ms)

    @app.post("/snapshot", dependencies=[Depends(auth)])
    async def snapshot(req: SessionReq):
        return await pool.snapshot(req.session_id)

    @app.post("/screenshot", dependencies=[Depends(auth)])
    async def screenshot(req: ShotReq):
        png = await pool.screenshot(req.session_id, req.full_page)
        return Response(content=png, media_type="image/png")

    @app.post("/click", dependencies=[Depends(auth)])
    async def click(req: ClickReq):
        await pool.click(req.session_id, req.selector, req.timeout_ms)
        return {"ok": True}

    @app.post("/type", dependencies=[Depends(auth)])
    async def type_text(req: TypeReq):
        await pool.type(req.session_id, req.selector, req.text, req.submit, req.timeout_ms)
        return {"ok": True}

    @app.post("/press", dependencies=[Depends(auth)])
    async def press(req: PressReq):
        await pool.press(req.session_id, req.key)
        return {"ok": True}

    @app.post("/eval", dependencies=[Depends(auth)])
    async def evaluate(req: EvalReq):
        return {"result": await pool.evaluate(req.session_id, req.script)}

    @app.post("/wait", dependencies=[Depends(auth)])
    async def wait_for(req: WaitReq):
        await pool.wait_for(req.session_id, req.selector, req.timeout_ms)
        return {"ok": True}

    @app.post("/close", dependencies=[Depends(auth)])
    async def close(req: SessionReq):
        await pool.close(req.session_id)
        return {"ok": True}

    if mcp_app is not None:
        # Mounted last so REST routes match first. FastMCP serves at /mcp inside
        # its own app; mounting at root avoids /mcp/mcp and the 307 redirect.
        app.mount("/", mcp_app)

    return app


try:
    import modal
except ImportError:
    modal = None

if modal is not None:
    def _modal_secrets():
        # Deploy succeeds even before the secret exists; auth stays off until it is created.
        try:
            secret = modal.Secret.from_name("browser-api-key", env_keys=["BROWSER_API_KEY"])
            secret.hydrate()
            return [secret]
        except Exception:
            return []

    playwright_image = (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install("playwright==1.63.0", "fastapi>=0.115")
        .run_commands("playwright install --with-deps chromium")
    )

    app = modal.App("cloud-browser")

    @app.function(
        image=playwright_image,
        secrets=_modal_secrets(),
        keep_warm=1,  # keeps one container alive 24/7 so the agent never hits a cold start
        max_containers=1,  # single container keeps in-memory browser sessions
        cpu=2,
        memory=2048,
        timeout=600,
    )
    @modal.asgi_app()
    def api():
        return create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8099")))

