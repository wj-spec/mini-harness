from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from core.models import Message, message_from_dict
from core.state import TaskState


SessionPurpose = Literal["user", "test"]


def _validate_purpose(purpose: SessionPurpose) -> None:
    if purpose not in ("user", "test"):
        raise ValueError("session purpose must be 'user' or 'test'")


class SessionStore:
    """追加式 session 日志：消息、状态、恢复标记。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.events_path = self.root / "events.jsonl"
        self.state_path = self.root / "state.json"
        self.meta_path = self.root / "meta.json"
        self.trace_path = self.root / "trace.jsonl"

    @property
    def id(self) -> str:
        return self.root.name

    @property
    def audit_path(self) -> Path:
        return self.root / "audit.jsonl"

    @property
    def purpose(self) -> SessionPurpose | None:
        """旧元数据默认为用户会话；损坏或未知标记不进入任何范围。"""
        if self.meta_path.is_symlink():
            return None
        try:
            metadata = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "user"
        except (OSError, ValueError):
            return None
        if not isinstance(metadata, dict):
            return None
        purpose = metadata.get("purpose", "user")
        return purpose if purpose in ("user", "test") else None

    @classmethod
    def new(
        cls, workspace: str | Path, model: str, *, purpose: SessionPurpose = "user",
    ) -> "SessionStore":
        _validate_purpose(purpose)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        root = Path(workspace) / ".vibe" / "sessions" / stamp
        root.mkdir(parents=True, exist_ok=True)
        store = cls(root)
        store.meta_path.write_text(
            json.dumps(
                {
                    "id": stamp,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "workspace": str(Path(workspace).resolve()),
                    "model": model,
                    "purpose": purpose,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        store.events_path.touch()
        return store

    @classmethod
    def latest(
        cls, workspace: str | Path, *, purpose: SessionPurpose = "user",
    ) -> "SessionStore | None":
        sessions = cls.list_sessions(workspace, purpose=purpose)
        return cls.open_existing(workspace, sessions[0]["id"], purpose=purpose) if sessions else None

    @classmethod
    def open_existing(
        cls, workspace: str | Path, session_id: str, *, purpose: SessionPurpose = "user",
    ) -> "SessionStore | None":
        _validate_purpose(purpose)
        if (
            not isinstance(session_id, str) or not session_id
            or session_id in {".", ".."}
            or "/" in session_id or "\\" in session_id
            or any(unicodedata.category(char).startswith("C") for char in session_id)
        ):
            raise ValueError("session ID must be a single directory name")
        base = Path(workspace) / ".vibe" / "sessions"
        if not base.resolve().is_relative_to(Path(workspace).resolve()):
            return None
        root = base / session_id
        if root.is_symlink() or not root.is_dir():
            return None
        store = cls(root)
        if store.events_path.is_symlink() or not store.events_path.is_file():
            return None
        if store.purpose != purpose:
            return None
        return store

    @classmethod
    def list_sessions(
        cls, workspace: str | Path, *, purpose: SessionPurpose = "user",
    ) -> list[dict]:
        """只读会话索引；以消息事件排序，不受打开或新增附件影响。"""
        _validate_purpose(purpose)
        base = Path(workspace) / ".vibe" / "sessions"
        if not base.is_dir() or not base.resolve().is_relative_to(Path(workspace).resolve()):
            return []
        sessions: list[dict] = []
        for path in base.iterdir():
            try:
                store = cls.open_existing(workspace, path.name, purpose=purpose)
            except ValueError:
                continue
            if store is None:
                continue
            summary = store._summary()
            if summary is not None:
                sessions.append(summary)
        return sorted(sessions, key=lambda row: (row["updated_at"], row["id"]), reverse=True)

    def _summary(self) -> dict | None:
        count = 0
        title = None
        updated = None
        for event in self._events():
            if event.get("type") != "message":
                continue
            try:
                message = message_from_dict(event.get("message"))
            except ValueError:
                continue
            count += 1
            if message.role == "user" and title is None:
                first_line = message.content.split("\n", 1)[0]
                visible = "".join(
                    char if not unicodedata.category(char).startswith("C") else ascii(char)[1:-1]
                    for char in first_line
                )
                title = visible[:80] + ("…" if len(visible) > 80 else "")
            stamp = event.get("ts")
            if isinstance(stamp, str):
                try:
                    timestamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    updated = timestamp.astimezone(timezone.utc)
                except (ValueError, OverflowError):
                    pass
        if not count:
            return None
        if updated is None:
            updated = datetime.fromtimestamp(self.events_path.stat().st_mtime, timezone.utc)
        return {
            "id": self.id, "message_count": count, "title": title or "（无用户标题）",
            "updated_at": updated.isoformat(timespec="microseconds"),
        }

    def append_message(self, message: Message) -> None:
        self._append(
            {
                "type": "message",
                "ts": datetime.now(timezone.utc).isoformat(),
                "message": asdict(message),
            }
        )

    def append_event(self, event_type: str, **data) -> None:
        self._append(
            {
                "type": event_type,
                "ts": datetime.now(timezone.utc).isoformat(),
                **data,
            }
        )

    def append_trace(self, event: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def cost_summary(self) -> dict:
        if not self.trace_path.exists():
            return {"runs": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "cost_usd": 0.0, "tool_calls": 0,
                    "last_status": None}
        runs: list[dict] = []
        for line in self.trace_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "run_end":
                runs.append(event)
        return {
            "runs": len(runs),
            "prompt_tokens": sum(int(r.get("prompt_tokens", 0)) for r in runs),
            "completion_tokens": sum(int(r.get("completion_tokens", 0)) for r in runs),
            "total_tokens": sum(int(r.get("total_tokens", 0)) for r in runs),
            "cost_usd": sum(float(r.get("cost_usd", 0.0)) for r in runs),
            "tool_calls": sum(int(r.get("tool_calls", 0)) for r in runs),
            "last_status": runs[-1].get("status") if runs else None,
        }

    def latest_run(self) -> dict | None:
        """最新运行的起止标记；未结束的新运行不会继承旧停止状态。"""
        if not self.trace_path.exists():
            return None
        latest = None
        for line in self.trace_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") in {"run_start", "run_end"}:
                latest = event
        return latest

    def _append(self, event: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        with open(self.events_path, "ab", buffering=0) as stream:
            if stream.write(payload) != len(payload):
                raise OSError("incomplete session event write")

    def _events(self) -> Iterator[dict]:
        if not self.events_path.exists():
            return
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event

    def load_messages(self) -> list[Message]:
        messages: list[Message] = []
        for event in self._events():
            if event.get("type") != "message":
                continue
            try:
                message = message_from_dict(event.get("message"))
            except ValueError:
                continue
            messages.append(message)
        return messages

    def save_context(self, messages: list[Message]) -> None:
        """追加工作 history 快照；原始消息仍留在日志中。"""
        snapshot = [asdict(message) for message in messages if message.role != "system"]
        if not snapshot:
            return
        for message in snapshot:
            message_from_dict(message)
        self.append_event(
            "context_checkpoint", version=1,
            source_message_count=len(self.load_messages()), messages=snapshot,
        )

    def load_context(self) -> list[Message]:
        """复放最近有效快照及之后的消息，兼容没有快照的旧会话。"""
        messages: list[Message] = []
        source_count = 0
        for event in self._events():
            if event.get("type") == "message":
                try:
                    message = message_from_dict(event.get("message"))
                except ValueError:
                    continue
                source_count += 1
                messages.append(message)
                continue
            if event.get("type") != "context_checkpoint":
                continue
            count = event.get("source_message_count")
            snapshot = event.get("messages")
            if (
                type(event.get("version")) is not int or event["version"] != 1
                or type(count) is not int or count != source_count
                or not isinstance(snapshot, list) or not snapshot
            ):
                continue
            try:
                restored = [message_from_dict(message) for message in snapshot]
            except ValueError:
                continue
            if any(message.role == "system" for message in restored):
                continue
            messages = restored
        return messages

    def save_state(self, state: TaskState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(state), ensure_ascii=False, indent=2)
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def load_state(self) -> TaskState | None:
        if not self.state_path.exists():
            return None
        try:
            return TaskState(**json.loads(self.state_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return None

    def repair_incomplete_tool_calls(self) -> int:
        """崩溃后 assistant(tool_calls) 可能没有对应 tool 结果；补一条 interrupted。"""
        messages = self.load_messages()
        repaired = 0
        i = 0
        while i < len(messages):
            message = messages[i]
            if message.role != "assistant" or not message.tool_calls:
                i += 1
                continue
            j = i + 1
            present: set[str | None] = set()
            while j < len(messages) and messages[j].role == "tool":
                present.add(messages[j].tool_call_id)
                j += 1
            for call in message.tool_calls:
                if call.id in present:
                    continue
                synthetic = Message(
                    "tool",
                    "ERROR interrupted: tool result unknown after crash",
                    name=call.name,
                    tool_call_id=call.id,
                )
                self.append_message(synthetic)
                self.append_event("recovered_tool", tool=call.name, tool_call_id=call.id)
                repaired += 1
            i = j
        return repaired
