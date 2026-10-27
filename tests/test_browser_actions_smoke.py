import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from fastmcp.exceptions import ToolError

from app.browser_server import BrowserWorker, create_server, read_origins

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("value", [None, "[]", '"localhost"', '["https://example.com"]',
                                  '["http://localhost/path"]', '["http://localhost:0"]',
                                  '["file:///tmp"]'])
def test_browser_actions_server_requires_explicit_loopback_origins(value):
    with pytest.raises(ValueError, match="origin"):
        read_origins(value)


def test_browser_actions_server_only_exposes_controlled_tools():
    async def scenario():
        server = create_server(["http://127.0.0.1:3000"])
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert set(tools) == {
            "browser_navigate", "browser_snapshot", "browser_console_messages",
            "browser_network_requests", "browser_close", "browser_click",
            "browser_type", "browser_select_option",
        }
        schema = tools["browser_type"].parameters
        assert schema["properties"]["submit"]["default"] is False
        assert set(schema["required"]) == {"snapshot_id", "ref", "text"}
        assert set(tools["browser_click"].parameters["required"]) == {"snapshot_id", "ref"}

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="POSIX descendant cleanup")
@pytest.mark.parametrize("ending", ["cancel", "timeout", "eof"])
def test_browser_worker_reaps_descendants_when_closing(tmp_path, monkeypatch, ending):
    try:
        subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("process tree inspection unavailable in this environment")
    marker = tmp_path / "received"
    executable = tmp_path / "node"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, signal, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)'], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "for line in sys.stdin:\n"
        f"    Path({str(marker)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
        + ("    print(json.dumps({'id': json.loads(line)['id'], 'result': {'ready': True}}), flush=True)\n"
           if ending == "eof" else "    time.sleep(120)\n")
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    async def scenario():
        worker = BrowserWorker(["http://127.0.0.1:3000"])
        original_timeout = asyncio.timeout
        if ending == "timeout":
            monkeypatch.setattr(asyncio, "timeout", lambda _seconds: original_timeout(0.2))
        task = asyncio.create_task(worker.call("browser_snapshot", {}))
        if ending == "cancel":
            async with original_timeout(2):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            task.cancel()
        try:
            if ending == "eof":
                assert json.loads(await task) == {"ready": True}
                await worker.close()
            else:
                with pytest.raises(asyncio.CancelledError if ending == "cancel" else TimeoutError):
                    await task
                with pytest.raises(ToolError, match="unavailable"):
                    await worker.call("browser_snapshot", {})
            assert marker.exists()
            for pid in json.loads(marker.read_text()):
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
        finally:
            await worker.close()
            if marker.exists():
                for pid in json.loads(marker.read_text()):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    asyncio.run(scenario())


def test_browser_worker_kills_node_even_when_process_scan_is_unavailable(tmp_path, monkeypatch):
    marker = tmp_path / "received"
    executable = tmp_path / "node"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\nfrom pathlib import Path\n"
        "for line in sys.stdin:\n"
        f"    Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(120)\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    def unavailable(*_args, **_kwargs):
        raise PermissionError("ps unavailable")

    monkeypatch.setattr(subprocess, "run", unavailable)

    async def scenario():
        worker = BrowserWorker(["http://127.0.0.1:3000"])
        task = asyncio.create_task(worker.call("browser_snapshot", {}))
        async with asyncio.timeout(2):
            while not marker.exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(RuntimeError, match="cleanup incomplete"):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
        with pytest.raises(ToolError, match="unavailable"):
            await worker.call("browser_snapshot", {})

    asyncio.run(scenario())


@pytest.mark.skipif(os.getenv("RUN_BROWSER_SMOKE") != "1", reason="opt-in real browser smoke")
def test_real_browser_controlled_actions_smoke():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "browser_actions_smoke.py")],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "B2 smoke: PASS" in result.stdout
