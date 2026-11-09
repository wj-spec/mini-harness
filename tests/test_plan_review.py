from __future__ import annotations

import json

import pytest

from app.runtime.plan import Plan, PlanError, load_plan, propose_plan
from app.runtime.plan_review import (
    dismiss,
    latest_plan,
    render_markdown,
    request_changes,
    review_intent,
)


def _plan(**overrides):
    data = {
        "schema_version": 1,
        "plan_id": "pl-1",
        "task_id": "t-20261108-000000-abcdef",
        "objective": "实现计划模式审阅",
        "steps": [
            {"step_id": "s1", "title": "读 README", "kind": "read", "files": ["README.md"]},
            {"step_id": "s2", "title": "改实现", "kind": "edit", "depends_on": ["s1"]},
        ],
        "acceptance_criteria": [
            {"id": "ac1", "text": "测试通过", "confirmed": True, "required": True},
        ],
        "verification_plan": [
            {"id": "v1", "covers": ["ac1"], "command": "pytest -q", "source": "plan_approval"},
        ],
        "constraints": ["不改公共 API"],
        "out_of_scope": ["不做 UI"],
        "open_questions": ["是否保留旧版本"],
    }
    data.update(overrides)
    return Plan.from_dict(data)


def _audit(task_dir):
    path = task_dir / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_review_intent_identifies_plan_version(tmp_path):
    plan = _plan()
    intent = review_intent(plan)
    assert intent["kind"] == "plan-review"
    assert intent["plan_id"] == "pl-1"
    assert intent["plan_version"] == 1
    assert len(intent["plan_hash"]) == 64


def test_render_markdown_is_reviewable_contract():
    text = render_markdown(_plan())
    assert text.startswith("# 实现计划模式审阅")
    assert "## 目标与成功标准" in text
    assert "## 实施步骤" in text
    assert "## 验证命令" in text
    assert "## 审阅反馈" in text
    assert "ac1" in text and "测试通过" in text
    assert "pytest -q" in text
    assert "s1" in text and "README.md" in text
    assert "计划：`pl-1`" in text


def test_request_changes_creates_draft_version_with_feedback(tmp_path):
    task_dir = tmp_path / "tasks" / "t-1"
    plan = _plan()
    propose_plan(task_dir, plan)
    revised = request_changes(task_dir, plan, "请补边界情况", requester="user")
    assert revised.version == 2 and revised.status == "draft"
    assert revised.review_feedback == ["请补边界情况"]
    stored = load_plan(task_dir / "plan" / "plan.v2.json")
    assert stored.review_feedback == ["请补边界情况"]
    assert revised.approval == {}
    assert (task_dir / "plan" / "plan.v1.json").exists()
    events = _audit(task_dir)
    assert any(item["event"] == "plan_changes_requested" for item in events)
    created = next(item for item in events if item["event"] == "plan_changes_requested")
    assert created["intent"]["plan_version"] == 1


def test_request_changes_requires_feedback(tmp_path):
    task_dir = tmp_path / "tasks" / "t-1"
    with pytest.raises(PlanError):
        request_changes(task_dir, _plan(), "   ")


def test_dismiss_records_audit_without_changing_plan(tmp_path):
    task_dir = tmp_path / "tasks" / "t-1"
    plan = _plan()
    propose_plan(task_dir, plan)
    dismiss(task_dir, plan, by="user", note="先不看")
    events = _audit(task_dir)
    assert any(item["event"] == "plan_review_dismissed" for item in events)
    assert load_plan(task_dir / "plan" / "plan.v1.json").status == "proposed"


def test_dismiss_does_not_append_through_an_audit_symlink(tmp_path):
    task_dir = tmp_path / "tasks" / "t-1"
    plan = _plan()
    path = propose_plan(task_dir, plan)
    audit = task_dir / "audit.jsonl"
    audit.unlink()
    outside = tmp_path / "unrelated.txt"
    outside.write_text("keep me\n")
    audit.symlink_to(outside)
    with pytest.raises(PlanError, match="symlink"):
        dismiss(task_dir, plan)
    assert outside.read_text() == "keep me\n"
    assert audit.is_symlink()
    assert load_plan(path).status == "proposed"


def test_latest_plan_uses_newest_version(tmp_path):
    task_dir = tmp_path / "tasks" / "t-1"
    plan = _plan()
    propose_plan(task_dir, plan)
    revised = request_changes(task_dir, plan, "改一下")
    latest = latest_plan(tmp_path)
    assert latest is not None
    path, found = latest
    assert path.name == "plan.v2.json"
    assert found.version == revised.version


