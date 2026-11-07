"""A tiny OpenAI-compatible SSE server for local testing. No API key needed."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CHUNKS = ["你好，", "我是 mock 模型。", " 这是流式回复。"]


class Handler(BaseHTTPRequestHandler):
    fail_statuses: list[int] = []
    request_log: list[str] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.request_log.append(self.path)
        if self.fail_statuses:
            status = int(self.fail_statuses.pop(0))
            body = json.dumps({"error": {"message": f"injected {status}"}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return
        self._stream_reply()

    def _stream_reply(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for text in CHUNKS:
            chunk = {"choices": [{"delta": {"content": text}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.05)
        self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args) -> None:
        pass


def start(
    port: int = 0, *, fail_statuses: list[int] | None = None,
) -> tuple[ThreadingHTTPServer, int]:
    """启动 mock 服务器；fail_statuses 为按请求顺序注入的 HTTP 错误码。"""

    class ConfiguredHandler(Handler):
        pass

    ConfiguredHandler.fail_statuses = list(fail_statuses or [])
    ConfiguredHandler.request_log = []
    server = ThreadingHTTPServer(("127.0.0.1", port), ConfiguredHandler)
    server.fail_statuses = ConfiguredHandler.fail_statuses  # type: ignore[attr-defined]
    server.request_log = ConfiguredHandler.request_log  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


if __name__ == "__main__":
    server, port = start()
    print(f"mock OpenAI server: http://127.0.0.1:{port}/v1")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
