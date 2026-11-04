"""后台任务 worker：独立进程跑一个 task segment，并通过 Unix socket 暴露 attach。

spec: docs/specs/process-supervision.md（P0b）。worker 进程自身是 supervisor/relay：
它启动一个 ``--stdio`` 子进程（复用现有 serve 协议），把子进程 stdout 事件写入
``worker-events.jsonl`` 并广播给 attach 客户端；客户端请求转发到子进程 stdin。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from app.runtime.approvals import ApprovalError, ApprovalInbox
from app.runtime.goals import GoalStore, continue_decision, goal_prompt
from app.runtime.supervise import (
    WORKER_HEARTBEAT_TTL_SECONDS,
    WorkerState,
    read_worker_state,
    write_worker_state,
)
from app.runtime.tasks import TaskStore
from app.runtime.workspace import (
    WorktreeError,
    WorktreeManager,
    WorkspaceBusy,
    WorkspaceLock,
    acquire_workspace_lock,
)

WORKER_EVENTS_FILE = "worker-events.jsonl"
WORKER_SOCKET_FILE = "ctrl.sock"
WORKER_LOG_FILE = "worker.log"
WORKER_LOCK_FILE = "worker.lock"
WORKER_PID_FILE = "worker.pid"
WORKER_SCHEMA_VERSION = 1
DEFAULT_APPROVAL_TIMEOUT = 30.0


class WorkerError(Exception):
    pass


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _heartbeat_interval() -> float:
    return _env_float("VIBE_WORKER_HEARTBEAT_SECONDS", 10.0)


def _heartbeat_ttl() -> float:
    return _env_float("VIBE_WORKER_HEARTBEAT_TTL", WORKER_HEARTBEAT_TTL_SECONDS)


def socket_path(task_dir: str | Path, task_id: str) -> Path:
    override = os.getenv("VIBE_WORKER_SOCKET_DIR")
    if override:
        base = Path(override)
    else:
        base = Path(task_dir)
    path = base / WORKER_SOCKET_FILE
    # macOS AF_UNIX 路径上限约 104 字节；过长时退回短临时目录。
    if len(os.fsencode(str(path))) > 100:
        base = Path("/tmp") / f"vibe-worker-{os.getuid()}"
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = base / f"{task_id}.sock"
    return path


def _write_json_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()


def _read_task(session_root: Path, task_id: str):
    store = TaskStore(session_root)
    try:
        return store, store.get(task_id)
    except Exception as exc:
        raise WorkerError(f"cannot load task {task_id}: {type(exc).__name__}: {exc}") from exc


def _rotate_log(path: Path) -> None:
    try:
        max_bytes = int(os.getenv("VIBE_WORKER_LOG_MAX_BYTES", str(64 * 1024 * 1024)))
        backups = int(os.getenv("VIBE_WORKER_LOG_BACKUPS", "3"))
    except ValueError:
        return
    if backups < 1 or max_bytes < 1 or not path.is_file():
        return
    try:
        if path.stat().st_size <= max_bytes:
            return
    except OSError:
        return
    for index in range(backups - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        target = path.with_name(f"{path.name}.{index + 1}")
        if source.exists():
            source.replace(target)
    try:
        path.replace(path.with_name(path.name + ".1"))
    except OSError:
        pass


def _acquire_worker_lock(task_dir: Path) -> None:
    lock = task_dir / WORKER_LOCK_FILE
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        existing = read_worker_state(task_dir)
        if existing is not None and _pid_alive(existing.pid):
            raise WorkerError(f"worker already running for {existing.task_id} (pid {existing.pid})")
        # 陈旧锁回收：只有 pid 明确不存在才删
        if existing is None or not _pid_alive(existing.pid):
            lock.unlink(missing_ok=True)
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        else:  # pragma: no cover
            raise WorkerError("worker lock is held")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()}\n")


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _release_worker_files(task_dir: Path) -> None:
    for name in (WORKER_LOCK_FILE, WORKER_PID_FILE):
        try:
            (task_dir / name).unlink(missing_ok=True)
        except OSError:
            pass


class Worker:
    def __init__(self, *, session_root: Path, task_id: str, workspace: Path,
                 child_args: list[str], child_env: dict[str, str] | None = None):
        self.session_root = session_root
        self.task_id = task_id
        self.workspace = workspace
        self.child_args = child_args
        self.child_env = child_env
        self.task_dir = TaskStore(session_root).task_dir(task_id)
        self.state = WorkerState(
            schema_version=WORKER_SCHEMA_VERSION, task_id=task_id, pid=os.getpid(),
            host=socket.gethostname(), status="starting",
            started_at=self._now(), heartbeat_at=self._now(),
        )
        self.clients: set[asyncio.StreamWriter] = set()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.child: asyncio.subprocess.Process | None = None
        self.done = asyncio.Event()
        self.stopping = asyncio.Event()
        self.exit_code = 0
        self.goal_store = GoalStore(session_root)
        self.goal_record = None
        self.inbox = ApprovalInbox(session_root)
        self.controller: asyncio.StreamWriter | None = None
        self.active_run_id: str | None = None
        self.approval_timed_out = False
        self.workspace_lock: WorkspaceLock | None = None
        self.worktree = None

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    async def run(self) -> int:
        store = TaskStore(self.session_root)
        try:
            task = store.get(self.task_id)
        except Exception as exc:
            raise WorkerError(f"cannot load task {self.task_id}: {exc}") from exc
        if task.status in {"completed", "failed", "cancelled"}:
            raise WorkerError(f"task {self.task_id} is terminal ({task.status})")
        if Path(task.workspace).resolve() != Path(self.workspace).resolve():
            raise WorkerError("task workspace mismatch")
        store.recover_stale()
        if task.kind == "goal":
            try:
                self.goal_record = self.goal_store.ensure_for_task(task)
            except Exception:
                self.goal_record = None
        try:
            self.workspace_lock = acquire_workspace_lock(
                self.workspace, task_id=self.task_id, session_id=self.session_root.name,
            )
        except WorkspaceBusy as exc:
            raise WorkerError(f"workspace busy: {exc}") from exc
        self.task_dir.mkdir(parents=True, exist_ok=True)
        if os.getenv("VIBE_WORKTREE", "1").strip().lower() not in {"0", "false", "off", "no"}:
            try:
                self.worktree = WorktreeManager(self.workspace).create(
                    self.task_id, self.session_root.name,
                )
                (self.task_dir / "worktree.json").write_text(
                    json.dumps(self.worktree.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.child_args = [
                    *self.child_args, "--tools-root", str(self.worktree.path),
                ]
                try:
                    store.append_event(
                        self.task_id, "worktree_created",
                        data={"path": str(self.worktree.path), "branch": self.worktree.branch,
                              "base_commit": self.worktree.base_commit},
                    )
                except Exception:
                    pass
            except WorktreeError as exc:
                if os.getenv("VIBE_ALLOW_NON_GIT", "").strip().lower() not in {"1", "true", "yes"}:
                    raise WorkerError(
                        "non-git workspace: background writes require a git worktree; "
                        "set VIBE_ALLOW_NON_GIT=1 only for read-only tasks"
                    ) from exc
                self.worktree = None
                try:
                    store.append_event(self.task_id, "non_git_shared_workspace",
                                       data={"workspace": str(self.workspace)})
                except Exception:
                    pass
        _rotate_log(self.task_dir / WORKER_LOG_FILE)
        _acquire_worker_lock(self.task_dir)
        (self.task_dir / WORKER_PID_FILE).write_text(f"{os.getpid()}\n", encoding="utf-8")
        log_path = self.task_dir / WORKER_LOG_FILE
        events_path = self.task_dir / WORKER_EVENTS_FILE
        events_path.touch(exist_ok=True)
        write_worker_state(self.task_dir, self.state)
        sock_path = socket_path(self.task_dir, self.task_id)
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        sock_path.unlink(missing_ok=True)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stopping.set)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

        log_handle = log_path.open("a", encoding="utf-8")
        server = None
        heartbeat_task = None
        try:
            server = await asyncio.start_unix_server(
                lambda reader, writer: self._client(reader, writer), path=str(sock_path),
            )
            os.chmod(sock_path, 0o600)
            self._started_wall = time.time()
            self.child = await asyncio.create_subprocess_exec(
                *self.child_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log_handle,
                env={**os.environ, **(self.child_env or {})},
            )
            self.state.status = "running"
            self.state.heartbeat_at = self._now()
            write_worker_state(self.task_dir, self.state)
            heartbeat_task = asyncio.create_task(self._heartbeat())
            tasks = [
                asyncio.create_task(self._feed_child()),
                asyncio.create_task(self._read_child(events_path)),
                asyncio.create_task(self._watch_stop()),
            ]
            await self._queue_initial_run()
            waiters = [asyncio.create_task(self.done.wait()), asyncio.create_task(self.stopping.wait())]
            finished, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            for task in waiters:
                if not task.done():
                    task.cancel()
            if self.stopping.is_set() and not self.done.is_set():
                await self._send_shutdown()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._send_shutdown()
            try:
                self.exit_code = await asyncio.wait_for(self.child.wait(), 10)
            except asyncio.TimeoutError:
                self.child.kill()
                self.exit_code = await self.child.wait()
            if self.stopping.is_set() and not self.done.is_set():
                self.state.status = "killed"
            else:
                self.state.status = "exited" if self.exit_code == 0 else "failed"
            self.state.exit_code = self.exit_code
            self.state.heartbeat_at = self._now()
            write_worker_state(self.task_dir, self.state)
            return 0 if self.exit_code == 0 else 1
        except Exception as exc:
            self.state.status = "failed"
            self.state.error = f"{type(exc).__name__}: {exc}"
            self.state.heartbeat_at = self._now()
            try:
                write_worker_state(self.task_dir, self.state)
            except OSError:
                pass
            self._log(log_handle, f"worker failed: {self.state.error}")
            return 1
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            if server is not None:
                server.close()
                await server.wait_closed()
            try:
                sock_path.unlink(missing_ok=True)
            except OSError:
                pass
            if self.child is not None and self.child.returncode is None:
                self.child.kill()
                await self.child.wait()
            try:
                self.inbox.expire(self.task_id, reason="worker_exit")
            except Exception:
                pass
            if self.workspace_lock is not None:
                self.workspace_lock.release()
                self.workspace_lock = None
            _release_worker_files(self.task_dir)
            log_handle.close()

    def _log(self, handle, message: str) -> None:
        handle.write(f"[worker {self._now()}] {message}\n")
        handle.flush()

    async def _queue_initial_run(self) -> None:
        # 子进程 serve 启动后会读 stdin；初始 run 挂上已有 task。
        task = TaskStore(self.session_root).get(self.task_id)
        if self.goal_record is not None:
            text = goal_prompt(self.goal_record, segment=max(1, len(task.segments) + 1))
        else:
            text = task.objective
        await self._queue_run(text)

    async def _queue_run(self, text: str) -> None:
        request_id = f"worker-{uuid.uuid4().hex[:12]}"
        self.active_run_id = request_id
        frame = {
            "v": 1, "type": "run", "id": request_id, "text": text,
            "task": {"kind": "goal", "task_id": self.task_id,
                     "objective": TaskStore(self.session_root).get(self.task_id).objective},
        }
        await self.queue.put(json.dumps(frame, ensure_ascii=False))

    async def _watch_stop(self) -> None:
        path = Path(self.workspace) / ".vibe" / "stop"
        while True:
            await asyncio.sleep(0.2)
            try:
                if path.exists() and path.stat().st_mtime >= getattr(self, "_started_wall", 0):
                    self.stopping.set()
                    await self._cancel_active_run()
                    return
            except OSError:
                return

    async def _heartbeat(self) -> None:
        interval = _heartbeat_interval()
        while True:
            await asyncio.sleep(interval)
            self.state.heartbeat_at = self._now()
            try:
                write_worker_state(self.task_dir, self.state)
            except OSError:
                return

    async def _feed_child(self) -> None:
        assert self.child is not None and self.child.stdin is not None
        while True:
            frame = await self.queue.get()
            self.child.stdin.write((frame.rstrip("\n") + "\n").encode("utf-8"))
            await self.child.stdin.drain()

    async def _read_child(self, events_path: Path) -> None:
        assert self.child is not None and self.child.stdout is not None
        while True:
            line = await self.child.stdout.readline()
            if not line:
                self.done.set()
                return
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            if not text:
                continue
            _write_json_line(events_path, {"raw": text})
            await self._broadcast(text)
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "closed":
                self.done.set()
            elif event.get("type") == "done":
                await self._handle_done(event)
            if event.get("type") == "approval_request":
                await self._record_approval(event)

    async def _handle_done(self, event: dict[str, Any]) -> None:
        if self.approval_timed_out:
            self.approval_timed_out = False
            try:
                store = TaskStore(self.session_root)
                task = store.get(self.task_id)
                if task.status not in {"completed", "failed", "cancelled"}:
                    store.transition(self.task_id, "failed", reason="approval_timeout")
            except Exception:
                pass
            self.done.set()
            return
        if self.goal_record is None:
            self.done.set()
            return
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        status = str(result.get("status") or "")
        stop_reason = str(result.get("stop_reason") or "")
        try:
            task = TaskStore(self.session_root).get(self.task_id)
            self.goal_record = self.goal_store.load() or self.goal_record
            cont, reason = continue_decision(
                self.goal_record, task, status, stop_reason,
                max_segments=int(os.getenv("VIBE_GOAL_MAX_SEGMENTS", "8")),
                max_seconds=float(os.getenv("VIBE_GOAL_SECONDS", str(4 * 60 * 60))),
            )
        except (OSError, ValueError, Exception):
            self.done.set()
            return
        if not cont:
            if reason:
                try:
                    TaskStore(self.session_root).append_event(
                        self.task_id, "goal_worker_stopped", data={"reason": reason},
                    )
                except Exception:
                    pass
            self.done.set()
            return
        segment = max(1, len(task.segments) + 1)
        text = goal_prompt(self.goal_record, segment=segment, stop_reason=stop_reason)
        await self._queue_run(text)

    async def _record_approval(self, event: dict[str, Any]) -> None:
        approval_id = event.get("approval_id")
        if not isinstance(approval_id, str):
            return
        call = event.get("call") if isinstance(event.get("call"), dict) else {}
        timeout = _env_float("VIBE_WORKER_APPROVAL_TIMEOUT", DEFAULT_APPROVAL_TIMEOUT)
        from datetime import datetime, timedelta, timezone
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=timeout)).isoformat()
        try:
            self.inbox.submit(
                self.task_id, approval_id=approval_id,
                tool=str(call.get("name") or "tool"), tier=event.get("tier"),
                decisions=[str(item) for item in event.get("decisions", [])],
                requester="parent", preview=str(event.get("preview") or ""),
                preview_ref=f"worker-events:{approval_id}",
                expires_at=expires_at, session_id=self.session_root.name,
            )
        except ApprovalError:
            return
        asyncio.create_task(self._approval_deadline(approval_id, timeout))

    async def _approval_deadline(self, approval_id: str, timeout: float) -> None:
        await asyncio.sleep(timeout)
        pending = {item["approval_id"] for item in self.inbox.pending(self.task_id)}
        if approval_id not in pending:
            return
        self.inbox.expire(self.task_id, reason="approval_timeout")
        self.approval_timed_out = True
        try:
            TaskStore(self.session_root).append_event(
                self.task_id, "approval_timeout", data={"approval_id": approval_id},
            )
        except Exception:
            pass
        await self._cancel_active_run()

    async def _cancel_active_run(self) -> None:
        if not self.active_run_id:
            return
        await self.queue.put(json.dumps({
            "v": 1, "type": "cancel", "id": f"worker-cancel-{uuid.uuid4().hex[:8]}",
            "target": self.active_run_id,
        }))

    async def _broadcast(self, text: str) -> None:
        frame = (text.rstrip("\n") + "\n").encode("utf-8")
        dead = []
        for writer in self.clients:
            try:
                writer.write(frame)
                await writer.drain()
            except (ConnectionError, OSError):
                dead.append(writer)
        for writer in dead:
            self.clients.discard(writer)
            writer.close()

    async def _send_shutdown(self) -> None:
        if self.child is None or self.child.stdin is None or self.child.returncode is not None:
            return
        try:
            self.child.stdin.write(json.dumps({
                "v": 1, "type": "shutdown", "id": f"worker-close-{uuid.uuid4().hex[:8]}",
            }).encode("utf-8") + b"\n")
            await self.child.stdin.drain()
        except (ConnectionError, OSError):
            pass

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        mode = "observe"
        take_control = False
        try:
            first = await asyncio.wait_for(reader.readline(), 10)
            if not first:
                return
            try:
                handshake = json.loads(first.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                await self._send_writer(writer, {
                    "v": 1, "type": "error", "id": None,
                    "code": "attach_required", "message": "first frame must be attach",
                })
                return
            mode = str(handshake.get("mode") or "observe")
            take_control = bool(handshake.get("take_control"))
            if mode not in {"control", "observe"}:
                await self._send_writer(writer, {
                    "v": 1, "type": "error", "id": None,
                    "code": "attach_invalid", "message": "mode must be control or observe",
                })
                return
            if mode == "control":
                if self.controller is not None:
                    if not take_control:
                        await self._send_writer(writer, {
                            "v": 1, "type": "error", "id": None,
                            "code": "attach_denied",
                            "message": "another controller is attached; use take_control",
                        })
                        return
                    previous = self.controller
                    self.controller = None
                    await self._send_writer(previous, {
                        "v": 1, "type": "error", "id": None,
                        "code": "control_taken", "message": "another client took control",
                    })
                    previous.close()
                self.controller = writer
            self.clients.add(writer)
            await self._send_writer(writer, {"v": 1, "type": "attach_ok", "id": None, "mode": mode})
            events_path = self.task_dir / WORKER_EVENTS_FILE
            if events_path.is_file():
                for line in events_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line).get("raw")
                    except json.JSONDecodeError:
                        continue
                    if isinstance(raw, str):
                        writer.write((raw.rstrip("\n") + "\n").encode("utf-8"))
                await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    frame = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict) or frame.get("v") != 1:
                    continue
                if mode == "observe":
                    await self._send_writer(writer, {
                        "v": 1, "type": "error", "id": frame.get("id"),
                        "code": "observer_readonly",
                        "message": "observe clients cannot send control frames",
                    })
                    continue
                if frame.get("type") == "approval":
                    approval_id = frame.get("approval_id")
                    decision = frame.get("decision")
                    if isinstance(approval_id, str) and isinstance(decision, str):
                        try:
                            self.inbox.resolve(
                                self.task_id, approval_id, decision=decision,
                                scope=decision if decision != "deny" else "deny",
                                decided_by="attach", decider="user",
                            )
                        except ApprovalError as exc:
                            await self._send_writer(writer, {
                                "v": 1, "type": "error", "id": frame.get("id"),
                                "code": "approval_mismatch", "message": str(exc),
                            })
                            continue
                await self.queue.put(text)
        except (ConnectionError, OSError):
            pass
        finally:
            self.clients.discard(writer)
            if self.controller is writer:
                self.controller = None
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _send_writer(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        try:
            writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
        except (ConnectionError, OSError):
            pass


def run_worker(session_root: Path, task_id: str, workspace: Path,
               child_args: list[str], child_env: dict[str, str] | None = None) -> int:
    worker = Worker(
        session_root=session_root, task_id=task_id, workspace=workspace,
        child_args=child_args, child_env=child_env,
    )
    try:
        return asyncio.run(worker.run())
    except WorkerError as exc:
        print(f"worker: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


async def _attach_async(session_root: Path, task_id: str, *, mode: str = "observe", take_control: bool = False) -> int:
    store = TaskStore(session_root)
    task_dir = store.task_dir(task_id)
    path = socket_path(task_dir, task_id)
    if not path.exists():
        print(f"worker socket not found: {path}", file=sys.stderr)
        return 1
    try:
        reader, writer = await asyncio.open_unix_connection(str(path))
    except OSError as exc:
        print(f"cannot attach: {exc}", file=sys.stderr)
        return 1

    try:
        writer.write((json.dumps({
            "v": 1, "type": "attach", "id": f"attach-{uuid.uuid4().hex[:8]}",
            "mode": mode, "take_control": take_control,
        }) + "\n").encode("utf-8"))
        await writer.drain()
        handshake = await asyncio.wait_for(reader.readline(), 5)
    except (OSError, asyncio.TimeoutError) as exc:
        print(f"attach handshake failed: {exc}", file=sys.stderr)
        writer.close()
        return 1
    if not handshake:
        print("attach handshake closed", file=sys.stderr)
        writer.close()
        return 1
    try:
        reply = json.loads(handshake.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        print("attach handshake returned invalid JSON", file=sys.stderr)
        writer.close()
        return 1
    if reply.get("type") == "error":
        print(f"attach denied: {reply.get('message')}", file=sys.stderr)
        writer.close()
        return 2

    async def output() -> None:
        while True:
            line = await reader.readline()
            if not line:
                return
            sys.stdout.write(line.decode("utf-8", errors="replace"))
            sys.stdout.flush()

    async def input_pump() -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                return
            if line.strip():
                writer.write(line.encode("utf-8"))
                await writer.drain()

    try:
        tasks = [asyncio.create_task(output())]
        if mode == "control":
            tasks.append(asyncio.create_task(input_pump()))
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
    return 0


def attach_worker(session_root: Path, task_id: str, *, mode: str = "observe", take_control: bool = False) -> int:
    try:
        return asyncio.run(_attach_async(session_root, task_id, mode=mode, take_control=take_control))
    except KeyboardInterrupt:
        return 130
