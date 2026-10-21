import asyncio
import os
import shlex
import sys

import pytest

from app.tools import ToolRegistry
from core.models import ToolCall


async def wait_file(path):
    async with asyncio.timeout(3):
        while not path.exists():
            await asyncio.sleep(0.01)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_cancel_native_command_cleans_child_after_shell_exit(tmp_path):
    async def scenario():
        started = tmp_path / "started"
        late = tmp_path / "late"
        script = (
            "from pathlib import Path; import time; "
            f"Path({str(started)!r}).write_text('ready'); "
            f"time.sleep(0.5); Path({str(late)!r}).write_text('leaked')"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} & exit 0"
        cancel = asyncio.Event()
        operation = asyncio.create_task(ToolRegistry(tmp_path).execute_async(
            ToolCall("bash", {"command": command}), cancel=cancel,
        ))
        await wait_file(started)
        cancel.set()
        ok, output = await asyncio.wait_for(operation, 0.3)
        assert not ok and "cancelled" in output
        await asyncio.sleep(0.6)
        assert not late.exists()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_native_command_timeout_cleans_descendants_and_keeps_output(tmp_path):
    async def scenario():
        late = tmp_path / "late"
        script = (
            "from pathlib import Path; import time; print('before timeout', flush=True); "
            f"time.sleep(0.5); Path({str(late)!r}).write_text('leaked')"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} & exit 0"
        ok, output = await ToolRegistry(tmp_path).execute_async(
            ToolCall("bash", {"command": command, "timeout": 0.1}),
        )
        assert not ok and "ERROR timeout" in output
        assert "before timeout" in output
        await asyncio.sleep(0.6)
        assert not late.exists()

    asyncio.run(scenario())


def test_native_async_command_reports_nonzero_exit_without_losing_output(tmp_path):
    ok, output = asyncio.run(ToolRegistry(tmp_path).execute_async(
        ToolCall("bash", {"command": "printf hello; exit 7"}),
    ))
    assert not ok and "exit=7\nhello" in output
    assert output.execution == {"status": "failed", "exit_code": 7}
