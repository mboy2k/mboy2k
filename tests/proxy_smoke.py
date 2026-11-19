"""Smoke test for the forward proxy: auth, HTTP relay, CONNECT tunnel, target policy.

Run from the repo root:  .venv/bin/python tests/proxy_smoke.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PROXY_HOST"] = "127.0.0.1"
os.environ["PROXY_PORT"] = "0"
os.environ["PROXY_USER"] = "hoplite"
os.environ["PROXY_PASSWORD"] = "smoke-secret"
os.environ["PROXY_ALLOW_PRIVATE"] = "1"

from cloud_browser import proxy  # noqa: E402  (the env above configures the listener)


async def _http_upstream(reader, writer):
    await reader.readuntil(b"\r\n\r\n")
    body = b"hello-from-upstream"
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
    await writer.drain()
    writer.close()


async def _echo_upstream(reader, writer):
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                return
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def _read_response(reader):
    head = await reader.readuntil(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":")[1])
    body = await reader.readexactly(length) if length else b""
    return status, body


async def _probe(port, request):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    try:
        return await asyncio.wait_for(_read_response(reader), timeout=10)
    finally:
        writer.close()


async def main():
    cfg = proxy.ProxyConfig()
    strict = proxy.ProxyConfig()
    strict.allow_private = False
    http_upstream = await asyncio.start_server(_http_upstream, "127.0.0.1", 0)
    echo_upstream = await asyncio.start_server(_echo_upstream, "127.0.0.1", 0)
    server = await proxy.start_server(cfg)
    strict_server = await proxy.start_server(strict)
    port = server.sockets[0].getsockname()[1]
    strict_port = strict_server.sockets[0].getsockname()[1]
    http_port = http_upstream.sockets[0].getsockname()[1]
    echo_port = echo_upstream.sockets[0].getsockname()[1]
    auth = f"Proxy-Authorization: {cfg.basic_header}\r\n".encode()
    get_url = f"http://127.0.0.1:{http_port}/ping".encode()
    try:
        status, body = await _probe(
            port, b"GET " + get_url + b" HTTP/1.1\r\nHost: up\r\n" + auth + b"\r\n")
        assert (status, body) == (200, b"hello-from-upstream"), (status, body)

        status, _ = await _probe(
            port, b"GET " + get_url + b" HTTP/1.1\r\nHost: up\r\n\r\n")
        assert status == 407, status

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT 127.0.0.1:%d HTTP/1.1\r\n" % echo_port + auth + b"\r\n")
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        assert b" 200 " in head.split(b"\r\n")[0], head
        writer.write(b"ping")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(4), timeout=10) == b"ping"
        writer.close()

        status, _ = await _probe(
            port, b"CONNECT 169.254.169.254:80 HTTP/1.1\r\n" + auth + b"\r\n")
        assert status == 403, status

        status, _ = await _probe(
            strict_port, b"GET " + get_url + b" HTTP/1.1\r\nHost: up\r\n" + auth + b"\r\n")
        assert status == 403, status
    finally:
        for closing in (server, strict_server, http_upstream, echo_upstream):
            closing.close()
    print("PROXY_SMOKE_OK")


if __name__ == "__main__":
    asyncio.run(main())
