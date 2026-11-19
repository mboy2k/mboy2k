"""Local proxy that sends every connection through a Hoplite sandbox.

The sandbox's Preview link cannot be used as a proxy directly (its relay rejects
CONNECT), so this client sits on your machine instead: it speaks ordinary HTTP
proxy protocol to local apps and carries each connection over WebSocket to the
`/tunnel` endpoint of the Cloud Browser app running in the sandbox.

    pip install websockets
    python tools/hoplite_proxy_client.py \
        --url "https://<preview-host>/?_modal_connect_token=<token>" \
        --api-key <BROWSER_API_KEY> --password <PROXY_PASSWORD>

Then use this entry in BP Proxy Switcher (or any proxy client):

    127.0.0.1:8899:hoplite:<PROXY_PASSWORD>

The Preview link expires after about an hour: copy a fresh one from the thread's
Preview panel and restart the client. `--url` also accepts the plain host.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import secrets
import sys
from urllib.parse import urlsplit, urlunsplit

CHUNK = 64 * 1024
HEAD_TIMEOUT = 30.0

PROXY_AUTHORIZATION = "proxy-authorization"


def build_ws_url(preview_url: str) -> str:
    parts = urlsplit(preview_url if "//" in preview_url else f"//{preview_url}")
    if not parts.netloc:
        raise ValueError("--url must include the preview host")
    scheme = "wss" if parts.scheme in ("https", "wss", "tls") else "ws"
    return urlunsplit((scheme, parts.netloc, "/tunnel", parts.query, ""))


def connect_ws(url: str, headers: dict):
    try:
        import websockets
    except ImportError:
        sys.exit("missing dependency: pip install websockets")
    try:
        return websockets.connect(url, additional_headers=headers, max_size=None,
                                  open_timeout=20)
    except TypeError:  # websockets < 14
        return websockets.connect(url, extra_headers=headers, max_size=None, open_timeout=20)


def _header(head: str, name: str) -> str:
    for line in head.split("\r\n")[1:]:
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == name:
            return value.strip()
    return ""


def _authorized(head: str, cfg) -> bool:
    if not cfg.password:
        return True
    expected = "Basic " + base64.b64encode(
        f"{cfg.user}:{cfg.password}".encode()).decode()
    return secrets.compare_digest(_header(head, PROXY_AUTHORIZATION), expected)


async def _reply(writer: asyncio.StreamWriter, status: int, reason: str,
                 detail: str, extra: str = "") -> None:
    body = detail.encode()
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        f"{extra}\r\n".encode() + body)
    with contextlib.suppress(ConnectionError, OSError):
        await writer.drain()


async def _tunnel(cfg, dst: str, client_reader: asyncio.StreamReader,
                  client_writer: asyncio.StreamWriter, prelude: bytes = b"",
                  on_ready=None) -> None:
    headers = {"X-API-Key": cfg.api_key} if cfg.api_key else {}
    async with connect_ws(cfg.ws_url, headers) as ws:
        await ws.send(json.dumps({"dst": dst}))
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
        if hello.get("error"):
            raise RuntimeError(hello["error"])
        if on_ready is not None:
            await on_ready()

        async def to_ws() -> None:
            if prelude:
                await ws.send(prelude)
            while True:
                data = await client_reader.read(CHUNK)
                if not data:
                    return
                await ws.send(data)

        async def to_client() -> None:
            async for message in ws:
                if isinstance(message, bytes):
                    client_writer.write(message)
                    await client_writer.drain()

        tasks = [asyncio.create_task(to_ws()), asyncio.create_task(to_client())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def _origin_head(head: str, target: str) -> bytes:
    """Rewrite an absolute-form request into origin-form for the tunnelled socket."""
    parts = urlsplit(target)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    lines = head.split("\r\n")
    method = lines[0].split()[0]
    rebuilt = [f"{method} {path} HTTP/1.1"]
    host = False
    for line in lines[1:]:
        if not line:
            break
        key = line.split(":", 1)[0].strip().lower()
        if key in ("proxy-authorization", "proxy-connection", "connection", "keep-alive"):
            continue
        host = host or key == "host"
        rebuilt.append(line)
    if not host:
        rebuilt.append(f"Host: {parts.netloc}")
    rebuilt.append("Connection: close")
    return ("\r\n".join(rebuilt) + "\r\n\r\n").encode("latin-1")


async def handle_client(cfg, reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter) -> None:
    try:
        try:
            head = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),
                                           timeout=HEAD_TIMEOUT)).decode("latin-1")
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
            return
        request = head.split("\r\n", 1)[0].split()
        if len(request) != 3:
            await _reply(writer, 400, "Bad Request", "malformed request")
            return
        method, target = request[0].upper(), request[1]
        if not _authorized(head, cfg):
            await _reply(writer, 407, "Proxy Authentication Required",
                         "proxy credentials required",
                         extra='Proxy-Authenticate: Basic realm="hoplite-sandbox"\r\n')
            return
        if method == "CONNECT":
            host, sep, port_text = target.rpartition(":")
            if not sep or not host or not port_text.isdigit():
                await _reply(writer, 400, "Bad Request", "CONNECT expects host:port")
                return
            prelude, dst = b"", target
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                await _reply(writer, 400, "Bad Request",
                             "point your client at this proxy; only absolute http:// "
                             "URLs and CONNECT are supported")
                return
            prelude = _origin_head(head, target)
            dst = f"{parts.hostname}:{parts.port or 80}"
        ready = False

        async def announce() -> None:
            nonlocal ready
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            ready = True

        try:
            await _tunnel(cfg, dst, reader, writer, prelude,
                          announce if method == "CONNECT" else None)
        except Exception as exc:
            if not ready:
                await _reply(writer, 502, "Bad Gateway", f"tunnel failed: {exc}")
            return
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(ConnectionError, OSError):
            writer.close()
            await writer.wait_closed()


class ClientConfig:
    def __init__(self, args):
        self.ws_url = build_ws_url(args.url)
        self.api_key = args.api_key or os.environ.get("HOPLITE_API_KEY", "")
        self.user = args.user
        self.password = args.password or os.environ.get("HOPLITE_PROXY_PASSWORD", "")
        host, _, port = args.listen.rpartition(":")
        self.host = host or "127.0.0.1"
        self.port = int(port)


async def serve(cfg) -> None:
    server = await asyncio.start_server(
        lambda r, w: handle_client(cfg, r, w), cfg.host, cfg.port)
    print(f"Proxy nghe tại {cfg.host}:{cfg.port}  ->  {cfg.ws_url.split('?')[0]}")
    print(f"Cắm vào BP Proxy Switcher: {cfg.host}:{cfg.port}:{cfg.user}:{cfg.password or '(không mật khẩu)'}")
    print("Ctrl+C để dừng.")
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True,
                        help="link Preview (kèm _modal_connect_token) hoặc host")
    parser.add_argument("--listen", default="127.0.0.1:8899", help="địa chỉ nghe cục bộ")
    parser.add_argument("--user", default="hoplite", help="user cho client nối vào proxy")
    parser.add_argument("--password", default="", help="mật khẩu proxy (rỗng = không hỏi)")
    parser.add_argument("--api-key", default="", help="BROWSER_API_KEY của sandbox")
    cfg = ClientConfig(parser.parse_args())
    try:
        asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
