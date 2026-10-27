import asyncio
import json
import os
import shlex
import signal
import sys

import pytest

from app.sandbox import CommandCancelled, run_command_async
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


def _command_with_child(tmp_path, *, devnull=False):
    child_file, leader_file = tmp_path / "child.json", tmp_path / "leader"
    redirect = ("fd=os.open(os.devnull,os.O_WRONLY); os.dup2(fd,1); os.dup2(fd,2); os.close(fd); "
                if devnull else "")
    script = (
        "import json,os,time; from pathlib import Path; "
        + redirect +
        "print('child partial',flush=True); "
        f"Path({str(child_file)!r}).write_text(json.dumps({{'pid':os.getpid(),'group':os.getpgrp()}})); "
        "time.sleep(30)"
    )
    command = (f"printf 'shell partial\\n'; echo $$ > {shlex.quote(str(leader_file))}; "
               f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} & wait")
    return command, child_file, leader_file


async def _child_ready(path):
    async with asyncio.timeout(3):
        while True:
            try:
                return json.loads(path.read_text())
            except (OSError, ValueError):
                await asyncio.sleep(0.005)


def _miss_first_group_signal(monkeypatch, group):
    real_killpg = os.killpg
    first = asyncio.Event()

    def signal_group(target, sig):
        if target == group and sig == signal.SIGKILL and not first.is_set():
            # 模拟已复现的 fork 竞态：首发组信号只杀掉 leader，遗漏新 child。
            os.kill(target, sig)
            first.set()
            return
        real_killpg(target, sig)

    monkeypatch.setattr(os, "killpg", signal_group)
    return real_killpg, first


async def _wait_process_gone(pid):
    async with asyncio.timeout(0.3):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.005)


async def _cleanup_command(operation, leader_file, real_killpg):
    try:
        group = int(leader_file.read_text())
        real_killpg(group, signal.SIGKILL)
    except (OSError, ValueError):
        pass
    done, _ = await asyncio.wait({operation}, timeout=2)
    if operation not in done:
        operation.cancel()
    await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("devnull", [False, True], ids=["inherited-pipe", "closed-pipe"])
def test_cancel_reaps_group_member_missed_by_first_signal(tmp_path, monkeypatch, devnull):
    async def scenario():
        command, child_file, leader_file = _command_with_child(tmp_path, devnull=devnull)
        cancel = asyncio.Event()
        operation = asyncio.create_task(ToolRegistry(tmp_path).execute_async(
            ToolCall("bash", {"command": command}), cancel=cancel,
        ))
        real_killpg = os.killpg
        try:
            child = await _child_ready(child_file)
            real_killpg, _ = _miss_first_group_signal(monkeypatch, child["group"])
            cancel.set()
            done, _ = await asyncio.wait({operation}, timeout=0.4)
            assert operation in done, "cancel must finish after a group signal misses a child"
            ok, output = operation.result()
            assert not ok and output.execution["status"] == "cancelled"
            assert "shell partial" in output
            if not devnull:
                assert "child partial" in output
            await _wait_process_gone(child["pid"])
            with pytest.raises(ProcessLookupError):
                os.killpg(child["group"], 0)
        finally:
            await _cleanup_command(operation, leader_file, real_killpg)

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_repeated_task_cancel_preserves_output_and_finishes_group_cleanup(tmp_path, monkeypatch):
    async def scenario():
        command, child_file, leader_file = _command_with_child(tmp_path)
        operation = asyncio.create_task(run_command_async(command, tmp_path))
        real_killpg = os.killpg
        try:
            child = await _child_ready(child_file)
            real_killpg, first_signal = _miss_first_group_signal(monkeypatch, child["group"])
            operation.cancel()
            await asyncio.wait_for(first_signal.wait(), 0.4)
            operation.cancel()
            done, _ = await asyncio.wait({operation}, timeout=0.4)
            assert operation in done, "a repeated cancel must not interrupt process cleanup"
            with pytest.raises(CommandCancelled) as cancelled:
                operation.result()
            assert "shell partial" in cancelled.value.output and "child partial" in cancelled.value.output
            await _wait_process_gone(child["pid"])
            with pytest.raises(ProcessLookupError):
                os.killpg(child["group"], 0)
        finally:
            await _cleanup_command(operation, leader_file, real_killpg)

    asyncio.run(scenario())
