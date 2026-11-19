"""Forward proxy (HTTP + HTTPS CONNECT) that sends traffic out through this sandbox.

The browser API listens on `PORT`; the proxy listens on `PROXY_PORT` and is started
by `cloud_browser/app.py` together with the API:

    curl -x http://hoplite:$PROXY_PASSWORD@127.0.0.1:8888 https://example.com

Basic proxy auth is required unless the listener is bound to a loopback address.
Run it standalone with `python -m cloud_browser.proxy`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import logging
import os
import secrets
import socket
import sys
import threading
from urllib.parse import urlsplit

log = logging.getLogger("cloud_browser.proxy")

CHUNK = 64 * 1024
MAX_HEADER_BYTES = 64 * 1024
HEADER_TIMEOUT = 30.0
CONNECT_TIMEOUT = 15.0

# Link-local metadata services (cloud credential endpoints) are never proxied.
METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})

REQUEST_STRIP = {"connection", "keep-alive", "proxy-authenticate",
                 "proxy-authorization", "proxy-connection", "te", "upgrade"}
RESPONSE_STRIP = {"connection", "keep-alive", "proxy-authenticate", "proxy-connection"}


def _truthy(value: str) -> bool:
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "")


class ProxyConfig:
    def __init__(self) -> None:
        self.host = os.environ.get("PROXY_HOST", "0.0.0.0")
        self.port = int(os.environ.get("PROXY_PORT", "8888"))
        self.user = os.environ.get("PROXY_USER", "hoplite")
        self.password = os.environ.get("PROXY_PASSWORD") or os.environ.get("BROWSER_API_KEY", "")
        self.api_key = os.environ.get("BROWSER_API_KEY", "")
        self.allow_private = _truthy(os.environ.get("PROXY_ALLOW_PRIVATE", ""))
        self.max_conns = int(os.environ.get("PROXY_MAX_CONNS", "256"))
        self.idle_timeout = float(os.environ.get("PROXY_TUNNEL_IDLE_TIMEOUT", "600"))

    @property
    def basic_header(self) -> str:
        raw = f"{self.user}:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()


def _header(headers: list[tuple[str, str]], name: str) -> str:
    for key, value in headers:
        if key.lower() == name:
            return value
    return ""


def _authorized(headers: list[tuple[str, str]], peer_ip: str, cfg: ProxyConfig) -> bool:
    if not cfg.password:
        return _is_loopback(peer_ip)
    for key, value in headers:
        low = key.lower()
        if low == "proxy-authorization" and secrets.compare_digest(value, cfg.basic_header):
            return True
        if low == "x-api-key" and cfg.api_key and secrets.compare_digest(value, cfg.api_key):
            return True
    return False


async def resolve_target(host: str, port: int, cfg: ProxyConfig) -> tuple[str, str]:
    """Return (address, "") for a proxyable target, or ("", reason) when refused.

    Shared with cloud_browser/tunnel.py so the WebSocket relay enforces the same
    "public addresses only" policy as the forward proxy.
    """
    if not host:
        return "", "missing target host"
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return "", f"dns failure: {exc}"
    if not infos:
        return "", "no address for target"
    for info in infos:
        addr = info[4][0].split("%", 1)[0]
        if addr in METADATA_IPS:
            return "", "metadata address blocked"
        ip = ipaddress.ip_address(addr)
        if not cfg.allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local
                                      or ip.is_reserved or ip.is_unspecified
                                      or ip.is_multicast):
            return "", f"{addr} is not a public address"
    return infos[0][4][0], ""


async def _respond(writer: asyncio.StreamWriter, status: int, reason: str,
                   detail: str = "", extra: str = "") -> None:
    body = (detail or reason).encode() + b"\n"
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        f"{extra}\r\n"
    ).encode()
    writer.write(head + body)
    with contextlib.suppress(ConnectionError, OSError):
        await writer.drain()


async def _read_head(reader: asyncio.StreamReader) -> bytes:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=HEADER_TIMEOUT)
    except asyncio.IncompleteReadError as exc:
        return (exc.partial or b"")[:MAX_HEADER_BYTES]
    except (asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError, OSError):
        return b""
    return head[:MAX_HEADER_BYTES]


def _parse_head(head: bytes) -> tuple[str, str, str, list[tuple[str, str]]] | None:
    if not head:
        return None
    lines = head.decode("latin-1").split("\r\n")
    request = lines[0].split()
    if len(request) != 3 or not request[2].startswith("HTTP/"):
        return None
    headers = []
    for line in lines[1:]:
        if not line:
            break
        name, sep, value = line.partition(":")
        if sep:
            headers.append((name.strip(), value.strip()))
    return request[0].upper(), request[1], request[2], headers


async def _copy_exact(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      count: int) -> None:
    remaining = count
    while remaining > 0:
        data = await reader.read(min(CHUNK, remaining))
        if not data:
            return
        writer.write(data)
        await writer.drain()
        remaining -= len(data)


async def _copy_chunked(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        line = await reader.readline()
        if not line:
            return
        writer.write(line)
        size = int(line.split(b";", 1)[0].strip() or b"0", 16)
        if size == 0:
            while True:
                trailer = await reader.readline()
                if not trailer:
                    return
                writer.write(trailer)
                if trailer in (b"\r\n", b"\n"):
                    break
            await writer.drain()
            return
        await _copy_exact(reader, writer, size)
        writer.write(await reader.readexactly(2))
        await writer.drain()


async def _copy_until_eof(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        data = await reader.read(CHUNK)
        if not data:
            return
        writer.write(data)
        await writer.drain()


async def _relay_request_body(reader: asyncio.StreamReader, upstream: asyncio.StreamWriter,
                              headers: list[tuple[str, str]]) -> bool:
    transfer_encoding = _header(headers, "transfer-encoding")
    content_length = _header(headers, "content-length")
    try:
        if transfer_encoding and "chunked" in transfer_encoding.lower():
            await _copy_chunked(reader, upstream)
        elif content_length:
            await _copy_exact(reader, upstream, int(content_length))
    except (asyncio.IncompleteReadError, ValueError, ConnectionError, OSError):
        return False
    return True


async def _relay_response(upstream: asyncio.StreamReader, client: asyncio.StreamWriter
                          ) -> tuple[int, str]:
    head = await _read_head(upstream)
    if not head:
        await _respond(client, 502, "Bad Gateway", detail="empty response from upstream")
        return 502, "empty upstream response"
    lines = head.decode("latin-1").split("\r\n")
    status = lines[0].split(" ", 2)
    code = int(status[1]) if len(status) > 1 and status[1].isdigit() else 0
    reason = status[2] if len(status) > 2 else ""
    headers: list[tuple[str, str]] = []
    kept = [lines[0]]
    for line in lines[1:]:
        if not line:
            break
        name, sep, value = line.partition(":")
        if not sep:
            continue
        headers.append((name.strip(), value.strip()))
        if name.strip().lower() not in RESPONSE_STRIP:
            kept.append(f"{name}: {value.strip()}")
    kept.append("Connection: close")
    client.write(("\r\n".join(kept) + "\r\n\r\n").encode("latin-1"))
    await client.drain()
    transfer_encoding = _header(headers, "transfer-encoding")
    content_length = _header(headers, "content-length")
    with contextlib.suppress(asyncio.IncompleteReadError, ValueError, ConnectionError, OSError):
        if transfer_encoding and "chunked" in transfer_encoding.lower():
            await _copy_chunked(upstream, client)
        elif content_length:
            await _copy_exact(upstream, client, int(content_length))
        else:
            await _copy_until_eof(upstream, client)
    return code, reason


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                idle_timeout: float, propagate_eof: bool) -> None:
    while True:
        try:
            data = await asyncio.wait_for(reader.read(CHUNK), timeout=idle_timeout)
        except asyncio.TimeoutError:
            return
        if not data:
            if propagate_eof:
                with contextlib.suppress(ConnectionError, OSError, RuntimeError):
                    if writer.can_write_eof():
                        writer.write_eof()
                        await writer.drain()
            return
        writer.write(data)
        await writer.drain()


async def _pump_tunnel(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter,
                       upstream_reader: asyncio.StreamReader,
                       upstream_writer: asyncio.StreamWriter, idle_timeout: float) -> None:
    # A client half-close only ends the upload direction, so keep the other
    # direction alive until the server closes or the tunnel goes idle.
    upload = asyncio.create_task(
        _pipe(client_reader, upstream_writer, idle_timeout, propagate_eof=True))
    download = asyncio.create_task(
        _pipe(upstream_reader, client_writer, idle_timeout, propagate_eof=False))
    done, _ = await asyncio.wait({upload, download}, return_when=asyncio.FIRST_COMPLETED)
    if download in done:
        upload.cancel()
    else:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(download), timeout=idle_timeout)
        download.cancel()
    await asyncio.gather(upload, download, return_exceptions=True)


async def _open_upstream(address: str, port: int) -> tuple[asyncio.StreamReader,
                                                           asyncio.StreamWriter]:
    return await asyncio.wait_for(asyncio.open_connection(address, port), timeout=CONNECT_TIMEOUT)


async def _handle_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                          target: str, cfg: ProxyConfig, peer: str) -> None:
    host, sep, port_text = target.rpartition(":")
    host = host.strip("[]")
    if not sep or not port_text.isdigit() or not host:
        await _respond(writer, 400, "Bad Request", detail="CONNECT expects host:port")
        log.info("%s CONNECT %s -> 400", peer, target)
        return
    port = int(port_text)
    address, why = await resolve_target(host, port, cfg)
    if not address:
        await _respond(writer, 403, "Forbidden", detail=why)
        log.info("%s CONNECT %s:%s -> 403 (%s)", peer, host, port, why)
        return
    try:
        upstream_reader, upstream_writer = await _open_upstream(address, port)
    except (OSError, asyncio.TimeoutError) as exc:
        await _respond(writer, 502, "Bad Gateway", detail=f"connect failed: {exc}")
        log.info("%s CONNECT %s:%s -> 502 (%s)", peer, host, port, exc)
        return
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    log.info("%s CONNECT %s:%s -> 200", peer, host, port)
    try:
        await _pump_tunnel(reader, writer, upstream_reader, upstream_writer, cfg.idle_timeout)
    finally:
        with contextlib.suppress(ConnectionError, OSError):
            upstream_writer.close()
            await upstream_writer.wait_closed()


async def _handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                       method: str, target: str, version: str,
                       headers: list[tuple[str, str]], cfg: ProxyConfig, peer: str) -> None:
    parts = urlsplit(target)
    if parts.scheme != "http" or not parts.hostname:
        await _respond(writer, 400, "Bad Request",
                       detail="expected an absolute http:// URL; use CONNECT for https")
        log.info("%s %s %s -> 400", peer, method, target)
        return
    port = parts.port or 80
    address, why = await resolve_target(parts.hostname, port, cfg)
    if not address:
        await _respond(writer, 403, "Forbidden", detail=why)
        log.info("%s %s %s -> 403 (%s)", peer, method, target, why)
        return
    try:
        upstream_reader, upstream_writer = await _open_upstream(address, port)
    except (OSError, asyncio.TimeoutError) as exc:
        await _respond(writer, 502, "Bad Gateway", detail=f"connect failed: {exc}")
        log.info("%s %s %s -> 502 (%s)", peer, method, target, exc)
        return
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        lines = [f"{method} {path} {version}"]
        has_host = False
        for name, value in headers:
            if name.lower() in REQUEST_STRIP:
                continue
            has_host = has_host or name.lower() == "host"
            lines.append(f"{name}: {value}")
        if not has_host:
            host_header = parts.hostname if port == 80 else f"{parts.hostname}:{port}"
            lines.append(f"Host: {host_header}")
        lines.append("Connection: close")
        upstream_writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await upstream_writer.drain()
        if not await _relay_request_body(reader, upstream_writer, headers):
            log.info("%s %s %s -> client aborted request body", peer, method, target)
            return
        code, reason = await _relay_response(upstream_reader, writer)
        log.info("%s %s %s -> %s %s", peer, method, target, code, reason)
    finally:
        with contextlib.suppress(ConnectionError, OSError):
            upstream_writer.close()
            await upstream_writer.wait_closed()


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                  cfg: ProxyConfig, slots: asyncio.Semaphore) -> None:
    peer_info = writer.get_extra_info("peername") or ("?", 0)
    peer = str(peer_info[0])
    try:
        parsed = _parse_head(await _read_head(reader))
        if parsed is None:
            await _respond(writer, 400, "Bad Request", detail="malformed proxy request")
            return
        method, target, version, headers = parsed
        if not _authorized(headers, peer, cfg):
            await _respond(writer, 407, "Proxy Authentication Required",
                           detail="proxy credentials required",
                           extra='Proxy-Authenticate: Basic realm="cloud-browser-proxy"\r\n')
            log.info("%s %s %s -> 407", peer, method, target)
            return
        if method == "CONNECT":
            await _handle_connect(reader, writer, target, cfg, peer)
        else:
            await _handle_http(reader, writer, method, target, version, headers, cfg, peer)
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    except Exception:  # one bad client must not take the proxy down
        log.exception("proxy handler failed for %s", peer)
    finally:
        with contextlib.suppress(ConnectionError, OSError):
            writer.close()
            await writer.wait_closed()
        slots.release()


async def start_server(cfg: ProxyConfig | None = None) -> asyncio.AbstractServer:
    cfg = cfg or ProxyConfig()
    slots = asyncio.Semaphore(cfg.max_conns)

    async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await slots.acquire()
        await _handle(reader, writer, cfg, slots)

    return await asyncio.start_server(on_client, cfg.host, cfg.port)


async def serve(cfg: ProxyConfig | None = None) -> None:
    cfg = cfg or ProxyConfig()
    if not cfg.password and not _is_loopback(cfg.host):
        raise RuntimeError(
            "refusing to listen on a public address without PROXY_PASSWORD or BROWSER_API_KEY")
    server = await start_server(cfg)
    listeners = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    log.info("forward proxy listening on %s (auth: %s%s)", listeners,
             "basic" if cfg.password else "loopback-only",
             "" if not cfg.allow_private else ", private targets allowed")
    async with server:
        await server.serve_forever()


def start_in_thread(cfg: ProxyConfig | None = None) -> None:
    """Start the proxy beside the API; no-op when PROXY_DISABLED is set."""
    if _truthy(os.environ.get("PROXY_DISABLED", "")):
        log.info("PROXY_DISABLED is set; forward proxy not started")
        return

    def runner() -> None:
        try:
            asyncio.run(serve(cfg))
        except Exception:
            log.exception("forward proxy stopped")

    threading.Thread(target=runner, name="forward-proxy", daemon=True).start()


def main() -> None:
    logging.basicConfig(level=os.environ.get("PROXY_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
