"""沙箱网络白名单：本地 HTTP 代理，按域名放行 CONNECT 与绝对 URI 请求。"""

from __future__ import annotations

import http.client
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy")


def host_allowed(host: str, entries: tuple[str, ...]) -> bool:
    """精确匹配或 *.example.com 后缀匹配。"""
    candidate = (host or "").strip().lower().rstrip(".")
    for entry in entries:
        pattern = entry.strip().lower().rstrip(".")
        if not pattern:
            continue
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if candidate == pattern[2:] or candidate.endswith(suffix):
                return True
        elif candidate == pattern:
            return True
    return False


class _ProxyHandler(BaseHTTPRequestHandler):
    server_version = "mini-harness-sandbox-proxy"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - silence default logging
        return

    def _deny(self) -> None:
        self.server.denied.append(self.path[:200])
        self.send_error(403, "blocked by sandbox allowlist")

    def _allowed(self, host: str) -> bool:
        return host_allowed(host, self.server.allowlist)

    def do_CONNECT(self) -> None:  # noqa: N802 - stdlib naming
        host, _, port_text = self.path.partition(":")
        try:
            port = int(port_text or 443)
        except ValueError:
            self.send_error(400, "invalid CONNECT target")
            return
        if not self._allowed(host):
            self._deny()
            return
        try:
            upstream = socket.create_connection((host, port), timeout=15)
        except OSError as exc:
            self.send_error(502, f"upstream connect failed: {type(exc).__name__}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._tunnel(upstream)

    def _tunnel(self, upstream: socket.socket) -> None:
        client = self.connection
        try:
            while True:
                readable, _, _ = select.select([client, upstream], [], [], 30)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is client else client).sendall(data)
        except OSError:
            return
        finally:
            upstream.close()

    def _handle_http(self) -> None:
        parts = urlsplit(self.path)
        if parts.scheme != "http" or not parts.hostname:
            self.send_error(400, "proxy expects an absolute http:// URI")
            return
        if not self._allowed(parts.hostname):
            self._deny()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=15)
        try:
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() not in {"proxy-connection", "proxy-authorization"}}
            connection.request(self.command, parts.path or "/", body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
        except OSError as exc:
            self.send_error(502, f"upstream request failed: {type(exc).__name__}")
            return
        finally:
            connection.close()
        self.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() not in {"transfer-encoding", "connection", "content-length"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    do_GET = _handle_http  # noqa: N815 - stdlib dispatch naming
    do_POST = _handle_http
    do_PUT = _handle_http
    do_DELETE = _handle_http
    do_HEAD = _handle_http


class AllowlistProxy:
    """本地代理；沙箱只放行到该端口的回环流量。"""

    def __init__(self, allowlist: tuple[str, ...]):
        self.allowlist = tuple(entry for entry in (e.strip() for e in allowlist) if entry)
        self.denied: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
        self._server.daemon_threads = True
        self._server.allowlist = self.allowlist
        self._server.denied = self.denied
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> "AllowlistProxy":
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def env(self) -> dict[str, str]:
        url = f"http://127.0.0.1:{self.port}"
        return {key: url for key in PROXY_ENV_KEYS} | {"NO_PROXY": "localhost,127.0.0.1"}