def test_review_keeps_exact_plan_when_another_plan_becomes_latest(tmp_path):
    from app.runtime.plan_review import PlanReview

    first = _plan()
    first_path = propose_plan(tmp_path / "tasks" / first.task_id, first)
    review = PlanReview(tmp_path, session_id="session-1")
    shown = review.read_review()
    assert shown["outcome"] == "ready"
    assert shown["target"]["session_id"] == "session-1"
    assert shown["target"]["plan_task_id"] == first.task_id
    second = _plan(plan_id="pl-2", task_id="t-20261108-000001-fedcba", objective="另一个计划")
    second_path = propose_plan(tmp_path / "tasks" / second.task_id, second)
    before = {path: path.read_bytes() for path in (first_path, second_path)}

    reopened = review.read_review(shown["target"])

    assert reopened["target"] == shown["target"]
    assert reopened["summary"]["objective"] == "实现计划模式审阅"
    assert reopened["summary"]["status"] == "proposed"
    assert reopened["body_page"].startswith("# 实现计划模式审阅")
    assert reopened["launch"] is None
    assert {path: path.read_bytes() for path in before} == before
    assert len(list((tmp_path / "tasks").iterdir())) == 2


@pytest.mark.parametrize("change,code", [("revision", "superseded"), ("edit", "content_changed")])
def test_review_refuses_obsolete_content_without_switching_target(tmp_path, change, code):
    from app.runtime.plan_review import PlanReview

    plan = _plan()
    task_dir = tmp_path / "tasks" / plan.task_id
    path = propose_plan(task_dir, plan)
    review = PlanReview(tmp_path, session_id="session-1")
    shown = review.read_review()
    if change == "revision":
        request_changes(task_dir, plan, "补充测试")
    else:
        payload = json.loads(path.read_text())
        payload["objective"] = "内容已改动"
        path.write_text(json.dumps(payload), encoding="utf-8")

    result = review.read_review(shown["target"])

    assert result["outcome"] == "unavailable"
    assert result["code"] == code
    assert result["target"] == shown["target"]
    assert result["launch"] is None
    assert result["body_page"] == ""
    assert result["available_actions"] == []
    replacement = review.read_review(result["replacement_target"])
    assert replacement["outcome"] == "ready"
    assert replacement["target"] != shown["target"]


@pytest.mark.parametrize("patch,code", [
    ({"session_id": "another-session"}, "target_mismatch"),
    ({"plan_id": "another-plan"}, "target_mismatch"),
    ({"plan_task_id": "../outside"}, "invalid_request"),
    ({"plan_version": True}, "invalid_request"),
    ({"plan_version": 0}, "invalid_request"),
    ({"plan_hash": "not-a-hash"}, "invalid_request"),
    ({"unknown": "value"}, "invalid_request"),
])
def test_review_validates_target_before_reading(tmp_path, patch, code):
    from app.runtime.plan_review import PlanReview

    plan = _plan()
    path = propose_plan(tmp_path / "tasks" / plan.task_id, plan)
    review = PlanReview(tmp_path, session_id="session-1")
    ref = review.read_review()["target"]
    ref.update(patch)
    before = path.read_bytes()
    result = review.read_review(ref)
    assert result["outcome"] == "unavailable"
    assert result["code"] == code
    assert result["body_page"] == ""
    assert result["available_actions"] == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("task_id", "t-20261108-000001-fedcba"), ("plan_id", "pl-2"), ("version", 2),
])
def test_review_refuses_file_with_mismatched_identity(tmp_path, field, value):
    from app.runtime.plan_review import PlanReview

    plan = _plan()
    path = propose_plan(tmp_path / "tasks" / plan.task_id, plan)
    review = PlanReview(tmp_path, session_id="session-1")
    ref = review.read_review()["target"]
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = review.read_review(ref)
    assert result["code"] == "target_mismatch"
    assert result["replacement_target"] is None


