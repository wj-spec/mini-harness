"""workspace 级 kill switch（spec: docs/specs/workspace-isolation.md / 09）。"""

from __future__ import annotations

import json
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any

from app.runtime.supervise import WorkerState, read_worker_state, write_worker_state

STOP_FILE = "stop"


class KillSwitchError(Exception):
    pass


def stop_file(workspace: str | Path) -> Path:
    return Path(workspace) / ".vibe" / STOP_FILE


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, OSError):
        return False
    return True


def active_workers(
    workspace: str | Path, *, task_id: str | None = None,
) -> list[tuple[str, WorkerState, Path]]:
    workspace = Path(workspace)
    result: list[tuple[str, WorkerState, Path]] = []
    sessions = workspace / ".vibe" / "sessions"
    if not sessions.is_dir():
        return result
    for worker_file in sorted(sessions.glob("*/tasks/*/worker.json")):
        try:
            state = read_worker_state(worker_file.parent)
        except Exception:
            continue
        if state is None:
            continue
        if task_id is not None and state.task_id != task_id:
            continue
        if state.host != socket.gethostname():
            continue
        if not _pid_alive(state.pid):
            continue
        result.append((state.task_id, state, worker_file.parent))
    return result


def stop_workers(
    workspace: str | Path, *, task_id: str | None = None,
    timeout: float = 5.0, grace: float = 3.0,
) -> list[dict[str, Any]]:
    """SIGTERM → grace → SIGKILL；写 .vibe/stop 信号文件；返回处理结果。"""
    workspace = Path(workspace)
    signal_path = stop_file(workspace)
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(json.dumps({
        "requested_at": time.time(), "pid": os.getpid(), "task_id": task_id,
    }), encoding="utf-8")

    def signal_worker(pid: int, signum: int) -> None:
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signum)
            return
        # 只对“自己是进程组组长”的 worker 发组信号，避免误杀测试/父进程组。
        if pgid == pid:
            os.killpg(pgid, signum)
        else:
            os.kill(pid, signum)

    results: list[dict[str, Any]] = []
    for task, state, task_dir in active_workers(workspace, task_id=task_id):
        result = {"task_id": task, "pid": state.pid, "signalled": False, "killed": False}
        try:
            signal_worker(state.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            result["error"] = "worker disappeared before signal"
            results.append(result)
            continue
        result["signalled"] = True
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and _pid_alive(state.pid):
            time.sleep(0.05)
        if _pid_alive(state.pid):
            try:
                signal_worker(state.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            result["killed"] = True
        state.status = "killed"
        state.error = "workspace kill switch"
        try:
            write_worker_state(task_dir, state)
        except Exception:
            pass
        results.append(result)
    return results
