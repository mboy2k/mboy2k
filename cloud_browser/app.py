"""Cloud browser API: a 24/7 Playwright browser your AI agent can call over HTTP.

Runs on Modal (https://modal.com) with `modal deploy app.py`, or standalone
with `python app.py` on any machine that has Playwright installed.
"""

import os
import asyncio
import re
import shutil
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
PROFILES_DIR = os.environ.get("BROWSER_PROFILES_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "browser_data"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]
BLOCKABLE = {
    "images": {"image"},
    "media": {"media"},
    "font": {"font"},
    "stylesheet": {"stylesheet"},
}
JS_MARKDOWN = """
() => {
  const md = [];
  document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,blockquote,pre').forEach(el => {
    const t = (el.innerText || '').trim(); if (!t) return;
    const tag = el.tagName.toLowerCase();
    if (tag.startsWith('h')) md.push('#'.repeat(+tag[1]) + ' ' + t);
    else if (tag === 'li') md.push('- ' + t);
    else if (tag === 'blockquote') md.push('> ' + t);
    else if (tag === 'pre') md.push('```\\n' + t + '\\n```');
    else md.push(t);
  });
  return md.join('\\n\\n');
}
"""


@dataclass
class Session:
    context: object = None
    page: object = None
    options: dict = field(default_factory=dict)
    profile: Optional[str] = None
    last_used: float = field(default_factory=time.monotonic)


class BrowserPool:
    def __init__(self):
        self._create_lock = asyncio.Lock()
        self._pw = None
        self._browsers: dict[str, object] = {}  # one browser process per proxy
        self.sessions: dict[str, Session] = {}
        self._profile_sessions: dict[str, str] = {}  # profile -> session_id

    def _profile_dir(self, profile: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
            raise HTTPException(400, "profile name must match [A-Za-z0-9_-]{1,64}")
        path = os.path.join(PROFILES_DIR, profile)
        os.makedirs(path, exist_ok=True)
        return path

    async def _ensure_browser(self, proxy: Optional[str] = None):
        if self._pw is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
        key = proxy or ""
        browser = self._browsers.get(key)
        if browser is not None and browser.is_connected():
            return browser
        kwargs = {"headless": True, "args": LAUNCH_ARGS}
        if proxy:
            kwargs["proxy"] = {"server": proxy}
        self._browsers[key] = await self._pw.chromium.launch(**kwargs)
        return self._browsers[key]

    @staticmethod
    def _context_options(o: dict) -> dict:
        opts = {
            "user_agent": o.get("user_agent") or USER_AGENT,
            "viewport": {"width": o.get("viewport_width", 1280), "height": o.get("viewport_height", 800)},
            "locale": o.get("locale") or "en-US",
            "timezone_id": o.get("timezone"),
            "geolocation": o.get("geolocation"),
            "permissions": o.get("permissions") or [],
            "color_scheme": o.get("color_scheme") or "light",
            "is_mobile": bool(o.get("is_mobile", False)),
            "has_touch": bool(o.get("has_touch", False)),
            "device_scale_factor": o.get("device_scale_factor", 1),
        }
        return {k: v for k, v in opts.items() if v is not None}

    @staticmethod
    def _block_types(names) -> set:
        types: set = set()
        for n in names or []:
            types |= BLOCKABLE.get(n, set())
        return types

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
                   wait_until: str = "domcontentloaded", timeout_ms: int = 30000,
                   options: Optional[dict] = None) -> dict:
        if not (url.startswith(("http://", "https://", "data:", "about:"))):
            raise HTTPException(400, "url must be http(s), data:, or about:blank")
        options = options or {}
        proxy = options.get("proxy")
        if proxy and not proxy.startswith(("http://", "https://", "socks5://")):
            raise HTTPException(400, "proxy must be http(s):// or socks5:// [user:pass@]host:port")
        await self._reap()
        browser = await self._ensure_browser(proxy)
        session = self.sessions.get(session_id) if session_id else None
        if session is not None and options != session.options:
            await self.close(session_id)  # recreate so new per-session options apply
            session = None
        if session is None:
            # Serialize creation: concurrent opens would otherwise all pass the
            # cap check before any of them registers, racing past MAX_SESSIONS.
            async with self._create_lock:
                session = self.sessions.get(session_id) if session_id else None
                if session is None:
                    if len(self.sessions) >= MAX_SESSIONS:
                        oldest = min(self.sessions, key=lambda s: self.sessions[s].last_used)
                        await self.close(oldest)
                    if session_id is None:
                        session_id = uuid.uuid4().hex[:12]
                    block_types = self._block_types(options.get("block_resources"))
                    profile = options.get("profile")
                    ctx_opts = self._context_options(options)
                    if profile:
                        # Chromium headless does not reliably flush its on-disk
                        # cookie store, so profile state is snapshotted to a
                        # storage_state file on close and reloaded here.
                        state_path = os.path.join(self._profile_dir(profile), "state.json")
                        if os.path.exists(state_path):
                            ctx_opts["storage_state"] = state_path
                    context = await browser.new_context(**ctx_opts)
                    if block_types:
                        async def _block(route):
                            if route.request.resource_type in block_types:
                                await route.abort()
                            else:
                                await route.continue_()
                        await context.route("**/*", _block)
                    page = await context.new_page()
                    session = Session(context=context, page=page, options=dict(options), profile=profile)
                    self.sessions[session_id] = session
                    if profile:
                        self._profile_sessions[profile] = session_id
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

    async def snapshot(self, session_id: str, fmt: str = "text") -> dict:
        page = self._touch(session_id).page
        title = await page.title()
        if fmt == "markdown":
            text = await page.evaluate(JS_MARKDOWN)
        else:
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
            if session.profile:
                self._profile_sessions.pop(session.profile, None)
                try:
                    state_path = os.path.join(PROFILES_DIR, session.profile, "state.json")
                    await session.context.storage_state(path=state_path)
                except Exception:
                    pass
            try:
                await session.context.close()
            except Exception:
                pass


def build_mcp(pool: BrowserPool):
    """MCP (Model Context Protocol) server so MCP-capable agents can use the browser natively."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("cloud-browser")

    @mcp.tool()
    async def browser_open(url: str, session_id: str | None = None,
                           proxy: str | None = None, user_agent: str | None = None,
                           viewport_width: int = 1280, viewport_height: int = 800,
                           locale: str | None = None, timezone: str | None = None,
                           color_scheme: str | None = None,
                           block_resources: list[str] | None = None,
                           geolocation: dict | None = None,
                           profile: str | None = None) -> dict:
        """Open a URL. Session options apply at creation; resend them to recreate the session."""
        options = {k: v for k, v in dict(
            proxy=proxy, user_agent=user_agent, viewport_width=viewport_width,
            viewport_height=viewport_height, locale=locale, timezone=timezone,
            color_scheme=color_scheme, block_resources=block_resources,
            geolocation=geolocation, profile=profile,
        ).items() if v is not None}
        return await pool.open(url, session_id, options=options)

    @mcp.tool()
    async def browser_snapshot(session_id: str, format: str = "text") -> dict:
        """Get title, page text (format=text|markdown) and links of the current page."""
        return await pool.snapshot(session_id, format)

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

    @mcp.tool()
    async def browser_get_cookies(session_id: str) -> dict:
        """Get all cookies of the session."""
        session = pool._touch(session_id)
        return {"cookies": await session.context.cookies()}

    @mcp.tool()
    async def browser_set_cookies(session_id: str, cookies: list) -> dict:
        """Add cookies (list of Playwright cookie dicts with name/value/domain/path)."""
        session = pool._touch(session_id)
        await session.context.add_cookies(cookies)
        return {"ok": True}

    @mcp.tool()
    async def browser_list_profiles() -> dict:
        """List persistent login profiles (active flag shows which are open now)."""
        import os as _os

        items = []
        if _os.path.isdir(PROFILES_DIR):
            for name in sorted(_os.listdir(PROFILES_DIR)):
                items.append({"name": name, "active": name in pool._profile_sessions})
        return {"profiles": items}

    @mcp.tool()
    async def browser_delete_profile(profile: str) -> dict:
        """Delete a persistent profile and its saved login state."""
        sid = pool._profile_sessions.get(profile)
        if sid:
            await pool.close(sid)
        pool._profile_dir(profile)  # validates the name
        import shutil as _shutil

        _shutil.rmtree(os.path.join(PROFILES_DIR, profile), ignore_errors=True)
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
        # Per-session customization; applied when the session is created,
        # resend with the same session_id to recreate it with new options.
        proxy: Optional[str] = None
        user_agent: Optional[str] = None
        viewport_width: int = 1280
        viewport_height: int = 800
        locale: Optional[str] = None
        timezone: Optional[str] = None
        geolocation: Optional[dict] = None
        permissions: Optional[list] = None
        color_scheme: Optional[str] = None
        is_mobile: bool = False
        has_touch: bool = False
        device_scale_factor: float = 1
        block_resources: Optional[list] = None  # images, media, font, stylesheet
        profile: Optional[str] = None  # persistent login profile, survives close/restart

    class SessionReq(BaseModel):
        session_id: str

    class SnapshotReq(SessionReq):
        format: str = "text"  # text | markdown

    class CookiesSetReq(SessionReq):
        cookies: list  # Playwright cookie dicts

    class ProfileReq(BaseModel):
        profile: str

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
        options = req.model_dump(exclude={"url", "session_id", "wait_until", "timeout_ms"}, exclude_none=True)
        return await pool.open(req.url, req.session_id, req.wait_until, req.timeout_ms, options)

    @app.post("/snapshot", dependencies=[Depends(auth)])
    async def snapshot(req: SnapshotReq):
        return await pool.snapshot(req.session_id, req.format)

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

    @app.get("/cookies", dependencies=[Depends(auth)])
    async def get_cookies(session_id: str):
        session = pool._touch(session_id)
        return {"cookies": await session.context.cookies()}

    @app.post("/cookies", dependencies=[Depends(auth)])
    async def set_cookies(req: CookiesSetReq):
        session = pool._touch(req.session_id)
        try:
            await session.context.add_cookies(req.cookies)
        except Exception as exc:
            raise HTTPException(400, f"add_cookies failed: {exc}")
        return {"ok": True}

    @app.get("/profiles", dependencies=[Depends(auth)])
    async def list_profiles():
        items = []
        if os.path.isdir(PROFILES_DIR):
            for name in sorted(os.listdir(PROFILES_DIR)):
                items.append({"name": name, "active": name in pool._profile_sessions})
        return {"profiles": items}

    @app.post("/profiles/delete", dependencies=[Depends(auth)])
    async def delete_profile(req: ProfileReq):
        sid = pool._profile_sessions.get(req.profile)
        if sid:
            await pool.close(sid)
        pool._profile_dir(req.profile)  # validates the name
        shutil.rmtree(os.path.join(PROFILES_DIR, req.profile), ignore_errors=True)
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
        # keep_warm=0 scales to zero (cheap, ~15s cold start after ~2 min idle);
        # keep_warm=1 keeps one container alive 24/7 so agents never wait.
        keep_warm=int(os.environ.get("MODAL_KEEP_WARM", "1")),
        max_containers=1,  # single container keeps in-memory browser sessions
        cpu=int(os.environ.get("MODAL_CPU", "2")),
        memory=int(os.environ.get("MODAL_MEM_MIB", "2048")),
        timeout=600,
    )
    @modal.asgi_app()
    def api():
        return create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8099")))

