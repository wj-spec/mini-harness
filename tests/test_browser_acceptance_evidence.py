import json
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from app.session import SessionStore
from core.models import Message, ToolCall
from scripts.browser_acceptance_evidence import collect_report


ORIGIN = "http://127.0.0.1:3000"
DRAFT = "B2 验收草稿"


def fixture_evidence(workspace):
    session = SessionStore.new(workspace, "acceptance-model", purpose="test")
    trace, events = [{"type": "run_start", "run_id": "r1", "ts": 99}], []

    def call(remote, call_id, args, *, run="r1", allowed=True, snapshot=True):
        start = 100 + len(trace)
        tool = f"mcp_browser_{remote}_123456789abc"
        trace.append({"type": "tool_start", "run_id": run, "step": 1,
                      "tool_call_id": call_id, "tool": tool, "args": args, "ts": start})
        if remote != "browser_snapshot":
            trace.append({"type": "approval", "run_id": run, "step": 1,
                          "tool_call_id": call_id, "tool": tool, "allowed": allowed,
                          "scope": "once" if allowed else "deny", "ts": start + 0.1})
        trace.append({"type": "tool_end", "run_id": run, "step": 1,
                      "tool_call_id": call_id, "tool": tool, "ok": allowed,
                      "status": "succeeded" if allowed else "denied", "ts": start + 0.2})
        payload = {
            "snapshot_id": call_id, "url": ORIGIN + "/", "snapshot": "count 1",
            "elements": [
                {"ref": "note", "name": "Note", "value": DRAFT},
                {"ref": "shade", "name": "Shade", "options": [{"value": "green", "selected": True}]},
            ],
        }
        output = ("[untrusted browser data]\n" + json.dumps(payload)) if snapshot else "(empty)"
        if not allowed:
            output = "ERROR denied_by_user"
        for stamp, message in [
            (start - 0.1, Message("assistant", tool_calls=[ToolCall(tool, args, call_id)])),
            (start + 0.3, Message("tool", output, name=tool, tool_call_id=call_id)),
        ]:
            events.append({"type": "message", "ts": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                           "message": asdict(message)})
        return payload

    call("browser_click", "click", {"snapshot_id": "s0", "ref": "increment"})
    call("browser_type", "fill", {"snapshot_id": "click", "ref": "note", "text": DRAFT})
    call("browser_select_option", "select", {"snapshot_id": "fill", "ref": "shade", "values": ["green"]})
    trace.append({"type": "run_end", "run_id": "r1", "status": "done", "total_tokens": 150, "ts": trace[-1]["ts"] + 0.4})
    trace.append({"type": "run_start", "run_id": "r2", "ts": trace[-1]["ts"] + 0.1})
    call("browser_type", "deny", {"snapshot_id": "select", "ref": "note", "text": "拒绝写入"}, run="r2", allowed=False)
    call("browser_snapshot", "verify", {}, run="r2")
    trace.append({"type": "run_end", "run_id": "r2", "status": "done", "total_tokens": 120, "ts": trace[-1]["ts"] + 0.4})
    return session, trace, events


def save(session, trace, events):
    session.trace_path.write_text("\n".join(json.dumps(item) for item in trace) + "\n")
    session.events_path.write_text("\n".join(json.dumps(item) for item in events) + "\n")


def report(workspace, before_ids=(), submissions=0):
    return collect_report(workspace, set(before_ids), origin=ORIGIN, submissions=submissions, exit_code=0)


def test_checks_require_actual_results_and_keep_manual_experience_pending(tmp_path):
    old = SessionStore.new(tmp_path, "old", purpose="test")
    session, trace, events = fixture_evidence(tmp_path)
    save(session, trace, events)
    result = report(tmp_path, [old.id])
    assert result["passed"] is True and result["status"] == "passed"
    assert result["session_id"] == session.id
    assert result["user_experience"] == "pending"
    assert result["real_model_verified"] is False
    assert result["denied_actions"] == 1
    assert result["total_tokens"] == 270


