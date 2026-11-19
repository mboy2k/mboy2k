"""WebSocket relay that dials raw TCP from inside the sandbox.

The Hoplite Preview relay terminates TLS and forwards HTTP and WebSocket to this
app, but rejects `CONNECT` (`400 Invalid request URI`), so an external proxy
client cannot use it directly. This endpoint closes the gap: the client opens one
WebSocket per connection, sends a JSON handshake naming the target, and the bytes
then flow both ways over binary frames.

Used by `tools/hoplite_proxy_client.py`; auth and target policy mirror the
forward proxy in `cloud_browser/proxy.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets as pysecrets

try:
    from cloud_browser.proxy import ProxyConfig, _truthy, resolve_target
except ImportError:  # `python cloud_browser/app.py` puts this directory on sys.path
    from proxy import ProxyConfig, _truthy, resolve_target

log = logging.getLogger("cloud_browser.tunnel")

CHUNK = 64 * 1024
DIAL_TIMEOUT = 15.0
HANDSHAKE_TIMEOUT = 30.0


async def _pump(websocket, reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter) -> None:
    async def to_tcp() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data:
                writer.write(data)
                await writer.drain()
            elif message.get("text") == "close":
                return

    async def to_ws() -> None:
        while True:
            data = await reader.read(CHUNK)
            if not data:
                return
            await websocket.send_bytes(data)

    tasks = [asyncio.create_task(to_tcp()), asyncio.create_task(to_ws())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def handle_tunnel(websocket, api_key: str = "") -> None:
    """Serve one WebSocket tunnel: JSON handshake, then raw byte relay."""
    if api_key:
        key = websocket.headers.get("x-api-key") or websocket.query_params.get("key", "")
        if not key or not pysecrets.compare_digest(key, api_key):
            await websocket.close(code=4401, reason="invalid or missing X-API-Key header")
            return
    await websocket.accept()
    try:
        hello = await asyncio.wait_for(websocket.receive_json(), timeout=HANDSHAKE_TIMEOUT)
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close(code=4400, reason="expected a JSON handshake with dst")
        return

    target = str((hello or {}).get("dst", ""))
    host, sep, port_text = target.rpartition(":")
    host = host.strip("[]")
    if not sep or not host or not port_text.isdigit():
        await websocket.send_json({"error": "dst must be host:port"})
        await websocket.close(code=4400, reason="dst must be host:port")
        return
    port = int(port_text)

    cfg = ProxyConfig()
    address, why = await resolve_target(host, port, cfg)
    if not address:
        await websocket.send_json({"error": why})
        await websocket.close(code=4403, reason=why)
        log.info("tunnel %s:%s refused (%s)", host, port, why)
        return
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port),
                                                timeout=DIAL_TIMEOUT)
    except (OSError, asyncio.TimeoutError) as exc:
        await websocket.send_json({"error": f"connect failed: {exc}"})
        await websocket.close(code=4502, reason="connect failed")
        log.info("tunnel %s:%s -> %s failed (%s)", host, port, address, exc)
        return

    await websocket.send_json({"ok": True, "peer": address})
    log.info("tunnel %s:%s -> %s", host, port, address)
    try:
        await _pump(websocket, reader, writer)
    finally:
        with contextlib.suppress(ConnectionError, OSError):
            writer.close()
            await writer.wait_closed()
