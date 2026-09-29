"""Hoplite PC: a small web desktop served by the same app as the browser API.

Three apps: a calculator (pure client-side), a stateful shell inside this
sandbox, and a remote view of the Playwright browser pool. It is reachable only
through the thread's Hoplite Preview, which keeps access inside the workspace.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE = BASE_DIR.parent
UI_FILE = BASE_DIR / "desktop_ui.html"
TERM_TIMEOUT = int(os.environ.get("PC_TERM_TIMEOUT", "60"))
VIEWPORT = {"width": 1280, "height": 800}
GEO_TTL = 600
TS_BIN = "/usr/local/bin/tailscale"
TS_SOCKET = "/var/run/tailscale/tailscaled.sock"
ACCESS_STATE = Path("/var/lib/hoplite-pc")
SSH_USER = os.environ.get("PC_SSH_USER", "root")
_GEO: dict = {"data": None, "at": 0.0}

try:  # app.py runs both as a script and as cloud_browser.app
    from machine import get_machine
except ImportError:
    from cloud_browser.machine import get_machine


class DesktopState:
    def __init__(self, pool) -> None:
        self.pool = pool
        self.machine = get_machine()
        self.shell = self.machine.shell
        self.browser_session: Optional[str] = None

    def snapshot(self) -> dict:
        payload = self.machine.info()
        payload["viewport"] = VIEWPORT
        payload["browser_session"] = self.browser_session
        return payload


async def _geo() -> dict:
    now = time.time()
    if _GEO["data"] is not None and now - _GEO["at"] < GEO_TTL:
        return _GEO["data"]
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            data = (await client.get("https://ipinfo.io/json")).json()
        _GEO.update(data={k: data.get(k) for k in ("ip", "city", "region", "country", "org")}, at=now)
    except Exception:
        _GEO["at"] = now - GEO_TTL + 60  # retry sooner, but never fail /pc/state
        if _GEO["data"] is None:
            _GEO["data"] = {"ip": None, "error": "khong lay duoc IP cong khai"}
    return _GEO["data"]


def _clamp(value: Optional[float]) -> float:
    return max(0.0, min(1.0, value or 0.0))


async def _page_info(pool, session_id: str) -> dict:
    page = pool.page_of(session_id)
    try:
        title = await page.title()
    except Exception:
        title = ""
    return {"session_id": session_id, "url": page.url, "title": title}


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return "https://www.google.com/"
    if raw.startswith(("http://", "https://", "data:", "about:")):
        return raw
    if "." in raw and " " not in raw:
        return "https://" + raw
    return "https://www.google.com/search?q=" + quote(raw)


def _tailscale_status() -> dict:
    if not Path(TS_BIN).exists():
        return {"installed": False, "state": "not_installed"}
    try:
        proc = subprocess.run(
            [TS_BIN, "--socket", TS_SOCKET, "status", "--json"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as exc:
        return {"installed": True, "state": "error", "error": str(exc)[:200]}
    if proc.returncode != 0:
        return {"installed": True, "state": "not_running",
                "error": (proc.stderr or "").strip()[:200]}
    try:
        data = json.loads(proc.stdout or "{}")
    except Exception:
        data = {}
    self_node = data.get("Self") or {}
    ips = self_node.get("TailscaleIPs") or []
    ipv4 = [ip for ip in ips if ":" not in ip]
    return {
        "installed": True,
        "state": data.get("BackendState") or "unknown",
        "auth_url": data.get("AuthURL") or "",
        "hostname": self_node.get("HostName") or "",
        "dns_name": (self_node.get("DNSName") or "").rstrip("."),
        "ip": (ipv4 or ips or [""])[0],
        "online": bool(self_node.get("Online")),
    }


def _sshd_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 22), timeout=2):
            return True
    except OSError:
        return False


def access_report() -> dict:
    tailscale = _tailscale_status()
    password = ""
    try:
        password = (ACCESS_STATE / "ssh-password").read_text().strip()
    except OSError:
        pass
    target = tailscale.get("dns_name") or tailscale.get("ip") or ""
    keys = ""
    try:
        keys = (Path("/home") / SSH_USER / ".ssh" / "authorized_keys").read_text()
    except OSError:
        pass
    return {
        "tailscale": tailscale,
        "sshd": _sshd_up(),
        "ssh": {
            "user": SSH_USER,
            "port": 22,
            "password": password,
            "target": target,
            "command": f"ssh {SSH_USER}@{target}" if target else "",
            "key_logins": len([line for line in keys.splitlines() if line.strip()]),
        },
    }


class TermReq(BaseModel):
    cmd: str
    timeout: int = TERM_TIMEOUT


class BrowserSessionReq(BaseModel):
    session_id: str


class BrowserOpenReq(BaseModel):
    url: str = ""
    session_id: Optional[str] = None


class BrowserActionReq(BrowserSessionReq):
    kind: str
    fx: Optional[float] = None
    fy: Optional[float] = None
    dy: Optional[float] = None
    text: Optional[str] = None
    submit: bool = False
    url: Optional[str] = None


def build_desktop_router(pool, api_key: str = "") -> APIRouter:
    state = DesktopState(pool)
    router = APIRouter()

    @router.get("/pc/state")
    async def pc_state():
        payload = state.snapshot()
        payload["geo"] = await _geo()
        payload["active_sessions"] = len(pool.sessions)
        return payload

    @router.get("/pc/access")
    def pc_access():
        return access_report()

    @router.get("/pc/agent")
    async def pc_agent_kit():
        # Connection kit for the owner's coding agent: the URL it should call and
        # the key it must send. Same trust level as the desktop itself.
        ts = _tailscale_status()
        dns = ts.get("dns_name") or ""
        ip = ts.get("ip") or ""
        running = ts.get("state") == "Running" and bool(dns or ip)
        return {
            "workspace": str(WORKSPACE),
            "api_key": api_key,
            "api_key_required": bool(api_key),
            "tailnet": {
                "running": running,
                "base_url": f"http://{dns or ip}:3000" if running else "",
                "dns_name": dns,
                "ip": ip,
            },
            "mcp_url": "/mcp",
            "tools": [
                "machine_info", "machine_run", "machine_read_file", "machine_write_file",
                "machine_list_dir", "browser_open", "browser_snapshot", "browser_screenshot",
                "browser_click", "browser_type", "browser_eval", "browser_close",
            ],
            "rest": {
                "machine_info": "GET /machine/info",
                "machine_run": "POST /machine/exec {cmd, timeout?}",
                "machine_read_file": "POST /machine/read {path, max_bytes?}",
                "machine_write_file": "POST /machine/write {path, content, append?}",
                "machine_list_dir": "POST /machine/list {path?}",
            },
        }

    @router.post("/pc/term/run")
    async def term_run(req: TermReq):
        return await state.shell.run(req.cmd, max(1, min(req.timeout, 300)))

    @router.post("/pc/term/reset")
    async def term_reset():
        await state.shell._stop()
        return {"ok": True, "cwd": str(WORKSPACE)}

    @router.post("/pc/browser/open")
    async def browser_open(req: BrowserOpenReq):
        result = await pool.open(normalize_url(req.url), req.session_id, timeout_ms=45000)
        state.browser_session = result["session_id"]
        return await _page_info(pool, result["session_id"])

    @router.post("/pc/browser/action")
    async def browser_action(req: BrowserActionReq):
        pool.page_of(req.session_id)  # fail fast when the session already expired
        try:
            if req.kind == "click":
                await pool.mouse_click_at(
                    req.session_id,
                    _clamp(req.fx) * VIEWPORT["width"],
                    _clamp(req.fy) * VIEWPORT["height"],
                )
            elif req.kind == "scroll":
                await pool.mouse_scroll(req.session_id, req.dy if req.dy is not None else 600)
            elif req.kind == "key":
                await pool.type_keys(req.session_id, req.text or "", req.submit)
            elif req.kind == "press":
                await pool.press(req.session_id, req.text or "Enter")
            elif req.kind == "back":
                await pool.nav_back(req.session_id)
            elif req.kind == "reload":
                await pool.reload(req.session_id)
            elif req.kind == "goto":
                await pool.open(normalize_url(req.url or ""), req.session_id, timeout_ms=45000)
            else:
                raise HTTPException(400, f"thao tac khong ho tro: {req.kind}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"thao tac that bai: {exc}")
        return await _page_info(pool, req.session_id)

    @router.get("/pc/browser/shot")
    async def browser_shot(session_id: str):
        png = await pool.screenshot(session_id)
        return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @router.post("/pc/browser/close")
    async def browser_close(req: BrowserSessionReq):
        await pool.close(req.session_id)
        if state.browser_session == req.session_id:
            state.browser_session = None
        return {"ok": True}

    return router