def test_spoken_completion_without_tool_evidence_is_incomplete(tmp_path):
    session = SessionStore.new(tmp_path, "acceptance-model", purpose="test")
    session.append_message(Message("assistant", "已点击、填写并选择，提交数为0。"))
    session.append_trace({"type": "run_end", "run_id": "r1", "status": "done"})
    result = report(tmp_path)
    assert not result["passed"] and result["status"] == "incomplete"


@pytest.mark.parametrize("change", ["no_approval", "session_approval", "wrong_value", "old_snapshot", "wrong_origin", "denied_executed"])
def test_incomplete_or_contradictory_evidence_cannot_pass(tmp_path, change):
    session, trace, events = fixture_evidence(tmp_path)
    if change == "no_approval":
        trace[:] = [item for item in trace if not (item["type"] == "approval" and item["tool_call_id"] == "fill")]
    elif change == "session_approval":
        next(item for item in trace if item["type"] == "approval")["scope"] = "session"
    elif change in {"wrong_value", "wrong_origin"}:
        message = events[-1]["message"]
        payload = json.loads(message["content"].removeprefix("[untrusted browser data]\n"))
        if change == "wrong_value":
            payload["elements"][0]["value"] = "拒绝写入"
        else:
            payload["url"] = "https://other.example/"
        message["content"] = "[untrusted browser data]\n" + json.dumps(payload)
    elif change == "old_snapshot":
        trace[:] = [item for item in trace if item.get("tool_call_id") != "verify"]
        events[:] = events[:-2]
    else:
        end = next(item for item in trace if item["type"] == "tool_end" and item["tool_call_id"] == "deny")
        end.update(ok=True, status="succeeded")
    save(session, trace, events)
    assert report(tmp_path)["passed"] is False


def test_zero_or_multiple_new_sessions_never_select_latest(tmp_path):
    assert report(tmp_path)["status"] == "incomplete"
    for _ in range(2):
        session, trace, events = fixture_evidence(tmp_path)
        save(session, trace, events)
    assert report(tmp_path)["status"] == "ambiguous"


def test_submission_or_wrong_session_purpose_fails(tmp_path):
    session, trace, events = fixture_evidence(tmp_path)
    save(session, trace, events)
    assert report(tmp_path, submissions=1)["status"] == "failed"
    metadata = json.loads(session.meta_path.read_text())
    metadata["purpose"] = "user"
    session.meta_path.write_text(json.dumps(metadata))
    assert report(tmp_path)["status"] == "failed"


def test_duplicate_call_ids_across_runs_do_not_reuse_an_earlier_success(tmp_path):
    session, trace, events = fixture_evidence(tmp_path)
    for item in trace:
        if item.get("tool_call_id") == "deny":
            item["tool_call_id"] = "fill"
    for event in events:
        message = event["message"]
        for call in message.get("tool_calls", []):
            if call["id"] == "deny":
                call["id"] = "fill"
        if message.get("tool_call_id") == "deny":
            message["tool_call_id"] = "fill"
    save(session, trace, events)
    assert report(tmp_path)["passed"] is True


@pytest.mark.parametrize("change", ["final_values", "unfinished_run", "approval_after_end"])
def test_later_contradictions_cannot_be_hidden_by_earlier_success(tmp_path, change):
    session, trace, events = fixture_evidence(tmp_path)
    if change == "final_values":
        final = dict(events[-1])
        final["message"] = dict(final["message"])
        content = json.loads(final["message"]["content"].removeprefix("[untrusted browser data]\n"))
        content["elements"][0]["value"] = "unexpected final value"
        final["message"]["content"] = "[untrusted browser data]\n" + json.dumps(content)
        final["message"]["tool_call_id"] = "last"
        final["ts"] = datetime.fromtimestamp(150, timezone.utc).isoformat()
        events.append(final)
        for kind, stamp in [("tool_start", 149), ("tool_end", 149.5)]:
            trace.append({"type": kind, "ts": stamp, "run_id": "r2", "step": 9,
                          "tool_call_id": "last", "tool": final["message"]["name"], "args": {}, "ok": True})
    elif change == "unfinished_run":
        trace.append({"type": "run_start", "run_id": "r3", "ts": 150})
    else:
        approval = next(item for item in trace if item["type"] == "approval")
        trace.remove(approval)
        trace.append(approval)
    save(session, trace, events)
    assert report(tmp_path)["passed"] is False


