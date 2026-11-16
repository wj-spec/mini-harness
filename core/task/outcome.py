"""可持久化的运行结果事实；展示文案由界面决定。"""
from __future__ import annotations

import hashlib

from .state import TaskState

MAX_FILES = 200
MAX_CHECKS = 100


def _verification(checks: list[dict], mutation_seq: int) -> str:
    if not checks:
        return "not_run"
    if any(not item["ok"] for item in checks):
        return "failed"
    if any(item["mutation_seq"] < mutation_seq for item in checks):
        return "stale"
    return "passed"


def build_outcome(
    state: TaskState, *, start_file_changes: int = 0, start_verifications: int = 0,
    operations: int = 0, tool_errors: int = 0, denied: bool = False,
    audit: dict | None = None,
) -> dict:
    files = list(dict.fromkeys(
        str(item["path"]) for item in state.file_changes[start_file_changes:] if item.get("path")
    ))
    checks = {}
    for item in state.verifications[start_verifications:]:
        key = hashlib.sha256(str(item.get("command", "")).encode()).hexdigest()
        checks[key] = {"key": key, "ok": item.get("ok") is True,
                       "mutation_seq": int(item.get("mutation_seq") or 0)}
    checks = list(checks.values())
    return {
        "schema_version": 1,
        "files": files[:MAX_FILES], "omitted_files": max(0, len(files) - MAX_FILES),
        "checks": checks[-MAX_CHECKS:], "mutation_seq": state.mutation_seq,
        "verification": _verification(checks, state.mutation_seq),
        "operations": operations, "tool_errors": tool_errors, "denied": denied,
        "audit_warning": bool(audit and (audit.get("integrity") in {"suspect", "violation"}
                                        or audit.get("coverage") == "error"
                                        or isinstance(audit.get("llm"), dict) and audit.get("verified") is False)),
        "prior_unknown": len(checks) > MAX_CHECKS,
    }


def merge_outcomes(previous: dict | None, current: dict, *, prior_unknown: bool = False) -> dict:
    if previous is None:
        return {**current, "prior_unknown": current["prior_unknown"] or prior_unknown}
    files = list(dict.fromkeys([*previous["files"], *current["files"]]))
    checks = {item["key"]: item for item in previous["checks"]}
    checks.update({item["key"]: item for item in current["checks"]})
    checks = list(checks.values())
    seq = max(previous["mutation_seq"], current["mutation_seq"])
    return {
        **current, "files": files[:MAX_FILES],
        "omitted_files": previous["omitted_files"] + current["omitted_files"] + max(0, len(files) - MAX_FILES),
        "checks": checks[-MAX_CHECKS:], "mutation_seq": seq,
        "verification": _verification(checks, seq),
        "operations": previous["operations"] + current["operations"],
        "tool_errors": previous["tool_errors"] + current["tool_errors"],
        "denied": previous["denied"] or current["denied"],
        "audit_warning": previous["audit_warning"] or current["audit_warning"],
        "prior_unknown": previous["prior_unknown"] or current["prior_unknown"] or prior_unknown or len(checks) > MAX_CHECKS,
    }


def valid_outcome(value) -> bool:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return False
    if any(type(value.get(key)) is not int or value[key] < 0
           for key in ("omitted_files", "mutation_seq", "operations", "tool_errors")):
        return False
    if any(type(value.get(key)) is not bool for key in ("denied", "audit_warning", "prior_unknown")):
        return False
    if not isinstance(value.get("verification"), str) or value["verification"] not in {"not_run", "passed", "failed", "stale"}:
        return False
    if "plan_verification" in value and (not isinstance(value["plan_verification"], str)
                                         or value["plan_verification"] not in {"not_run", "passed", "failed"}):
        return False
    files, checks = value.get("files"), value.get("checks")
    return (isinstance(files, list) and len(files) <= MAX_FILES
            and all(isinstance(item, str) and 0 < len(item) <= 4096 for item in files)
            and isinstance(checks, list) and len(checks) <= MAX_CHECKS
            and all(isinstance(item, dict) and isinstance(item.get("key"), str)
                    and len(item["key"]) == 64 and all(c in "0123456789abcdef" for c in item["key"])
                    and type(item.get("ok")) is bool and type(item.get("mutation_seq")) is int
                    and item["mutation_seq"] >= 0 for item in checks))
