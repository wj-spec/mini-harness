"""只核对浏览器验收的会话证据，不以模型自述认定通过。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from app.browser_policy import BROWSER_READS, BROWSER_WRITES, normalized_origin
from app.session import SessionStore

DRAFT = "B2 验收草稿"
REJECTED_DRAFT = "拒绝写入"


def _remote(name: str) -> str | None:
    for remote in BROWSER_READS | BROWSER_WRITES:
        if re.fullmatch(r"mcp_browser_" + remote + r"(?:_[0-9a-f]{12})?", name):
            return remote
    return None


def _timestamp(value) -> float:
    if type(value) in (int, float):
        return float(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _read(path: Path) -> list[dict]:
    if path.is_symlink():
        raise ValueError("symlink evidence is not accepted")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("invalid evidence record")
    return records


def _snapshot_correct(snapshot: dict, origin: str) -> bool:
    try:
        if normalized_origin(snapshot["url"]) != normalized_origin(origin):
            return False
        elements = snapshot["elements"]
        note = next(element for element in elements if element.get("name") == "Note")
        shade = next(element for element in elements if element.get("name") == "Shade")
        selected = [option["value"] for option in shade["options"] if option.get("selected")]
        return (note.get("value") == DRAFT and selected == ["green"]
                and re.search(r"\bcount 1\b", snapshot["snapshot"]) is not None)
    except (KeyError, TypeError, ValueError, StopIteration):
        return False


def _calls(trace: list[dict], events: list[dict]) -> list[dict]:
    calls = {}
    for index, event in enumerate(trace):
        kind = event.get("type")
        if kind not in {"tool_start", "approval", "tool_end"}:
            continue
        key = (event.get("run_id"), event.get("tool_call_id"), event.get("step"), event.get("tool"))
        if kind == "tool_start":
            if key in calls:
                raise ValueError("ambiguous tool trace identity")
            calls[key] = {"start": event, "index": index, "remote": _remote(event.get("tool", ""))}
        elif key in calls:
            calls[key][kind] = event
            calls[key][kind + "_index"] = index

    # 消息日志没有 run_id；按同一调用的终态时间匹配，避免跨轮重用 call_id。
    for index, event in enumerate(events):
        message = event.get("message", {})
        if event.get("type") != "message" or message.get("role") != "tool":
            continue
        stamp = _timestamp(event["ts"])
        candidates = [call for call in calls.values()
                      if "message" not in call and "tool_end" in call
                      and call["start"].get("tool") == message.get("name")
                      and call["start"].get("tool_call_id") == message.get("tool_call_id")
                      and _timestamp(call["start"]["ts"]) <= stamp
                      and _timestamp(call["tool_end"]["ts"]) <= stamp]
        if not candidates:
            continue
        candidates.sort(key=lambda call: _timestamp(call["tool_end"]["ts"]), reverse=True)
        if len(candidates) > 1 and candidates[0]["tool_end"]["ts"] == candidates[1]["tool_end"]["ts"]:
            raise ValueError("ambiguous tool result identity")
        call = candidates[0]
        call.update(message=message, message_index=index)
        try:
            payload = json.loads(message.get("content", "").removeprefix("[untrusted browser data]\n"))
            if isinstance(payload, dict) and "snapshot_id" in payload:
                call["snapshot"] = payload
        except (ValueError, TypeError):
            pass
    return list(calls.values())


def _required_runs(trace: list[dict], denial: dict | None) -> set[str]:
    """验收只覆盖第一任务 + 拒绝任务；之后用户额外探索的 run 不参与 final_values。"""
    starts = [event.get("run_id") for event in trace if event.get("type") == "run_start"]
    if denial is None:
        return set(starts)
    second = denial["start"].get("run_id")
    if second not in starts:
        return set(starts)
    index = starts.index(second)
    return set(starts[max(0, index - 1): index + 1])


def collect_report(workspace: Path, before_ids: set[str], *, origin: str,
                   submissions: int, exit_code: int | None) -> dict:
    """返回自动检查结论；真实模型身份与界面体验仍需人工核对。"""
    report = {
        "passed": False, "status": "incomplete", "checks": {},
        "real_model_verified": False, "user_experience": "pending",
        "submissions": submissions, "exit_code": exit_code,
    }
    base = Path(workspace) / ".vibe/sessions"
    if base.is_symlink():
        report["reason"] = "session directory is a symlink"
        return report
    candidates = sorted(path for path in base.iterdir() if path.is_dir() and path.name not in before_ids) if base.is_dir() else []
    if len(candidates) != 1:
        report.update(status="ambiguous" if len(candidates) > 1 else "incomplete",
                      reason="expected exactly one new session", candidate_sessions=[path.name for path in candidates])
        return report
    root = candidates[0]
    if root.is_symlink():
        report["reason"] = "session is a symlink"
        return report
    report.update(session_id=root.name, session_path=str(root))
    store = SessionStore(root)
    if store.purpose != "test":
        report.update(status="failed", reason="acceptance must use a test session")
        return report
    try:
        trace, events = _read(store.trace_path), _read(store.events_path)
        calls = _calls(trace, events)
        runs = [event for event in trace if event.get("type") == "run_end"]
        started_runs = [event.get("run_id") for event in trace if event.get("type") == "run_start"]
        ended_runs = [event.get("run_id") for event in runs]
        successes = [call for call in calls if call.get("tool_end", {}).get("ok") is True]
        denied = [call for call in calls if call.get("approval", {}).get("allowed") is False]
        target_denials = [call for call in denied if call["remote"] == "browser_type"
                          and call["start"].get("args", {}).get("text") == REJECTED_DRAFT]
        denial = target_denials[0] if target_denials else None
        required_runs = _required_runs(trace, denial)
        scoped_calls = [call for call in calls if call["start"].get("run_id") in required_runs]
        good = [call for call in successes if call.get("snapshot") is not None and _snapshot_correct(call["snapshot"], origin)
                and call["start"].get("run_id") in required_runs]
        snapshots = [call for call in successes if "snapshot" in call and call["start"].get("run_id") in required_runs]
        latest = max(snapshots, key=lambda call: call["message_index"]) if snapshots else None
        first_task_end = denial["index"] if denial is not None else len(trace)
        initial = [call for call in good if call["index"] < first_task_end]
        followups = [call for call in good if call["remote"] == "browser_snapshot" and denial is not None
                     and call.get("message_index", -1) > denial.get("message_index", float("inf"))]
        allowed_writes = [call for call in successes if call["remote"] in BROWSER_WRITES]
        succeeded_ends = [event for event in trace if event.get("type") == "tool_end" and event.get("ok") is True]
        initial_actions = {call["remote"] for call in successes if call["index"] < first_task_end}
        last_write = max((call["index"] for call in scoped_calls if call["remote"] in BROWSER_WRITES), default=-1)
        checks = {
            "normal_runs": len(runs) >= 2 and all(event.get("status") == "done" for event in runs),
            "complete_runs": len(started_runs) == len(ended_runs) and set(started_runs) == set(ended_runs),
            "ink_exit": exit_code in (None, 0),
            "zero_submissions": submissions == 0,
            "browser_tools_only": all(_remote(event.get("tool", "")) is not None for event in succeeded_ends),
            "complete_success_traces": len(successes) == len(succeeded_ends),
            "terminal_tools": all("tool_end" in call for call in calls),
            "consistent_terminals": all(event.get("status") == "succeeded" for event in succeeded_ends),
            "complete_write_results": all("message" in call and "snapshot" in call for call in allowed_writes),
            "once_per_write": all(call.get("approval", {}).get("allowed") is True
                                  and call["approval"].get("scope") == "once"
                                  and call["index"] < call["approval_index"] < call["tool_end_index"]
                                  for call in allowed_writes),
            "click_type_select": {"browser_click", "browser_type", "browser_select_option"} <= initial_actions,
            "initial_values": bool(initial),
            "denial_not_executed": bool(denial) and denial.get("tool_end", {}).get("ok") is False
                                   and denial["tool_end"].get("status") == "denied"
                                   and "ERROR denied_by_user" in denial.get("message", {}).get("content", ""),
            "no_rejected_value_written": not any(call["remote"] == "browser_type"
                                                  and call["start"].get("args", {}).get("text") == REJECTED_DRAFT
                                                  for call in successes),
            "snapshot_after_denial": bool(followups),
            "final_values": latest is not None and _snapshot_correct(latest["snapshot"], origin),
            "snapshot_after_last_attempt": latest is not None and latest["index"] >= last_write,
        }
        report.update(checks=checks, denied_actions=len(denied),
                      total_tokens=sum(event.get("total_tokens", 0) for event in runs),
                      model=json.loads(store.meta_path.read_text()).get("model"),
                      evidence_hashes={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                       for path in (store.trace_path, store.events_path)})
        if all(checks.values()):
            report.update(passed=True, status="passed")
        else:
            safety = ("ink_exit", "zero_submissions", "browser_tools_only", "complete_success_traces",
                      "once_per_write", "no_rejected_value_written")
            report.update(status="failed" if any(not checks[key] for key in safety) else "incomplete",
                          reason="missing or contradictory evidence",
                          unmet_checks=[key for key, value in checks.items() if not value])
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        report.update(reason=f"invalid or incomplete evidence: {type(exc).__name__}")
    return report