@pytest.mark.parametrize("output", [None, "(lost snapshot)"])
def test_successful_write_with_missing_result_does_not_reuse_old_snapshot(tmp_path, output):
    session, trace, events = fixture_evidence(tmp_path)
    tool = "mcp_browser_browser_type_123456789abc"
    for kind, stamp in [("tool_start", 149), ("approval", 149.2), ("tool_end", 149.5)]:
        trace.append({"type": kind, "ts": stamp, "run_id": "r2", "step": 9,
                      "tool_call_id": "last-write", "tool": tool,
                      "args": {"text": "other value"}, "ok": True,
                      "allowed": True, "scope": "once", "status": "succeeded"})
    if output is not None:
        events.append({"type": "message", "ts": datetime.fromtimestamp(150, timezone.utc).isoformat(),
                       "message": {"role": "tool", "name": tool, "tool_call_id": "last-write", "content": output}})
    save(session, trace, events)
    assert report(tmp_path)["passed"] is False


@pytest.mark.parametrize("ending", ["missing", "failed", "contradictory"])
def test_unknown_later_tool_state_never_reuses_a_prior_snapshot(tmp_path, ending):
    session, trace, events = fixture_evidence(tmp_path)
    if ending == "contradictory":
        next(item for item in trace if item["type"] == "tool_end")["status"] = "failed"
    else:
        trace.append({"type": "tool_start", "ts": 149, "run_id": "r2", "step": 9,
                      "tool_call_id": "unknown", "tool": "mcp_browser_browser_click_123456789abc", "args": {}})
        if ending == "failed":
            trace.append({"type": "tool_end", "ts": 149.5, "run_id": "r2", "step": 9,
                          "tool_call_id": "unknown", "tool": "mcp_browser_browser_click_123456789abc",
                          "ok": False, "status": "timeout"})
    save(session, trace, events)
    assert report(tmp_path)["passed"] is False


def test_completed_first_task_is_visible_before_a_refusal_task_exists(tmp_path):
    session, trace, events = fixture_evidence(tmp_path)
    trace = [event for event in trace if event.get("run_id") == "r1"]
    events = events[:6]
    save(session, trace, events)
    result = report(tmp_path)
    assert result["passed"] is False and result["status"] == "incomplete"
    assert result["checks"]["click_type_select"] is True
    assert result["checks"]["initial_values"] is True
    assert result["checks"]["denial_not_executed"] is False


def test_completed_extra_runs_after_acceptance_do_not_invalidate_final_values(tmp_path):
    session, trace, events = fixture_evidence(tmp_path)
    tool = "mcp_browser_browser_snapshot_123456789abc"
    start = trace[-1]["ts"] + 0.1
    trace.extend([
        {"type": "run_start", "run_id": "r3", "ts": start},
        {"type": "tool_start", "run_id": "r3", "step": 1, "tool_call_id": "extra", "tool": tool,
         "args": {}, "ts": start + 0.1},
        {"type": "tool_end", "run_id": "r3", "step": 1, "tool_call_id": "extra", "tool": tool,
         "ok": True, "status": "succeeded", "ts": start + 0.2},
        {"type": "run_end", "run_id": "r3", "status": "done", "total_tokens": 10, "ts": start + 0.4},
    ])
    payload = {
        "snapshot_id": "extra", "url": ORIGIN + "/", "snapshot": "count 0",
        "elements": [
            {"ref": "note", "name": "Note", "value": "extra run changed page"},
            {"ref": "shade", "name": "Shade", "options": [{"value": "blue", "selected": True}]},
        ],
    }
    events.append({
        "type": "message", "ts": datetime.fromtimestamp(start + 0.3, timezone.utc).isoformat(),
        "message": asdict(Message("tool", "[untrusted browser data]\n" + json.dumps(payload),
                                  name=tool, tool_call_id="extra")),
    })
    save(session, trace, events)
    result = report(tmp_path)
    assert result["passed"] is True
    assert result["checks"]["final_values"] is True
