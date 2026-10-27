#!/usr/bin/env python3
"""B1 browser observation smoke test using the real Playwright MCP server.

Runs a local fixture HTTP server, starts Playwright MCP through the project's
McpManager, navigates to the fixture, and reads snapshot/console/network.
This is an opt-in manual/integration check; it requires Node + Playwright MCP
and a Chromium install. It is intentionally not part of the default pytest run.

Usage:
    PYTHONPATH=. .venv/bin/python3 scripts/browser_smoke.py
"""
from __future__ import annotations

import asyncio
import contextlib
import http.server
import json
import os
import socketserver
import threading
from pathlib import Path

from app.mcp import McpManager
from app.policy import ApprovalDecision, ApprovalGate
from app.tools import ToolRegistry
from core.models import ToolCall

ROOT = Path(__file__).resolve().parents[1]
PAGE = b"""<!doctype html><html><head><title>Browser B1 Fixture</title></head>
<body><h1>B1 fixture</h1>
<button id="save">Save</button>
<script>console.error('fixture console error');</script>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/fail":
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"fixture failure"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def fixture_server():
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            thread.join(timeout=2)


async def main() -> int:
    version = "0.0.83"
    with fixture_server() as base_url:
        config = {
            "browser": {
                "command": "npx",
                "args": [
                    "--yes", f"@playwright/mcp@{version}",
                    "--headless", "--no-sandbox", "--browser", "chromium",
                    "--image-responses", "omit", "--snapshot-mode", "full",
                    "--user-data-dir", ".vibe/browser/profile",
                    "--output-dir", ".vibe/browser/artifacts",
                ],
                "env": {
                    "NPM_CONFIG_CACHE": ".vibe/npm-cache",
                    "PLAYWRIGHT_BROWSERS_PATH": ".vibe/ms-playwright",
                    "PWTEST_SOCKETS_DIR": "/tmp/vibe-browser-sockets",
                    "HOME": ".vibe/browser/home",
                    "XDG_CACHE_HOME": ".vibe/browser/cache",
                    "TMPDIR": ".vibe/browser/tmp",
                },
                "cwd": ".",
                "timeout": 90,
                "risk": "read",
                "allowed_tools": [
                    "browser_navigate", "browser_snapshot",
                    "browser_console_messages", "browser_network_requests",
                ],
                "tool_risks": {"browser_navigate": "write"},
                "allowed_origins": [base_url],
            }
        }
        async def approve(call, preview):
            assert call.name == "mcp_start"
            assert "allowed tools" in preview
            return ApprovalDecision(True, "once")

        manager = McpManager.from_config(config, ROOT)
        try:
            registry = ToolRegistry(ROOT)
            loaded = await manager.register_into(registry, ApprovalGate(approve))
            print("loaded:", loaded)
            if manager.errors:
                print("server errors:", manager.errors)
            if len(loaded) != 4:
                print("ERROR: expected 4 B1 tools")
                return 1
            exposed = {remote: name for name, (_, remote) in manager.tools.items()}
            ok, nav = await registry.execute_async(ToolCall(exposed["browser_navigate"], {"url": base_url + "/"}))
            print(f"--- browser_navigate ok={ok} ---")
            print(nav[:2000])
            if not ok:
                return 1
            for remote in ("browser_snapshot", "browser_console_messages", "browser_network_requests"):
                ok, output = await registry.execute_async(ToolCall(exposed[remote], {}))
                print(f"--- {remote} ok={ok} ---")
                print(output[:1200])
                if not ok:
                    return 1
            print("B1 smoke: PASS")
            return 0
        finally:
            await manager.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