@pytest.mark.parametrize("damage,code", [
    ("missing", "not_found"), ("json", "storage_error"), ("utf8", "storage_error"),
    ("oversize", "storage_error"), ("newer_corrupt", "storage_error"),
    ("file_link", "needs_reconciliation"), ("plan_link", "needs_reconciliation"),
    ("task_link", "needs_reconciliation"), ("tasks_link", "needs_reconciliation"),
])
def test_review_does_not_read_or_fallback_past_damaged_storage(tmp_path, damage, code):
    from app.runtime.plan_review import PlanReview

    session = tmp_path / "session"
    plan = _plan()
    path = propose_plan(session / "tasks" / plan.task_id, plan)
    review = PlanReview(session, session_id="session-1")
    ref = review.read_review()["target"]
    if damage == "missing":
        path.unlink()
    elif damage == "newer_corrupt":
        path.with_name("plan.v2.json").write_bytes(b"{")
    elif damage in {"json", "utf8", "oversize"}:
        path.write_bytes({"json": b"{", "utf8": b"\xff",
                          "oversize": b" " * (4 * 1024 * 1024 + 1)}[damage])
    else:
        link = {"file_link": path, "plan_link": path.parent,
                "task_link": path.parent.parent, "tasks_link": session / "tasks"}[damage]
        outside = tmp_path / "outside"
        link.rename(outside)
        link.symlink_to(outside, target_is_directory=outside.is_dir())
    result = review.read_review(ref)
    assert result["outcome"] == "unavailable"
    assert result["code"] == code
    assert result["body_page"] == ""
    assert result["launch"] is None
    assert result["replacement_target"] is None


def test_review_discovery_uses_newest_family_version_despite_old_file_mtime(tmp_path):
    import os
    from app.runtime.plan_review import PlanReview

    plan = _plan()
    task_dir = tmp_path / "tasks" / plan.task_id
    path = propose_plan(task_dir, plan)
    request_changes(task_dir, plan, "补充边界")
    os.utime(path, (path.stat().st_mtime + 100, path.stat().st_mtime + 100))
    result = PlanReview(tmp_path, session_id="session-1").read_review()
    assert result["outcome"] == "ready"
    assert result["target"]["plan_version"] == 2
    assert result["summary"]["status"] == "draft"


@pytest.mark.parametrize("damage,code", [
    ("corrupt", "storage_error"), ("link", "needs_reconciliation"),
    ("identity", "target_mismatch"),
])
def test_review_discovery_does_not_hide_latest_broken_version(tmp_path, damage, code):
    from app.runtime.plan_review import PlanReview

    plan = _plan()
    task_dir = tmp_path / "session" / "tasks" / plan.task_id
    path = propose_plan(task_dir, plan)
    newer = path.with_name("plan.v2.json")
    if damage == "corrupt":
        newer.write_text("{")
    elif damage == "link":
        outside = tmp_path / "outside.json"
        outside.write_text(json.dumps(_plan(version=2).to_dict()))
        newer.symlink_to(outside)
    else:
        newer.write_text(json.dumps(_plan(version=3).to_dict()))
    result = PlanReview(tmp_path / "session", session_id="session-1").read_review()
    assert result["outcome"] == "unavailable"
    assert result["code"] == code
    assert result["target"] is None


