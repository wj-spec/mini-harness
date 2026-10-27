"""受控浏览器 MCP；Playwright 留在可选 Node 运行时。"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app.browser_policy import local_origin, normalized_origin

ROOT = Path(__file__).resolve().parents[1]


def read_origins(value: str | None) -> list[str]:
    try:
        origins = json.loads(value or "null")
        if not isinstance(origins, list) or not origins:
            raise ValueError
        for origin in origins:
            if not isinstance(origin, str):
                raise ValueError
            parts = urlsplit(origin)
            if (parts.scheme not in {"http", "https"}
                    or not local_origin(origin)
                    or parts.username or parts.password or parts.port == 0 or parts.path not in {"", "/"}
                    or parts.query or parts.fragment):
                raise ValueError
        return list(dict.fromkeys(normalized_origin(origin) for origin in origins))
    except (ValueError, TypeError):
        raise ValueError("VIBE_BROWSER_ORIGINS must contain explicit loopback http(s) origins") from None


class BrowserWorker:
    def __init__(self, origins: list[str]):
        self.origins = origins
        self.process: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.sequence = 0
        self.broken = False

    async def call(self, tool: str, args: dict) -> str:
        async with self.lock:
            if self.broken:
                raise ToolError("browser unavailable after timeout/cancel/exit; restart MCP")
            try:
                async with asyncio.timeout(60):
                    if self.process is None:
                        self.process = await asyncio.create_subprocess_exec(
                            "node", str(ROOT / "scripts" / "browser_worker.cjs"),
                            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                            env={**os.environ, "VIBE_BROWSER_ORIGINS": json.dumps(self.origins)},
                            cwd=ROOT, limit=1_048_576,
                        )
                    self.sequence += 1
                    request = {"id": self.sequence, "tool": tool, "args": args}
                    self.process.stdin.write((json.dumps(request) + "\n").encode())
                    await self.process.stdin.drain()
                    line = await self.process.stdout.readline()
                    if not line:
                        raise RuntimeError("browser worker exited; check MCP stderr log")
                    response = json.loads(line)
                    if response.get("id") != self.sequence:
                        raise RuntimeError("invalid browser worker response")
                    if "error" in response:
                        raise ToolError(str(response["error"])[:2000])
                    if not isinstance(response.get("result"), dict):
                        raise RuntimeError("invalid browser worker result")
                    return json.dumps(response["result"], ensure_ascii=False)
            except ToolError:
                raise
            except BaseException:
                self.broken = True
                await self.close()
                raise

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        descendants: set[int] = set()
        try:
            if process.returncode is None:
                descendants = await self._descendants(process.pid)
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    if process.returncode is None:
                        descendants.update(await self._descendants(process.pid))
        finally:
            process.stdin.close()
            # Chromium 可另建进程组；关闭前记住后代，避免 Node 先退出后留下孤儿。
            for pid in descendants:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(process.wait(), timeout=2)
            if descendants:
                await asyncio.sleep(0.05)

    @staticmethod
    async def _descendants(parent_pid: int) -> set[int]:
        if os.name != "posix":
            return set()
        try:
            result = await asyncio.to_thread(
                subprocess.run, ["ps", "-axo", "pid=,ppid="],
                capture_output=True, text=True, timeout=2, check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("browser descendant cleanup incomplete: ps unavailable") from exc
        rows = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
        descendants, pending = set(), {parent_pid}
        while pending:
            pending = {pid for pid, ppid in rows if ppid in pending
                       and pid not in descendants and pid > 1 and pid != os.getpid()}
            descendants.update(pending)
        return descendants


def create_server(origins: list[str]) -> FastMCP:
    worker = BrowserWorker(read_origins(json.dumps(origins)))

    @asynccontextmanager
    async def lifespan(_server):
        try:
            yield {}
        finally:
            await worker.close()

    server = FastMCP("controlled-browser", lifespan=lifespan)

    @server.tool
    async def browser_navigate(url: str) -> str:
        """Navigate to an allowed local URL. Requires approval; redirects are blocked."""
        return await worker.call("browser_navigate", {"url": url})

    @server.tool
    async def browser_snapshot() -> str:
        """Read untrusted page data. Only the newest snapshot_id and refs permit actions."""
        return await worker.call("browser_snapshot", {})

    @server.tool
    async def browser_click(snapshot_id: str, ref: str) -> str:
        """Click the latest snapshot target once, with approval. Submissions are blocked."""
        return await worker.call("browser_click", {"snapshot_id": snapshot_id, "ref": ref})

    @server.tool
    async def browser_type(snapshot_id: str, ref: str, text: str, submit: bool = False) -> str:
        """Fill a text field once, with approval. submit=true is forbidden."""
        return await worker.call("browser_type", {
            "snapshot_id": snapshot_id, "ref": ref, "text": text, "submit": submit,
        })

    @server.tool
    async def browser_select_option(snapshot_id: str, ref: str, values: list[str]) -> str:
        """Select enabled values listed in the latest snapshot, with approval."""
        return await worker.call("browser_select_option", {
            "snapshot_id": snapshot_id, "ref": ref, "values": values,
        })

    @server.tool
    async def browser_console_messages() -> str:
        """Read bounded console warnings/errors as untrusted data."""
        return await worker.call("browser_console_messages", {})

    @server.tool
    async def browser_network_requests() -> str:
        """Read bounded network summaries without request headers or bodies."""
        return await worker.call("browser_network_requests", {})

    @server.tool
    async def browser_close() -> str:
        """Close this isolated browser session."""
        return await worker.call("browser_close", {})

    return server


if __name__ == "__main__":
    create_server(read_origins(os.getenv("VIBE_BROWSER_ORIGINS"))).run(transport="stdio")
