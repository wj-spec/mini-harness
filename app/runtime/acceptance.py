"""计划验收映射（15 P0）：已确认 criteria → verification plan → 三态结论。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ACCEPTANCE_SCHEMA_VERSION = 1
TRUSTED_SOURCES = ("project_profile", "plan_approval")
FAILURE_CODES = (
    "no_confirmed_criteria", "acceptance_untrusted", "manual_pending",
    "acceptance_uncovered", "acceptance_failed",
)


class AcceptanceError(ValueError):
    pass


@dataclass
class AcceptanceCriterion:
    id: str
    text: str
    required: bool = True
    confirmed: bool = False
    manual: bool = False
    source: str = "model_proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "required": self.required,
            "confirmed": self.confirmed, "manual": self.manual, "source": self.source,
        }


@dataclass
class VerificationCommand:
    id: str
    covers: list[str]
    command: str
    kind: str = "command"
    expect: dict[str, Any] = field(default_factory=dict)
    source: str = "project_profile"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "covers": list(self.covers), "command": self.command,
            "kind": self.kind, "expect": dict(self.expect), "source": self.source,
        }


@dataclass
class PlanAcceptance:
    task_id: str
    plan_id: str
    plan_version: int
    criteria: list[AcceptanceCriterion]
    verification_plan: list[VerificationCommand]
    trusted_source: str = "project_profile"
    schema_version: int = ACCEPTANCE_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Any) -> "PlanAcceptance":
        if not isinstance(value, dict):
            raise AcceptanceError("acceptance must be an object")
        if value.get("schema_version", ACCEPTANCE_SCHEMA_VERSION) != ACCEPTANCE_SCHEMA_VERSION:
            raise AcceptanceError("unsupported acceptance schema_version")
        task_id = _text(value.get("task_id"), "task_id")
        plan_id = _text(value.get("plan_id"), "plan_id")
        version = value.get("plan_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise AcceptanceError("plan_version must be a positive integer")
        raw_criteria = value.get("acceptance_criteria", [])
        if not isinstance(raw_criteria, list):
            raise AcceptanceError("acceptance_criteria must be a list")
        criteria: list[AcceptanceCriterion] = []
        seen: set[str] = set()
        for item in raw_criteria:
            if not isinstance(item, dict):
                raise AcceptanceError("criterion must be an object")
            cid = _text(item.get("id"), "criterion.id")
            if cid in seen:
                raise AcceptanceError(f"duplicate criterion id: {cid}")
            seen.add(cid)
            criteria.append(AcceptanceCriterion(
                id=cid, text=_text(item.get("text"), "criterion.text"),
                required=bool(item.get("required", True)),
                confirmed=item.get("confirmed") is True,
                manual=bool(item.get("manual", False)),
                source=str(item.get("source", "model_proposed")),
            ))
        raw_plan = value.get("verification_plan", [])
        if not isinstance(raw_plan, list):
            raise AcceptanceError("verification_plan must be a list")
        plan: list[VerificationCommand] = []
        for item in raw_plan:
            if not isinstance(item, dict):
                raise AcceptanceError("verification command must be an object")
            vid = _text(item.get("id"), "verification.id")
            if vid in {entry.id for entry in plan}:
                raise AcceptanceError(f"duplicate verification id: {vid}")
            covers = item.get("covers", [])
            if not isinstance(covers, list) or not covers or not all(
                isinstance(entry, str) and entry for entry in covers
            ):
                raise AcceptanceError(f"verification {vid}: covers must be a nonempty string list")
            command = _text(item.get("command"), "verification.command")
            expect = item.get("expect", {})
            if not isinstance(expect, dict):
                raise AcceptanceError(f"verification {vid}: expect must be an object")
            plan.append(VerificationCommand(
                id=vid, covers=list(covers), command=command,
                kind=str(item.get("kind", "command")), expect=dict(expect),
                source=str(item.get("source", "project_profile")),
            ))
        trusted = str(value.get("trusted_source", "project_profile"))
        if trusted not in TRUSTED_SOURCES:
            raise AcceptanceError(f"untrusted acceptance source: {trusted!r}")
        return cls(
            schema_version=ACCEPTANCE_SCHEMA_VERSION, task_id=task_id, plan_id=plan_id,
            plan_version=version, criteria=criteria, verification_plan=plan,
            trusted_source=trusted,
        )

    def required_confirmed(self) -> list[AcceptanceCriterion]:
        return [item for item in self.criteria if item.required and item.confirmed]

    def status(self, results: dict[str, dict[str, Any]] | None = None) -> tuple[bool | None, list[str]]:
        """results: verification id -> {"ok": bool, ...}. 返回 (True/False/None, codes)。"""
        results = results or {}
        required = self.required_confirmed()
        if not required:
            return None, ["no_confirmed_criteria"]
        if self.trusted_source not in TRUSTED_SOURCES:
            return None, ["acceptance_untrusted"]
        manual = [item for item in required if item.manual]
        covered = {cid for command in self.verification_plan for cid in command.covers}
        uncovered = [item.id for item in required if not item.manual and item.id not in covered]
        if uncovered:
            return False, ["acceptance_uncovered", *uncovered]
        failed = []
        for command in self.verification_plan:
            if not any(cid in {item.id for item in required} for cid in command.covers):
                continue
            if not results.get(command.id, {}).get("ok"):
                failed.append(command.id)
        if failed:
            return False, ["acceptance_failed", *failed]
        if manual:
            return None, ["manual_pending", *[item.id for item in manual]]
        return True, []


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceError(f"{name} must be a nonempty string")
    return value.strip()


def acceptance_path(task_dir: str | Path, version: int) -> Path:
    return Path(task_dir) / f"acceptance.v{int(version)}.json"


def save_acceptance_result(task_dir: str | Path, version: int, result: dict[str, Any]) -> Path:
    path = acceptance_path(task_dir, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "plan_version": int(version), **result}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def load_acceptance_result(task_dir: str | Path, version: int) -> dict[str, Any] | None:
    path = acceptance_path(task_dir, version)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None