def test_review_pages_complete_unicode_body_with_one_identity(tmp_path):
    from app.runtime.plan_review import PlanReview

    constraints = [f"边界-{index:04d}: 中文🧭" + "细节" * 20 for index in range(900)]
    plan = _plan(constraints=constraints)
    path = propose_plan(tmp_path / "tasks" / plan.task_id, plan)
    before = path.read_bytes()
    review = PlanReview(tmp_path, session_id="session-1")
    page = review.read_review()
    ref = page["target"]
    pages = []
    while True:
        assert page["outcome"] == "ready"
        assert page["target"] == ref
        assert 0 < len(page["body_page"]) <= 16000
        assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) < 1024 * 1024
        pages.append(page["body_page"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert cursor == sum(map(len, pages))
        page = review.read_review(ref, cursor=cursor)
    assert len(pages) >= 3
    body = "".join(pages)
    for line in constraints:
        assert body.count("- " + line + "\n") == 1
    assert body.startswith("# 实现计划模式审阅\n")
    assert body.endswith("## 审阅反馈\n- （暂无）\n")
    assert path.read_bytes() == before


@pytest.mark.parametrize("cursor", [-1, True, "1", 0.5, 10 ** 9])
def test_review_refuses_invalid_body_cursor(tmp_path, cursor):
    from app.runtime.plan_review import PlanReview

    plan = _plan()
    propose_plan(tmp_path / "tasks" / plan.task_id, plan)
    review = PlanReview(tmp_path, session_id="session-1")
    ref = review.read_review()["target"]
    result = review.read_review(ref, cursor=cursor)
    assert result["code"] == "invalid_request"
    assert result["body_page"] == ""


def test_review_next_page_cannot_rediscover_another_plan(tmp_path):
    from app.runtime.plan_review import PlanReview

    plan = _plan()
    propose_plan(tmp_path / "tasks" / plan.task_id, plan)
    result = PlanReview(tmp_path, session_id="session-1").read_review(cursor=1)
    assert result["code"] == "invalid_request"


def test_review_body_stays_identical_when_approved_between_pages(tmp_path):
    from app.runtime.plan import approve_plan
    from app.runtime.plan_review import PlanReview

    plan = _plan(constraints=["正文🧭" * 9000])
    task_dir = tmp_path / "tasks" / plan.task_id
    propose_plan(task_dir, plan)
    review = PlanReview(tmp_path, session_id="session-1")
    first = review.read_review()
    cursor = first["next_cursor"]
    assert cursor is not None
    before = review.read_review(first["target"], cursor=cursor)
    approve_plan(task_dir, plan, approved_by="user")
    after = review.read_review(first["target"], cursor=cursor)
    assert after["target"] == before["target"]
    assert after["summary"]["status"] == "approved"
    assert after["body_page"] == before["body_page"]
    assert after["next_cursor"] == before["next_cursor"]
    assert review.read_review(first["target"])["body_page"] == first["body_page"]


def test_review_body_contains_all_feedback(tmp_path):
    from app.runtime.plan_review import PlanReview

    feedback = [f"反馈-{index:02d}" for index in range(20)]
    plan = _plan(review_feedback=feedback)
    propose_plan(tmp_path / "tasks" / plan.task_id, plan)
    result = PlanReview(tmp_path, session_id="session-1").read_review()
    for item in feedback:
        assert "- " + item + "\n" in result["body_page"]


def test_review_body_ignores_json_object_key_order(tmp_path):
    from app.runtime.plan_review import PlanReview

    plan = _plan(steps=[{"step_id": "s1", "title": "验证", "verification": {
        "command": "pytest -q", "options": {"a": 1, "b": 2},
    }}])
    path = propose_plan(tmp_path / "tasks" / plan.task_id, plan)
    review = PlanReview(tmp_path, session_id="session-1")
    before = review.read_review()
    payload = json.loads(path.read_text())
    payload["steps"][0]["verification"] = {"options": {"b": 2, "a": 1}, "command": "pytest -q"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    after = review.read_review(before["target"])
    assert after["outcome"] == "ready"
    assert after["target"] == before["target"]
    assert after["body_page"] == before["body_page"]


@pytest.mark.parametrize("discover", [False, True])
def test_review_reports_unencodable_json_content_as_storage_error(tmp_path, discover):
    from app.runtime.plan_review import PlanReview

    plan = _plan()
    path = propose_plan(tmp_path / "tasks" / plan.task_id, plan)
    review = PlanReview(tmp_path, session_id="session-1")
    ref = review.read_review()["target"]
    payload = json.loads(path.read_text())
    payload["objective"] = "invalid \ud800"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = review.read_review(None if discover else ref)
    assert result["outcome"] == "unavailable"
    assert result["code"] == "storage_error"
    assert result["body_page"] == ""


def _execution_review(tmp_path):
    from app.runtime.plan_review import PlanReview
    from app.runtime.task_runtime import TaskRuntime
    from app.runtime.tasks import TaskStore

    store = TaskStore(tmp_path / "session-1")
    original = store.create(kind="run", mode="plan", objective="规划任务", workspace=str(tmp_path))
    plan = _plan(task_id=original.task_id)
    propose_plan(store.task_dir(original.task_id), plan)
    runtime = TaskRuntime(store, workspace=tmp_path)
    review = PlanReview(store.root, session_id=store.root.name, task_runtime=runtime)
    return review, runtime, plan


def test_approve_execute_prepares_a_task_bound_to_the_displayed_plan(tmp_path):
    from app.runtime.plan import can_execute
    from types import SimpleNamespace

    review, runtime, original = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    decision = review.decide_review(ref, "approve-execute", "request-1")
    assert decision.receipt["outcome"] == "launched"
    binding = decision.receipt["launch"]
    assert binding["run_started"] is True
    assert binding["run_id"] == "request-1"
    assert decision.run_task is not None
    assert decision.run_task.task_id == binding["task_id"]
    record = runtime.store.get(binding["task_id"])
    assert (record.kind, record.mode, record.plan_id, record.plan_version) == ("run", "act", "pl-1", 1)
    assert record.extra["creation"]["target"] == ref
    assert [segment.run_id for segment in record.segments] == ["request-1"]
    assert len(runtime.store.list()) == 2
    stored = load_plan(runtime.store.task_dir(original.task_id) / "plan" / "plan.v1.json")
    assert can_execute(stored)
    assert stored.approval["approval_id"] == record.extra["creation"]["key"]
    runtime.finish_run(decision.run_task, SimpleNamespace(status="stopped", stop_reason="test"))


def test_reopened_and_retried_review_returns_the_original_run(tmp_path):
    from app.runtime.plan_review import PlanReview
    from types import SimpleNamespace

    review, runtime, original = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    first = review.decide_review(ref, "approve-execute", "request-1")
    reopened = PlanReview(runtime.store.root, session_id=runtime.store.root.name, task_runtime=runtime)
    try:
        retry = reopened.decide_review(ref, "approve-execute", "request-2", busy=True)
        assert retry.run_task is None
        assert retry.receipt["launch"]["run_started"] is False
        assert retry.receipt["launch"]["run_id"] == "request-1"
        assert retry.receipt["launch"]["task_id"] == first.run_task.task_id
        readonly = PlanReview(runtime.store.root, session_id=runtime.store.root.name)
        queried = readonly.read_review(ref)
        assert queried["launch"]["task_id"] == first.run_task.task_id
        assert queried["launch"]["run_started"] is False
        assert len(runtime.store.list()) == 2
        assert len(runtime.store.get(first.run_task.task_id).segments) == 1
    finally:
        runtime.finish_run(first.run_task, SimpleNamespace(status="stopped", stop_reason="test"))
    assert reopened.read_review(ref)["launch"]["status"] == "paused"


@pytest.mark.parametrize("case,code", [
    ("bad_decision", "invalid_request"), ("bad_request_id", "invalid_request"),
    ("busy", "busy"), ("disabled", "tasks_disabled"), ("no_runtime", "tasks_disabled"),
    ("expired", "review_expired"), ("stale", "plan_stale"),
])
def test_launch_rejection_never_approves_or_creates_a_task(tmp_path, monkeypatch, case, code):
    import os
    import time
    from app.runtime.plan import approve_plan, mark_plan_stale
    from app.runtime.plan_review import PlanReview

    review, runtime, plan = _execution_review(tmp_path)
    task_dir = runtime.store.task_dir(plan.task_id)
    path = task_dir / "plan" / "plan.v1.json"
    ref = review.read_review()["target"]
    if case == "disabled":
        runtime.enabled = False
    elif case == "no_runtime":
        review = PlanReview(runtime.store.root, session_id=runtime.store.root.name)
    elif case == "expired":
        monkeypatch.setenv("VIBE_PLAN_REVIEW_TIMEOUT_SECONDS", "1")
        os.utime(path, (time.time() - 100, time.time() - 100))
    elif case == "stale":
        approve_plan(task_dir, plan, approved_by="user")
        mark_plan_stale(task_dir, plan)
    before = path.read_bytes()
    result = review.decide_review(ref, "unknown" if case == "bad_decision" else "approve-execute",
                                  "" if case == "bad_request_id" else "request-1", busy=case == "busy")
    assert result.receipt["code"] == code
    assert result.run_task is None
    assert result.receipt["launch"] is None
    assert path.read_bytes() == before
    assert len(runtime.store.list()) == 1
    assert not (task_dir / "plan" / "review-launch").exists()


@pytest.mark.parametrize("decision,status,outcome", [
    ("approve-only", "approved", "approved"), ("dismiss", "proposed", "dismissed"),
])
def test_review_non_execution_actions_do_not_need_task_runtime(tmp_path, decision, status, outcome):
    from app.runtime.plan_review import PlanReview

    review, runtime, plan = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    reader = PlanReview(runtime.store.root, session_id=runtime.store.root.name)
    result = reader.decide_review(ref, decision, "request-1")
    assert result.receipt["outcome"] == outcome
    assert result.run_task is None and result.receipt["launch"] is None
    assert len(runtime.store.list()) == 1
    path = runtime.store.task_dir(plan.task_id) / "plan" / "plan.v1.json"
    assert load_plan(path).status == status
    assert not path.parent.joinpath("review-launch").exists()
    if decision == "approve-only":
        before = path.read_bytes()
        again = reader.decide_review(ref, decision, "request-2")
        assert again.receipt["outcome"] == "approved"
        assert path.read_bytes() == before


def test_approved_but_not_created_retry_reuses_reserved_identity(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    review, runtime, plan = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    original_mkdir = Path.mkdir

    def fail_task_creation(path, *args, **kwargs):
        if path.parent == runtime.store.tasks_root and path.name != plan.task_id:
            raise OSError("task directory unavailable")
        return original_mkdir(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", fail_task_creation)
        failed = review.decide_review(ref, "approve-execute", "request-1")
    assert failed.receipt["outcome"] == "approved_not_started"
    assert failed.run_task is None
    pending = review.read_review(ref)
    assert pending["outcome"] == "approved_not_started"
    reserved_id = pending["launch"]["task_id"]
    assert pending["launch"]["run_id"] is None
    assert len(runtime.store.list()) == 1
    path = runtime.store.task_dir(plan.task_id) / "plan" / "plan.v1.json"
    approval = load_plan(path).approval
    resumed = review.decide_review(ref, "approve-execute", "request-2")
    assert resumed.run_task is not None
    assert resumed.run_task.task_id == reserved_id
    assert len(runtime.store.list()) == 2
    assert load_plan(path).approval == approval
    assert len(runtime.store.get(reserved_id).segments) == 1
    runtime.finish_run(resumed.run_task, SimpleNamespace(status="stopped", stop_reason="test"))


@pytest.mark.parametrize("crash_point,already_started", [
    ("reserved", False), ("approved", False), ("task_created", False),
    ("starting", False), ("task_running", False), ("segment", True), ("started", True),
])
def test_process_crash_recovers_one_initial_task(tmp_path, crash_point, already_started):
    import subprocess
    import sys
    from types import SimpleNamespace

    review, runtime, plan = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    script = r'''
import json,os,sys
from pathlib import Path
from app.runtime.plan_review import PlanReview
from app.runtime.task_runtime import TaskRuntime
from app.runtime.tasks import TaskStore
root,workspace,target,point=sys.argv[1:]
ref=json.loads(target)
store=TaskStore(root)
review=PlanReview(root,session_id=Path(root).name,task_runtime=TaskRuntime(store,workspace=workspace))
replace=os.replace
def crash_after_write(source,destination):
    replace(source,destination)
    path=Path(destination)
    payload=json.loads(path.read_text())
    hit=False
    if path.parent.name=='review-launch':
        hit=payload['stage']==point
    elif path.name=='plan.v1.json':
        hit=point=='approved' and payload['status']=='approved'
    elif path.name=='meta.json' and path.parent.name!=ref['plan_task_id']:
        hit=(point=='task_created' and payload['status']=='created'
             or point=='task_running' and payload['status']=='running' and not payload['segments']
             or point=='segment' and bool(payload['segments']))
    if hit: os._exit(82)
os.replace=crash_after_write
review.decide_review(ref,'approve-execute','crashed-request')
raise SystemExit(3)
'''
    child = subprocess.run([sys.executable, "-c", script, str(runtime.store.root), str(tmp_path),
                            json.dumps(ref), crash_point], capture_output=True, text=True, timeout=10)
    assert child.returncode == 82, child.stderr
    operations = list((runtime.store.task_dir(plan.task_id) / "plan" / "review-launch").glob("*.json"))
    assert len(operations) == 1
    reservation = json.loads(operations[0].read_text())
    retry = review.decide_review(ref, "approve-execute", "recovery-request")
    assert retry.receipt["outcome"] == "launched", retry.receipt
    assert retry.receipt["launch"]["task_id"] == reservation["task_id"]
    assert (retry.run_task is None) == already_started
    record = runtime.store.get(reservation["task_id"])
    assert len(runtime.store.list()) == 2
    assert len(record.segments) == 1
    if already_started:
        assert retry.receipt["launch"]["run_id"] == "crashed-request"
        assert retry.receipt["launch"]["run_started"] is False
    else:
        assert retry.receipt["launch"]["run_id"] == "recovery-request"
        runtime.finish_run(retry.run_task, SimpleNamespace(status="stopped", stop_reason="test"))


def test_failure_before_segment_can_be_retried_without_restarting_process(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from types import SimpleNamespace

    review, runtime, plan = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    replace_file = os.replace

    def fail_running_meta(source, destination):
        path = Path(destination)
        if path.name == "meta.json" and path.parent.name != plan.task_id:
            payload = json.loads(Path(source).read_text())
            if payload["status"] == "running" and not payload["segments"]:
                raise OSError("cannot start segment")
        return replace_file(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_running_meta)
        failed = review.decide_review(ref, "approve-execute", "request-1")
    assert failed.receipt["outcome"] == "approved_not_started"
    assert failed.run_task is None
    task_id = failed.receipt["launch"]["task_id"]
    assert not runtime.store.get(task_id).segments
    assert not (runtime.store.task_dir(task_id) / "lock").exists()
    retry = review.decide_review(ref, "approve-execute", "request-2")
    assert retry.run_task is not None and retry.run_task.task_id == task_id
    assert [segment.run_id for segment in runtime.store.get(task_id).segments] == ["request-2"]
    runtime.finish_run(retry.run_task, SimpleNamespace(status="stopped", stop_reason="test"))


def test_final_receipt_write_failure_keeps_the_single_handoff(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from types import SimpleNamespace

    review, runtime, plan = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    replace_file = os.replace

    def fail_started_receipt(source, destination):
        if Path(destination).parent.name == "review-launch":
            if json.loads(Path(source).read_text())["stage"] == "started":
                raise OSError("receipt write unavailable")
        return replace_file(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_started_receipt)
        first = review.decide_review(ref, "approve-execute", "request-1")
    assert first.run_task is not None
    assert first.receipt["launch"]["run_started"] is True
    retry = review.decide_review(ref, "approve-execute", "request-2")
    assert retry.run_task is None
    assert retry.receipt["launch"]["run_id"] == "request-1"
    assert len(runtime.store.get(first.run_task.task_id).segments) == 1
    runtime.finish_run(first.run_task, SimpleNamespace(status="stopped", stop_reason="test"))


@pytest.mark.parametrize("damage", [
    "schema_bool", "ref_bool", "creation_bool", "record_id", "stage_list",
    "failed_string", "wrong_key", "invalid_json", "missing_meta", "meta_symlink",
])
def test_damaged_launch_evidence_never_creates_another_run(tmp_path, damage):
    from types import SimpleNamespace

    review, runtime, plan = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    first = review.decide_review(ref, "approve-execute", "request-1")
    runtime.finish_run(first.run_task, SimpleNamespace(status="stopped", stop_reason="test"))
    operation = next((runtime.store.task_dir(plan.task_id) / "plan" / "review-launch").glob("*.json"))
    meta = runtime.store.task_dir(first.run_task.task_id) / "meta.json"
    path = meta if damage in {"creation_bool", "record_id", "missing_meta", "meta_symlink"} else operation
    payload = json.loads(path.read_text())
    if damage == "schema_bool":
        payload["schema_version"] = True
    elif damage == "ref_bool":
        payload["target"]["plan_version"] = True
    elif damage == "creation_bool":
        payload["creation"]["target"]["plan_version"] = True
    elif damage == "record_id":
        payload["task_id"] = plan.task_id
    elif damage == "stage_list":
        payload["stage"] = []
    elif damage == "failed_string":
        payload["prepare_failed"] = "true"
    elif damage == "wrong_key":
        payload["key"] = "0" * 64
    path.write_text(json.dumps(payload))
    if damage == "invalid_json":
        path.write_text("{bad")
    elif damage == "missing_meta":
        path.unlink()
    elif damage == "meta_symlink":
        copy = tmp_path / "unrelated.json"
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
    directories = sorted(item.name for item in runtime.store.tasks_root.iterdir())
    before = path.read_bytes() if path.exists() else None
    queried = review.read_review(ref)
    retried = review.decide_review(ref, "approve-execute", "request-2")
    assert queried["outcome"] == retried.receipt["outcome"] == "unavailable"
    assert retried.run_task is None and retried.receipt["launch"] is None
    assert sorted(item.name for item in runtime.store.tasks_root.iterdir()) == directories
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("version", [1, None])
def test_old_bound_execution_requires_reconciliation_before_new_launch(tmp_path, version):
    from app.runtime.plan import approve_plan

    review, runtime, plan = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    approve_plan(runtime.store.task_dir(plan.task_id), plan, approved_by="user")
    legacy = runtime.store.create(kind="run", mode="act", objective=plan.objective,
                                  workspace=runtime.workspace, plan_id=plan.plan_id, plan_version=version)
    before = (runtime.store.task_dir(plan.task_id) / "plan" / "plan.v1.json").read_bytes()
    query = review.read_review(ref)
    result = review.decide_review(ref, "approve-execute", "request-1")
    assert query["code"] == result.receipt["code"] == "needs_reconciliation"
    assert result.run_task is None
    assert len(runtime.store.list()) == 2
    assert not runtime.store.get(legacy.task_id).segments
    assert (runtime.store.task_dir(plan.task_id) / "plan" / "plan.v1.json").read_bytes() == before
    assert not (runtime.store.task_dir(plan.task_id) / "plan" / "review-launch").exists()


def test_two_processes_compete_for_one_plan_launch(tmp_path):
    import select
    import subprocess
    import sys

    review, runtime, plan = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    script = r'''
import json,os,sys
from pathlib import Path
from app.runtime.plan_review import PlanReview
from app.runtime.task_runtime import TaskRuntime
from app.runtime.tasks import TaskStore
root,workspace,target=sys.argv[1:]
replace=os.replace
def hold_reservation(source,destination):
    replace(source,destination)
    path=Path(destination)
    if path.parent.name=='review-launch' and json.loads(path.read_text())['stage']=='reserved':
        print('reserved',flush=True)
        sys.stdin.readline()
os.replace=hold_reservation
runtime=TaskRuntime(TaskStore(root),workspace=workspace)
review=PlanReview(root,session_id=Path(root).name,task_runtime=runtime)
result=review.decide_review(json.loads(target),'approve-execute','first-process')
print(json.dumps(result.receipt),flush=True)
'''
    child = subprocess.Popen([sys.executable, "-c", script, str(runtime.store.root), str(tmp_path),
                              json.dumps(ref)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 5)[0], "reservation was not reached"
        assert child.stdout.readline().strip() == "reserved"
        competing = review.decide_review(ref, "approve-execute", "second-process")
        assert competing.receipt["code"] == "lock_busy"
        assert competing.run_task is None and len(runtime.store.list()) == 1
        output, errors = child.communicate("continue\n", timeout=10)
        assert child.returncode == 0, errors
        first = json.loads(output)
        retry = review.decide_review(ref, "approve-execute", "second-retry")
        assert first["launch"]["run_started"] is True
        assert retry.run_task is None and retry.receipt["launch"]["run_started"] is False
        assert retry.receipt["launch"]["task_id"] == first["launch"]["task_id"]
        record = runtime.store.get(first["launch"]["task_id"])
        assert [item.run_id for item in record.segments] == ["first-process"]
        assert len(runtime.store.list()) == 2
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


@pytest.mark.parametrize("owner", ["live", "foreign"])
def test_old_starting_record_is_not_proof_that_owner_died(tmp_path, monkeypatch, owner):
    import os
    from pathlib import Path

    review, runtime, plan = _execution_review(tmp_path)
    ref = review.read_review()["target"]
    replace_file = os.replace

    def fail_before_running(source, destination):
        path = Path(destination)
        if path.name == "meta.json" and path.parent.name != plan.task_id:
            if json.loads(Path(source).read_text())["status"] == "running":
                raise OSError("prepare failed")
        return replace_file(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_before_running)
        failed = review.decide_review(ref, "approve-execute", "request-1")
    assert failed.receipt["outcome"] == "approved_not_started"
    operation = next((runtime.store.task_dir(plan.task_id) / "plan" / "review-launch").glob("*.json"))
    payload = json.loads(operation.read_text())
    payload.update(prepare_failed=False, created_at="2000-01-01T00:00:00Z", updated_at="2000-01-01T00:00:00Z")
    if owner == "foreign":
        payload["owner_host"] = "another-host.invalid"
    operation.write_text(json.dumps(payload))
    before = operation.read_bytes()
    assert review.read_review(ref)["code"] == "needs_reconciliation"
    retry = review.decide_review(ref, "approve-execute", "request-2")
    assert retry.receipt["code"] == "needs_reconciliation"
    assert retry.run_task is None and operation.read_bytes() == before
    assert len(runtime.store.list()) == 2
    assert not runtime.store.get(payload["task_id"]).segments


def test_invalid_legacy_goal_binding_is_rejected_before_approval(tmp_path):
    review, runtime, plan = _execution_review(tmp_path)
    path = runtime.store.task_dir(plan.task_id) / "plan" / "plan.v1.json"
    payload = json.loads(path.read_text())
    payload["goal_id"] = {"invalid": "binding"}
    path.write_text(json.dumps(payload))
    ref = review.read_review()["target"]
    before = path.read_bytes()
    result = review.decide_review(ref, "approve-execute", "request-1")
    assert result.receipt["code"] == "invalid_plan"
    assert result.run_task is None and path.read_bytes() == before
    assert len(runtime.store.list()) == 1
    assert not (path.parent / "review-launch").exists()
