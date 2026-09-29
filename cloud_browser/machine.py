"""The machine as a service for coding agents: shell, files and machine info.

One shared shell session is exposed two ways:
- REST at /machine/* (X-API-Key, same key as the browser API)
- MCP tools (machine_*) so MCP-capable agents (Claude Code, Cursor, Codex…)
  can run commands and edit files on this machine natively.
"""

from __future__ import annotations

import asyncio
import os
import platform
import re
import socket
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE = BASE_DIR.parent
TERM_TIMEOUT = int(os.environ.get("PC_TERM_TIMEOUT", "60"))
MAX_READ_BYTES = int(os.environ.get("PC_MAX_READ_BYTES", "400000"))
MAX_LIST_ENTRIES = 500
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


class Machine:
    """Shell + file access on the sandbox, with the basics an agent needs to orient."""

    def __init__(self, root: Path = WORKSPACE) -> None:
        self.root = root
        self.shell = ShellSession(root)
        self.started = time.time()

    async def run(self, cmd: str, timeout: int = TERM_TIMEOUT) -> dict:
        return await self.shell.run(cmd, timeout)

    def info(self) -> dict:
        total_mb, used_mb = 0, 0
        try:
            fields = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, value = line.partition(":")
                fields[key] = int(value.split()[0])
            total_mb = fields.get("MemTotal", 0) // 1024
            used_mb = max(total_mb - fields.get("MemAvailable", 0) // 1024, 0)
        except Exception:
            pass
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
            "workspace": str(self.root),
            "cwd": str(self.shell.cwd),
            "uptime_s": round(uptime),
            "user": os.environ.get("PC_SSH_USER", "root"),
        }

    def _resolve(self, path: str) -> Path:
        target = Path(path).expanduser()
        return target if target.is_absolute() else (self.shell.cwd / target).resolve()

    def read_file(self, path: str, max_bytes: int = MAX_READ_BYTES) -> dict:
        target = self._resolve(path)
        if not target.is_file():
            raise HTTPException(404, f"khong phai file: {target}")
        size = target.stat().st_size
        raw = target.read_bytes()[:max_bytes]
        try:
            content = raw.decode("utf-8")
            binary = False
        except UnicodeDecodeError:
            content = raw.decode("utf-8", "replace")
            binary = True
        return {
            "path": str(target),
            "size": size,
            "truncated": size > len(raw),
            "binary": binary,
            "content": content,
        }

    def write_file(self, path: str, content: str, append: bool = False) -> dict:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a" if append else "w", encoding="utf-8") as handle:
            handle.write(content)
        return {"path": str(target), "bytes": len(content.encode()), "append": append}

    def list_dir(self, path: str = ".") -> dict:
        target = self._resolve(path)
        if not target.is_dir():
            raise HTTPException(404, f"khong phai thu muc: {target}")
        entries = []
        for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))[:MAX_LIST_ENTRIES]:
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": size,
            })
        return {"path": str(target), "count": len(entries), "entries": entries}


_MACHINE: Optional[Machine] = None


def get_machine() -> Machine:
    """One machine per process: the desktop UI and every agent share the same shell."""
    global _MACHINE
    if _MACHINE is None:
        _MACHINE = Machine()
    return _MACHINE


class ExecReq(BaseModel):
    cmd: str
    timeout: int = TERM_TIMEOUT


class ReadReq(BaseModel):
    path: str
    max_bytes: int = MAX_READ_BYTES


class WriteReq(BaseModel):
    path: str
    content: str
    append: bool = False


class ListReq(BaseModel):
    path: str = "."


def build_machine_router(machine: Machine) -> APIRouter:
    router = APIRouter(tags=["machine"])

    @router.get("/machine/info")
    def machine_info():
        return machine.info()

    @router.post("/machine/exec")
    async def machine_exec(req: ExecReq):
        return await machine.run(req.cmd, max(1, min(req.timeout, 600)))

    @router.post("/machine/read")
    def machine_read(req: ReadReq):
        return machine.read_file(req.path, max(1, min(req.max_bytes, 4_000_000)))

    @router.post("/machine/write")
    def machine_write(req: WriteReq):
        return machine.write_file(req.path, req.content, req.append)

    @router.post("/machine/list")
    def machine_list(req: ListReq):
        return machine.list_dir(req.path)

    return router


def add_machine_tools(mcp, machine: Machine) -> None:
    """Register the machine on an existing FastMCP server."""

    @mcp.tool()
    def machine_info() -> dict:
        """Machine specs: host, kernel, CPU, RAM, workspace and current directory."""
        return machine.info()

    @mcp.tool()
    async def machine_run(cmd: str, timeout: int = TERM_TIMEOUT) -> dict:
        """Run a shell command on the machine; cd and exports persist between calls."""
        return await machine.run(cmd, max(1, min(timeout, 600)))

    @mcp.tool()
    def machine_read_file(path: str, max_bytes: int = MAX_READ_BYTES) -> dict:
        """Read a text file (relative paths resolve against the machine's current dir)."""
        return machine.read_file(path, max(1, min(max_bytes, 4_000_000)))

    @mcp.tool()
    def machine_write_file(path: str, content: str, append: bool = False) -> dict:
        """Write or append a text file, creating parent directories as needed."""
        return machine.write_file(path, content, append)

    @mcp.tool()
    def machine_list_dir(path: str = ".") -> dict:
        """List a directory with the type and size of each entry."""
        return machine.list_dir(path)
