import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import sandbox
from app.sandbox_proxy import AllowlistProxy, host_allowed
from app.tools import ToolRegistry


def test_host_allowed_matches_exact_and_wildcard():
    assert host_allowed("registry.npmjs.org", ("registry.npmjs.org",))
    assert host_allowed("cdn.npmjs.org", ("*.npmjs.org",))
    assert host_allowed("npmjs.org", ("*.npmjs.org",))
    assert not host_allowed("evil.com", ("*.npmjs.org",))
    assert not host_allowed("evilnpmjs.org", ("*.npmjs.org",))


class _Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "5")
        self.end_headers()
        self.wfile.write(b"hello")


@pytest.fixture()
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _proxy(allowlist):
    return AllowlistProxy(allowlist).start()


def _proxy_request(proxy_port, url):
    client = socket.create_connection(("127.0.0.1", proxy_port), timeout=10)
    client.sendall(
        f"GET {url} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
    data = b""
    while chunk := client.recv(4096):
        data += chunk
    client.close()
    return data


def test_proxy_forwards_allowed_host(upstream):
    proxy = _proxy(("127.0.0.1",))
    try:
        data = _proxy_request(proxy.port, f"http://127.0.0.1:{upstream}/")
        assert b"200" in data.split(b"\r\n", 1)[0]
        assert b"hello" in data
        assert proxy.denied == []
    finally:
        proxy.close()


def test_proxy_rejects_host_outside_allowlist(upstream):
    proxy = _proxy(("example.com",))
    try:
        data = _proxy_request(proxy.port, f"http://127.0.0.1:{upstream}/")
        assert b"403" in data.split(b"\r\n", 1)[0]
        assert proxy.denied
    finally:
        proxy.close()


def test_proxy_connect_tunnel_allows_and_denies(upstream):
    proxy = _proxy(("127.0.0.1",))
    try:
        client = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
        client.sendall(f"CONNECT 127.0.0.1:{upstream} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        head = client.recv(4096)
        assert b"200" in head
        client.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        assert b"hello" in client.recv(4096)
        client.close()
    finally:
        proxy.close()

    denied = _proxy(("example.com",))
    try:
        client = socket.create_connection(("127.0.0.1", denied.port), timeout=5)
        client.sendall(f"CONNECT 127.0.0.1:{upstream} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert b"403" in client.recv(4096)
        client.close()
    finally:
        denied.close()


def test_proxy_env_points_commands_at_local_proxy(upstream):
    proxy = _proxy(("127.0.0.1",))
    try:
        env = proxy.env()
        assert env["HTTP_PROXY"] == f"http://127.0.0.1:{proxy.port}"
        assert env["HTTPS_PROXY"] == f"http://127.0.0.1:{proxy.port}"
        assert env["NO_PROXY"] == "localhost,127.0.0.1"
    finally:
        proxy.close()


def test_allowlist_profile_only_opens_local_proxy_port(tmp_path):
    profile = sandbox.build_seatbelt_profile(tmp_path, proxy_port=23456)
    assert "(deny network*)" in profile
    assert '(allow network* (remote ip "localhost:23456"))' in profile


def test_allowlist_requires_sandbox(tmp_path):
    with pytest.raises(ValueError, match="requires --sandbox"):
        ToolRegistry(tmp_path, sandbox_network_allow="registry.npmjs.org")


def test_config_reads_allowlist_env(monkeypatch):
    monkeypatch.setenv("VIBE_SANDBOX_NETWORK_ALLOW", "registry.npmjs.org,*.pypi.org")
    from app.config import Config

    assert Config.from_env().sandbox_network_allow == "registry.npmjs.org,*.pypi.org"
