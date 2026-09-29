"""Hoplite PC: a small web desktop served by the same app as the browser API.

Three apps: a calculator (pure client-side), a stateful shell inside this
sandbox, and a remote view of the Playwright browser pool. It is reachable only
through the thread's Hoplite Preview, which keeps access inside the workspace.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import socket
import subprocess
import time
import uuid
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
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class ShellSession:
    """A single persistent bash so `cd` and exported vars survive between commands."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.buf = b""
        self.lock = asyncio.Lock()

    async def _ensure(self) -> None:
        if self.proc is None or self.proc.returncode is not None:
            self.proc = await asyncio.create_subprocess_exec(
                "/bin/bash", "--noprofile", "--norc", "-s",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(WORKSPACE),
            )
            self.buf = b""

    async def _stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass

    async def _read_until(self, marker: bytes) -> tuple[bytes, bytes]:
        while marker not in self.buf:
            chunk = await self.proc.stdout.read(65536)
            if not chunk:
                raise RuntimeError("shell da thoat")
            self.buf += chunk
        head, rest = self.buf.split(marker, 1)
        while b">>>" not in rest:
            chunk = await self.proc.stdout.read(65536)
            if not chunk:
                raise RuntimeError("shell da thoat")
            rest += chunk
        meta, _, tail = rest.partition(b">>>")
        self.buf = tail
        return head, meta

    async def run(self, cmd: str, timeout: int = TERM_TIMEOUT) -> dict:
        cmd = cmd.strip()
        if not cmd:
            return {"stdout": "", "code": 0, "cwd": str(self.cwd)}
        async with self.lock:
            await self._ensure()
            token = uuid.uuid4().hex[:10]
            marker = f"<<<END:{token}:"
            self.proc.stdin.write(
                f"{cmd}\n__rc=$?\nprintf '\\n{marker}%s:%s>>>\\n' \"$__rc\" \"$PWD\"\n".encode()
            )
            try:
                await self.proc.stdin.drain()
                head, meta = await asyncio.wait_for(self._read_until(marker.encode()), timeout)
            except asyncio.TimeoutError:
                await self._stop()
                raise HTTPException(504, f"lenh chay qua {timeout}s, em da khoi dong lai shell")
            except Exception as exc:
                await self._stop()
                raise HTTPException(500, f"terminal loi: {exc}")
            code_s, _, cwd = meta.decode(errors="replace").partition(":")
            code = int(code_s) if code_s.strip().lstrip("-").isdigit() else -1
            if cwd.strip():
                self.cwd = Path(cwd.strip())
            return {
                "stdout": _ANSI.sub("", head.decode(errors="replace")).lstrip("\n"),
                "code": code,
                "cwd": str(self.cwd),
            }


class DesktopState:
    def __init__(self, pool) -> None:
        self.pool = pool
        self.shell = ShellSession(WORKSPACE)
        self.browser_session: Optional[str] = None
        self.started = time.time()

    @staticmethod
    def _meminfo() -> tuple[int, int]:
        try:
            fields = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, value = line.partition(":")
                fields[key] = int(value.split()[0])
            total = fields.get("MemTotal", 0) // 1024
            return total, max(total - fields.get("MemAvailable", 0) // 1024, 0)
        except Exception:
            return 0, 0

    def snapshot(self) -> dict:
        total_mb, used_mb = self._meminfo()
        try:
            uptime = float(Path("/proc/uptime").read_text().split()[0])
        except Exception:
            uptime = time.time() - self.started
        return {
            "host": socket.gethostname(),
            "kernel": platform.platform(),
            "cpu_count": os.cpu_count(),
            "mem_total_mb": total_mb,
            "mem_used_mb": used_mb,
            "workspace": str(WORKSPACE),
            "cwd": str(self.shell.cwd),
            "uptime_s": round(uptime),
            "viewport": VIEWPORT,
            "browser_session": self.browser_session,
        }


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


def build_desktop_router(pool) -> APIRouter:
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
