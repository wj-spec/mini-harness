from __future__ import annotations

import json
import os

import pytest

from app.runtime.plan import (
    Plan, PlanError, PlanStep, load_plan, plan_path, render_plan, save_plan,
)


def _step(step_id="s1", **overrides):
    data = {"step_id": step_id, "title": f"步骤 {step_id}", "kind": "read"}
    data.update(overrides)
    return data


def _plan(**overrides):
    data = {
        "schema_version": 1, "plan_id": "pl-1", "task_id": "t-20261106-000000-abcdef",
        "objective": "修复问题", "steps": [_step()],
    }
    data.update(overrides)
    return data


def test_plan_roundtrip_and_hash(tmp_path):
    plan = Plan.from_dict(_plan(acceptance_criteria=[
        {"id": "ac1", "text": "测试通过", "verify": "pytest", "confirmed": False},
    ]))
    path = save_plan(tmp_path, plan)
    loaded = load_plan(path)
    assert loaded.plan_id == "pl-1" and loaded.steps[0].step_id == "s1"
    assert loaded.plan_hash() == plan.plan_hash()
    assert loaded.acceptance_criteria[0]["confirmed"] is False
    assert not (tmp_path / "plan" / "plan.v1.json.tmp").exists()


@pytest.mark.parametrize("created_at", [None, ""])
@pytest.mark.parametrize("kind", ["read", "edit"])
def test_legacy_plan_review_identity_survives_read_and_approval(tmp_path, created_at, kind):
    from app.runtime.plan import approve_plan, content_hash

    payload = _plan(status="proposed", steps=[_step(kind=kind)])
    if created_at is not None:
        payload["created_at"] = created_at
    path = plan_path(tmp_path, 1)
    path.parent.mkdir()
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    first = load_plan(path)
    assert first.steps[0].risk == "read"  # 旧产物缺省值不迁移。
    identity = content_hash(first)
    assert content_hash(load_plan(path)) == identity
    assert path.read_bytes() == before
    approve_plan(tmp_path, first, approved_by="user")
    approved = load_plan(path)
    assert content_hash(approved) == identity
    assert approved.approval["plan_hash"] == identity
    assert Plan.from_dict(_plan()).created_at


def test_plan_read_limit_is_four_mib_and_does_not_truncate(tmp_path):
    limit = 4 * 1024 * 1024
    payload = _plan(constraints=[""])
    empty_size = len(json.dumps(payload).encode("utf-8"))
    payload["constraints"] = ["x" * (limit - empty_size)]
    raw = json.dumps(payload).encode("utf-8")
    assert len(raw) == limit
    path = tmp_path / "plan.json"
    path.write_bytes(raw)
    assert len(load_plan(path).constraints[0]) == limit - empty_size
    path.write_bytes(raw + b" ")
    with pytest.raises(PlanError, match="4 MiB"):
        load_plan(path)
    assert path.stat().st_size == limit + 1


def test_invalid_utf8_plan_is_a_plan_error(tmp_path):
    path = tmp_path / "plan.json"
    path.write_bytes(b'\xff')
    with pytest.raises(PlanError, match="UTF-8"):
        load_plan(path)


@pytest.mark.parametrize("change", [
    {"objective": ""},
    {"steps": []},
    {"steps": [_step("s1"), _step("s1")]},
    {"steps": [_step("s1", depends_on=["missing"])]},
    {"steps": [_step("s1", files=["/etc/passwd"])]},
    {"steps": [_step("s1", files=["../outside"])]},
    {"steps": [_step("s1", kind="bogus")]},
    {"steps": [_step("s1", risk="bogus")]},
    {"schema_version": 2},
])
def test_invalid_plans_rejected(change):
    with pytest.raises(PlanError):
        Plan.from_dict(_plan(**change))


def test_dependency_cycle_rejected():
    with pytest.raises(PlanError):
        Plan.from_dict(_plan(steps=[
            _step("s1", depends_on=["s2"]),
            _step("s2", depends_on=["s1"]),
        ]))


def test_unknown_schema_and_render(tmp_path):
    path = tmp_path / "plan.v2.json"
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(PlanError):
        load_plan(path)
    plan = Plan.from_dict(_plan(
        acceptance_criteria=[{"id": "ac1", "text": "通过", "confirmed": True}],
        steps=[_step("s1"), _step("s2", depends_on=["s1"], kind="edit", risk="write")],
    ))
    text = render_plan(plan)
    assert "计划：修复问题" in text
    assert "[已确认]" in text
    assert "s2" in text and "depends=s1" in text


def test_plan_path_layout(tmp_path):
    assert plan_path(tmp_path, 3) == tmp_path / "plan" / "plan.v3.json"


def _accepted_plan():
    return Plan.from_dict(_plan(
        acceptance_criteria=[{"id": "ac1", "text": "通过", "confirmed": False}],
    ))


def test_plan_approval_gate_and_audit(tmp_path):
    from app.runtime.plan import (
        approve_plan, can_execute, content_hash, propose_plan, reject_plan,
    )

    plan = _accepted_plan()
    assert not can_execute(plan)
    propose_plan(tmp_path, plan)
    assert plan.status == "proposed" and not can_execute(plan)
    approve_plan(tmp_path, plan, approved_by="user", approval_id="a-1")
    assert plan.status == "approved" and can_execute(plan)
    assert plan.approval["plan_hash"] == content_hash(plan)
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["plan_proposed", "plan_approved"]
    assert events[-1]["approver"] == "user"

    rejected = _accepted_plan()
    rejected_dir = tmp_path / "another-task"
    propose_plan(rejected_dir, rejected)
    reject_plan(rejected_dir, rejected, rejected_by="user", reason="范围太大")
    assert rejected.status == "draft" and not can_execute(rejected)


def test_plan_approval_requires_capability_and_proposed(tmp_path):
    from app.runtime.plan import approve_plan, propose_plan

    plan = _accepted_plan()
    with pytest.raises(PlanError):
        approve_plan(tmp_path, plan, approved_by="user")
    propose_plan(tmp_path, plan)
    with pytest.raises(PlanError):
        approve_plan(tmp_path, plan, approved_by="user", capability="other")


@pytest.mark.skipif(os.name != "posix", reason="requires flock")
def test_plan_approval_refuses_a_live_process_review_lock(tmp_path):
    import subprocess
    import sys
    from app.runtime.plan import approve_plan, propose_plan

    plan = _accepted_plan()
    path = propose_plan(tmp_path, plan)
    before = path.read_bytes()
    script = """
import fcntl,sys
with open(sys.argv[1], 'a') as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    print('locked', flush=True)
    sys.stdin.read(1)
"""
    with subprocess.Popen([sys.executable, "-c", script, str(path.parent / "review.lock")],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as owner:
        try:
            assert owner.stdout.readline().strip() == "locked"
            with pytest.raises(PlanError, match="busy"):
                approve_plan(tmp_path, plan, approved_by="user")
            assert path.read_bytes() == before
            assert plan.status == "proposed"
        finally:
            owner.communicate("x", timeout=5)
    approve_plan(tmp_path, plan, approved_by="user")
    assert load_plan(path).status == "approved"


@pytest.mark.skipif(os.name != "posix", reason="requires flock")
@pytest.mark.parametrize("action", ["save", "propose", "reject", "revise", "feedback", "dismiss", "stale"])
def test_all_plan_writers_share_review_lock(tmp_path, action):
    from concurrent.futures import ThreadPoolExecutor
    from app.runtime.plan import mark_plan_stale, plan_lock, propose_plan, reject_plan, revise_plan
    from app.runtime.plan_review import dismiss, request_changes

    plan = _accepted_plan()
    path = propose_plan(tmp_path, plan)
    before = path.read_bytes()
    writes = {
        "save": lambda: save_plan(tmp_path, plan),
        "propose": lambda: propose_plan(tmp_path, plan),
        "reject": lambda: reject_plan(tmp_path, plan, rejected_by="user"),
        "revise": lambda: revise_plan(tmp_path, plan, objective="修订"),
        "feedback": lambda: request_changes(tmp_path, plan, "补测试"),
        "dismiss": lambda: dismiss(tmp_path, plan),
        "stale": lambda: mark_plan_stale(tmp_path, plan),
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        with plan_lock(tmp_path):
            with pytest.raises(PlanError, match="busy"):
                pool.submit(writes[action]).result(timeout=5)
    assert path.read_bytes() == before
    assert not plan_path(tmp_path, 2).exists()
    assert plan.status == "proposed"


@pytest.mark.skipif(os.name != "posix", reason="requires flock")
def test_nested_review_lock_survives_inner_exception_and_releases_on_exit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.runtime.plan import approve_plan, plan_lock, propose_plan

    plan = _accepted_plan()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with plan_lock(tmp_path):
            path = propose_plan(tmp_path, plan)
            with pytest.raises(RuntimeError):
                with plan_lock(tmp_path):
                    raise RuntimeError("inner operation failed")
            with pytest.raises(PlanError, match="busy"):
                pool.submit(approve_plan, tmp_path, plan, approved_by="other").result(timeout=5)
        pool.submit(approve_plan, tmp_path, plan, approved_by="user").result(timeout=5)
    assert load_plan(path).approval["approved_by"] == "user"


@pytest.mark.parametrize("change", ["content", "state"])
@pytest.mark.parametrize("action", ["approve", "propose", "reject", "revise", "feedback", "dismiss", "save", "stale"])
def test_plan_mutation_never_overwrites_newer_disk_state(tmp_path, action, change):
    from app.runtime.plan import approve_plan, mark_plan_stale, propose_plan, reject_plan, revise_plan
    from app.runtime.plan_review import dismiss, request_changes

    plan = _accepted_plan()
    path = propose_plan(tmp_path, plan)
    old = load_plan(path)
    if change == "content":
        payload = json.loads(path.read_text())
        payload["objective"] = "另一处保存的新内容"
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        reject_plan(tmp_path, plan, rejected_by="user")
    before = path.read_bytes()
    audit = (tmp_path / "audit.jsonl").read_bytes()
    actions = {
        "approve": lambda: approve_plan(tmp_path, old, approved_by="user"),
        "propose": lambda: propose_plan(tmp_path, old),
        "reject": lambda: reject_plan(tmp_path, old, rejected_by="user"),
        "revise": lambda: revise_plan(tmp_path, old, objective="过时对象的修订"),
        "feedback": lambda: request_changes(tmp_path, old, "过时反馈"),
        "dismiss": lambda: dismiss(tmp_path, old),
        "save": lambda: save_plan(tmp_path, old),
        "stale": lambda: mark_plan_stale(tmp_path, old),
    }
    with pytest.raises(PlanError, match="changed|exists"):
        actions[action]()
    assert path.read_bytes() == before
    assert (tmp_path / "audit.jsonl").read_bytes() == audit
    assert not plan_path(tmp_path, 2).exists()
    assert old.status == "proposed"


def test_plan_save_does_not_follow_or_delete_an_existing_temporary_symlink(tmp_path):
    victim = tmp_path / "unrelated.txt"
    victim.write_text("keep me")
    directory = tmp_path / "plan"
    directory.mkdir()
    other_temp = directory / "plan.v1.json.tmp"
    other_temp.symlink_to(victim)
    plan = _accepted_plan()
    path = save_plan(tmp_path, plan)
    assert victim.read_text() == "keep me"
    assert other_temp.is_symlink()
    assert not path.is_symlink()
    assert load_plan(path).objective == plan.objective


def test_plan_save_rejects_an_artifact_its_reader_cannot_load(tmp_path):
    plan = Plan.from_dict(_plan(constraints=["x" * (4 * 1024 * 1024)]))
    with pytest.raises(PlanError, match="4 MiB"):
        save_plan(tmp_path, plan)
    assert not plan_path(tmp_path, 1).exists()


def test_failed_approval_write_preserves_file_and_in_memory_state(tmp_path, monkeypatch):
    import os
    from app.runtime.plan import approve_plan, propose_plan

    plan = _accepted_plan()
    path = propose_plan(tmp_path, plan)
    before = path.read_bytes()
    replace_file = os.replace

    def disk_full(source, destination):
        if destination == path:
            raise OSError("disk full")
        return replace_file(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", disk_full)
        with pytest.raises(OSError, match="disk full"):
            approve_plan(tmp_path, plan, approved_by="user")
    assert path.read_bytes() == before
    assert plan.status == "proposed" and plan.approval == {}
    assert not list(path.parent.glob("*.tmp"))
    approve_plan(tmp_path, plan, approved_by="user")
    assert load_plan(path).status == "approved"


@pytest.mark.skipif(os.name != "posix", reason="requires flock")
def test_review_lock_is_released_after_owner_process_dies(tmp_path):
    import subprocess
    import sys
    from app.runtime.plan import approve_plan, propose_plan

    plan = _accepted_plan()
    path = propose_plan(tmp_path, plan)
    lock_path = path.parent / "review.lock"
    inode = lock_path.stat().st_ino
    script = """
import sys
from app.runtime.plan import plan_lock
with plan_lock(sys.argv[1], require_process_lock=True):
    print('locked', flush=True)
    sys.stdin.read(1)
"""
    with subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as owner:
        try:
            assert owner.stdout.readline().strip() == "locked"
            with pytest.raises(PlanError, match="busy"):
                approve_plan(tmp_path, plan, approved_by="user")
        finally:
            owner.kill()
            owner.wait(timeout=5)
    approve_plan(tmp_path, plan, approved_by="user")
    assert load_plan(path).status == "approved"
    assert lock_path.stat().st_ino == inode


def test_new_review_actions_require_process_lock_but_legacy_creation_remains(tmp_path, monkeypatch):
    from app.runtime import plan as module

    monkeypatch.setattr(module, "fcntl", None)
    with pytest.raises(PlanError) as caught:
        with module.plan_lock(tmp_path, require_process_lock=True):
            pytest.fail("unsupported action must not run")
    assert caught.value.code == "unsupported_platform"
    assert not (tmp_path / "plan").exists()
    path = module.propose_plan(tmp_path, _accepted_plan())
    assert load_plan(path).status == "proposed"


def test_repeating_a_revision_cannot_overwrite_an_existing_version(tmp_path):
    from app.runtime.plan import propose_plan, revise_plan

    plan = _accepted_plan()
    propose_plan(tmp_path, plan)
    revised = revise_plan(tmp_path, plan, objective="已保存的修订")
    before = plan_path(tmp_path, 2).read_bytes()
    with pytest.raises(PlanError, match="exists"):
        revise_plan(tmp_path, plan, objective="另一处的修订")
    assert plan_path(tmp_path, 2).read_bytes() == before
    assert load_plan(plan_path(tmp_path, 2)).objective == revised.objective


def test_plan_content_changed_after_approval_is_not_executable(tmp_path):
    from app.runtime.plan import approve_plan, can_execute, propose_plan

    plan = _accepted_plan()
    propose_plan(tmp_path, plan)
    approve_plan(tmp_path, plan, approved_by="user")
    plan.objective = "偷偷扩大范围"
    assert not can_execute(plan)


def test_submit_plan_tool_writes_proposed_plan(tmp_path):
    import asyncio

    from app.tools.registry import ToolRegistry
    from core.contracts import ToolCall

    registry = ToolRegistry(tmp_path)
    task_dir = tmp_path / ".vibe" / "sessions" / "s" / "tasks" / "t-20261106-000000-abcdef"
    task_dir.mkdir(parents=True)
    registry.register_plan_tool(task_dir)
    ok, text = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "计划目标",
        "steps": [{"step_id": "s1", "title": "读代码", "kind": "read"}],
    })))
    assert ok and "plan proposed" in text
    plan_file = task_dir / "plan" / "plan.v1.json"
    assert plan_file.exists()
    plan = load_plan(plan_file)
    assert plan.status == "proposed" and plan.task_id == "t-20261106-000000-abcdef"
    assert "plan_proposed" in (task_dir / "audit.jsonl").read_text(encoding="utf-8")
    registry.unregister("submit_plan")


@pytest.mark.parametrize("kind,risk,expected", [
    ("read", None, "read"),
    ("edit", None, "write"),
    ("run", None, "exec"),
    ("verify", None, "exec"),
    ("edit", "read", "write"),
    ("run", "write", "exec"),
    ("read", "network", "network"),
])
def test_submitted_plan_review_risk_covers_step_kind(tmp_path, kind, risk, expected):
    import asyncio
    from app.tools.registry import ToolRegistry
    from core.contracts import ToolCall

    registry = ToolRegistry(tmp_path)
    task_dir = tmp_path / ".vibe/sessions/s/tasks/t-20261106-000000-abcdef"
    task_dir.mkdir(parents=True)
    registry.register_plan_tool(task_dir)
    step = {"step_id": "s1", "title": "操作 message.txt", "kind": kind}
    if risk is not None:
        step["risk"] = risk
    ok, result = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "检查审阅风险", "steps": [step],
    })))
    assert ok, result
    plan = load_plan(task_dir / "plan/plan.v1.json")
    assert plan.steps[0].risk == expected
    assert f"{kind}/{expected}" in render_plan(plan)


def test_revision_submission_publishes_same_plan_for_fresh_review(tmp_path):
    import asyncio
    from app.runtime.plan import approve_plan, can_execute, content_hash, propose_plan
    from app.tools.registry import ToolRegistry
    from core.contracts import ToolCall

    base = Plan.from_dict(_plan(
        steps=[_step("s1", title="读取文件")],
        acceptance_criteria=[{"id": "ac1", "text": "检查内容", "confirmed": True}],
        verification_plan=[{"id": "v1", "covers": ["ac1"], "command": "pytest -q", "source": "plan_approval"}],
        constraints=["只改 message.txt"], out_of_scope=["不部署"],
        goal_id="goal-1", source={"mode": "plan"},
    ))
    task_dir = tmp_path / ".vibe/sessions/s/tasks" / base.task_id
    original = propose_plan(task_dir, base)
    approve_plan(task_dir, base, approved_by="user")
    before = original.read_bytes()
    registry = ToolRegistry(tmp_path)
    registry.register_plan_tool(task_dir, revision=base, feedback="增加中文内容检查", revision_id="a" * 64)
    ok, result = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "检查中文内容", "steps": [{"step_id": "s1", "title": "读取中文内容", "kind": "read"}],
        "open_questions": ["是否覆盖空文件？"],
    })))
    assert ok, result
    revised = load_plan(plan_path(task_dir, 2))
    assert (revised.plan_id, revised.task_id, revised.version) == (base.plan_id, base.task_id, 2)
    assert revised.status == "proposed" and revised.approval == {} and not can_execute(revised)
    assert revised.objective == "检查中文内容" and revised.steps[0].title == "读取中文内容"
    assert revised.acceptance_criteria == base.acceptance_criteria
    assert revised.verification_plan == base.verification_plan
    assert revised.constraints == ["只改 message.txt"] and revised.out_of_scope == ["不部署"]
    assert revised.open_questions == ["是否覆盖空文件？"] and revised.goal_id == "goal-1"
    assert revised.review_feedback == ["增加中文内容检查"]
    assert revised.source["revision"]["id"] == "a" * 64
    assert revised.source["revision"]["base_hash"] == content_hash(base)
    assert original.read_bytes() == before
    assert not (task_dir / "plan/execution.v2.json").exists()
    assert not (task_dir / "acceptance.v2.json").exists()


def test_revision_retry_returns_the_already_published_version(tmp_path):
    import asyncio
    from app.runtime.plan import propose_plan
    from app.tools.registry import ToolRegistry
    from core.contracts import ToolCall

    base = Plan.from_dict(_plan())
    task_dir = tmp_path / ".vibe/sessions/s/tasks" / base.task_id
    propose_plan(task_dir, base)
    registry = ToolRegistry(tmp_path)
    registry.register_plan_tool(task_dir, revision=base, feedback="补充检查", revision_id="b" * 64)
    ok, text = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "第一次已提交", "steps": [_step()],
    })))
    assert ok, text
    published = plan_path(task_dir, 2).read_bytes()
    restarted = ToolRegistry(tmp_path)
    restarted.register_plan_tool(task_dir, revision=load_plan(plan_path(task_dir, 1)),
                                 feedback="补充检查", revision_id="b" * 64)
    ok, text = asyncio.run(restarted.execute_async(ToolCall("submit_plan", {
        "objective": "回执丢失后模型再提交的不同内容", "steps": [_step()],
    })))
    assert ok and "v2" in text
    assert plan_path(task_dir, 2).read_bytes() == published
    assert not plan_path(task_dir, 3).exists()


def test_revision_submission_cannot_fill_a_gap_below_a_newer_version(tmp_path):
    import asyncio
    from app.runtime.plan import propose_plan
    from app.tools.registry import ToolRegistry
    from core.contracts import ToolCall

    base = Plan.from_dict(_plan())
    task_dir = tmp_path / ".vibe/sessions/s/tasks" / base.task_id
    propose_plan(task_dir, base)
    newer = Plan.from_dict({**base.to_dict(), "version": 3, "objective": "已经存在的新版"})
    newest_path = propose_plan(task_dir, newer)
    before = newest_path.read_bytes()
    registry = ToolRegistry(tmp_path)
    registry.register_plan_tool(task_dir, revision=base, feedback="基于旧版的反馈", revision_id="c" * 64)
    ok, result = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "不应发布", "steps": [_step()],
    })))
    assert not ok, result
    assert not plan_path(task_dir, 2).exists()
    assert newest_path.read_bytes() == before


@pytest.mark.parametrize("status", ["executing", "superseded", "legacy"])
def test_revision_submission_rejects_nonreviewable_source_status(tmp_path, status):
    import asyncio
    from app.tools.registry import ToolRegistry
    from core.contracts import ToolCall

    base = Plan.from_dict(_plan(status=status))
    task_dir = tmp_path / ".vibe/sessions/s/tasks" / base.task_id
    save_plan(task_dir, base)
    registry = ToolRegistry(tmp_path)
    registry.register_plan_tool(task_dir, revision=base, feedback="修改", revision_id="d" * 64)
    ok, result = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "不应发布", "steps": [_step()],
    })))
    assert not ok, result
    assert not plan_path(task_dir, 2).exists()


def test_revision_submission_rejects_a_copied_source_in_another_task(tmp_path):
    import asyncio
    from app.tools.registry import ToolRegistry
    from core.contracts import ToolCall

    base = Plan.from_dict(_plan(status="proposed"))
    wrong_dir = tmp_path / ".vibe/sessions/s/tasks/t-20261106-000001-fedcba"
    original = save_plan(wrong_dir, base)
    before = original.read_bytes()
    registry = ToolRegistry(tmp_path)
    registry.register_plan_tool(wrong_dir, revision=base, feedback="修改", revision_id="e" * 64)
    ok, result = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "不应发布", "steps": [_step()],
    })))
    assert not ok, result
    assert original.read_bytes() == before
    assert not plan_path(wrong_dir, 2).exists()


@pytest.fixture
def revision_tool(tmp_path):
    from app.runtime.plan import propose_plan
    from app.tools.registry import ToolRegistry

    base = Plan.from_dict(_plan())
    task_dir = tmp_path / ".vibe/sessions/s/tasks" / base.task_id
    propose_plan(task_dir, base)
    registry = ToolRegistry(tmp_path)
    registry.register_plan_tool(task_dir, revision=base, feedback="补充检查", revision_id="f" * 64)
    return base, task_dir, registry


@pytest.mark.parametrize("override", [
    {"plan_id": "model-selected"}, {"task_id": "model-selected"},
    {"version": 9}, {"approval": {"approved_by": "model"}},
])
def test_revision_model_cannot_choose_identity_or_approval(revision_tool, override):
    import asyncio
    from core.contracts import ToolCall

    _, task_dir, registry = revision_tool
    before = plan_path(task_dir, 1).read_bytes()
    ok, result = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "修改", "steps": [_step()], **override,
    })))
    assert not ok, result
    assert plan_path(task_dir, 1).read_bytes() == before
    assert not plan_path(task_dir, 2).exists()


def test_revision_source_changed_after_registration_is_not_published(revision_tool):
    import asyncio
    from core.contracts import ToolCall

    base, task_dir, registry = revision_tool
    changed = Plan.from_dict({**base.to_dict(), "objective": "外部修改了原版"})
    save_plan(task_dir, changed, expected=base)
    before = plan_path(task_dir, 1).read_bytes()
    ok, result = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "不应覆盖", "steps": [_step()],
    })))
    assert not ok, result
    assert plan_path(task_dir, 1).read_bytes() == before
    assert not plan_path(task_dir, 2).exists()


@pytest.mark.parametrize("failure", ["replace", "audit"])
def test_revision_storage_failure_keeps_old_plan_and_recovers_same_output(revision_tool, monkeypatch, failure):
    import asyncio
    from core.contracts import ToolCall

    _, task_dir, registry = revision_tool
    before = plan_path(task_dir, 1).read_bytes()
    call = ToolCall("submit_plan", {"objective": "修改后的内容", "steps": [_step()]})
    real_replace, real_open = os.replace, os.open

    def fail_replace(source, destination, *args, **kwargs):
        if str(destination) == str(plan_path(task_dir, 2)):
            raise OSError("injected replace failure")
        return real_replace(source, destination, *args, **kwargs)

    def fail_open(path, flags, *args, **kwargs):
        if str(path) == str(task_dir / "audit.jsonl") and flags & os.O_APPEND:
            raise OSError("injected audit failure")
        return real_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace" if failure == "replace" else "open", fail_replace if failure == "replace" else fail_open)
        ok, result = asyncio.run(registry.execute_async(call))
        assert not ok, result
    assert plan_path(task_dir, 1).read_bytes() == before
    assert plan_path(task_dir, 2).exists() is (failure == "audit")
    ok, result = asyncio.run(registry.execute_async(call))
    assert ok, result
    assert load_plan(plan_path(task_dir, 2)).objective == "修改后的内容"
    assert not plan_path(task_dir, 3).exists()
    events = [json.loads(line) for line in (task_dir / "audit.jsonl").read_text().splitlines()]
    assert any(event["event"] in {"plan_proposed", "plan_revision_reused"}
               and event["version"] == 2 for event in events)


def test_revision_different_operation_cannot_replace_published_output(revision_tool):
    import asyncio
    from core.contracts import ToolCall

    base, task_dir, registry = revision_tool
    call = ToolCall("submit_plan", {"objective": "保留原修订", "steps": [_step()]})
    assert asyncio.run(registry.execute_async(call))[0]
    before = plan_path(task_dir, 2).read_bytes()
    registry.register_plan_tool(task_dir, revision=base, feedback="另外的反馈", revision_id="1" * 64)
    ok, result = asyncio.run(registry.execute_async(call))
    assert not ok, result
    assert plan_path(task_dir, 2).read_bytes() == before


def test_revision_feedback_is_rejected_instead_of_silently_truncated(revision_tool):
    import asyncio
    from core.contracts import ToolCall

    base, task_dir, registry = revision_tool
    registry.register_plan_tool(task_dir, revision=base, feedback="中" * 4001, revision_id="2" * 64)
    ok, result = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "修改", "steps": [_step()],
    })))
    assert not ok and "4000" in result
    assert not plan_path(task_dir, 2).exists()


def test_submit_plan_schema_exposes_optional_step_risk(tmp_path):
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(tmp_path)
    registry.register_plan_tool(tmp_path / "task")
    spec = next(s["function"] for s in registry.specs() if s["function"]["name"] == "submit_plan")
    step = spec["parameters"]["properties"]["steps"]["items"]
    assert set(step["properties"]["risk"]["enum"]) == {"read", "write", "exec", "network"}
    assert "risk" not in step["required"]


def test_approval_gate_plan_execution_gate_denies():
    import asyncio
    from app.safety.policy import ApprovalGate
    from core.contracts import ToolCall

    gate = ApprovalGate()
    gate.execution_gate = lambda call: "plan_not_approved"
    decision = asyncio.run(gate.request(ToolCall("write_file", {"path": "x", "content": "x"}), "preview"))
    assert decision.allowed is False and decision.scope == "deny"
    assert gate.decisions_for(ToolCall("bash", {"command": "echo x"})) == ["deny"]


def test_find_plan_and_can_execute(tmp_path):
    from app.runtime.plan import find_plan, propose_plan, approve_plan

    task_dir = tmp_path / "tasks" / "t-20261106-000000-abcdef"
    plan = Plan.from_dict(_plan())
    propose_plan(task_dir, plan)
    found = find_plan(tmp_path, plan.plan_id)
    assert found is not None and found[0] == task_dir
    approve_plan(task_dir, found[1], approved_by="user")
    found = find_plan(tmp_path, plan.plan_id, 1)
    assert found is not None


def test_plan_acceptance_from_confirmed_criteria():
    from app.runtime.acceptance import PlanAcceptance

    plan = Plan.from_dict(_plan(
        acceptance_criteria=[{"id": "ac1", "text": "通过", "confirmed": True}],
        verification_plan=[{"id": "v1", "covers": ["ac1"], "command": "true",
                            "source": "plan_approval"}],
    ))
    acceptance = plan.acceptance()
    assert isinstance(acceptance, PlanAcceptance)
    assert acceptance.status({"v1": {"ok": True}})[0] is True
    unconfirmed = Plan.from_dict(_plan(acceptance_criteria=[
        {"id": "ac1", "text": "通过", "confirmed": False},
    ]))
    assert unconfirmed.acceptance() is None


def test_revise_plan_creates_new_draft_version(tmp_path):
    from app.runtime.plan import approve_plan, content_hash, propose_plan, revise_plan

    old = Plan.from_dict(_plan())
    propose_plan(tmp_path, old)
    approve_plan(tmp_path, old, approved_by="user")
    old_hash = content_hash(old)
    revised = revise_plan(tmp_path, old, objective="新目标",
                          steps=[{"step_id": "s1", "title": "新步骤", "kind": "read"}])
    assert revised.version == 2 and revised.status == "draft"
    assert revised.objective == "新目标" and not revised.approval
    assert revised.plan_id == old.plan_id
    assert (tmp_path / "plan" / "plan.v1.json").exists()
    assert load_plan(tmp_path / "plan" / "plan.v2.json").objective == "新目标"
    # 旧版本内容未被改写
    assert content_hash(load_plan(tmp_path / "plan" / "plan.v1.json")) == old_hash
    events = [json.loads(line)["event"] for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "plan_revised" in events


def test_steering_marks_approved_plan_stale(tmp_path):
    from app.runtime.plan import approve_plan, can_execute, propose_plan

    plan = Plan.from_dict(_plan())
    propose_plan(tmp_path, plan)
    approve_plan(tmp_path, plan, approved_by="user")
    assert can_execute(plan)
    plan.approval = {**plan.approval, "stale": True}
    assert not can_execute(plan)


def test_approve_plan_respects_review_timeout(tmp_path, monkeypatch):
    import time
    from app.runtime.plan import PlanError, approve_plan, propose_plan

    plan = Plan.from_dict(_plan())
    propose_plan(tmp_path, plan)
    monkeypatch.setenv("VIBE_PLAN_REVIEW_TIMEOUT_SECONDS", "0.05")
    time.sleep(0.08)
    with pytest.raises(PlanError, match="review window expired"):
        approve_plan(tmp_path, plan, approved_by="user")


def test_plan_verification_plan_validation():
    from app.runtime.plan import Plan, PlanError

    base = dict(_plan())
    base["acceptance_criteria"] = [{"id": "ac1", "text": "通过", "confirmed": True}]
    base["verification_plan"] = [{"id": "v1", "covers": ["ac1"], "command": "true",
                                  "source": "plan_approval"}]
    assert Plan.from_dict(base).verification_plan[0]["id"] == "v1"
    with pytest.raises(PlanError, match="invalid source"):
        bad = dict(base); bad["verification_plan"] = [{"id": "v1", "covers": ["ac1"], "command": "true", "source": "model"}]
        Plan.from_dict(bad)
    with pytest.raises(PlanError, match="unknown criteria"):
        bad = dict(base); bad["verification_plan"] = [{"id": "v1", "covers": ["nope"], "command": "true", "source": "plan_approval"}]
        Plan.from_dict(bad)
    with pytest.raises(PlanError, match="nonempty string"):
        bad = dict(base); bad["verification_plan"] = [{"id": "v1", "covers": ["ac1"], "command": "", "source": "plan_approval"}]
        Plan.from_dict(bad)
    with pytest.raises(PlanError, match="exceeds 50"):
        bad = dict(base); bad["verification_plan"] = [
            {"id": f"v{i}", "covers": ["ac1"], "command": "true", "source": "plan_approval"}
            for i in range(51)
        ]
        Plan.from_dict(bad)
    with pytest.raises(PlanError, match="command too long"):
        bad = dict(base); bad["verification_plan"] = [{"id": "v1", "covers": ["ac1"], "command": "x" * 4001, "source": "plan_approval"}]
        Plan.from_dict(bad)


def test_submit_plan_normalizes_model_friendly_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    from app.tools.registry import ToolRegistry
    from app.session.store import SessionStore
    from app.runtime.plan import load_plan

    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_dir = session.root / "tasks" / "t-20261106-000000-abcdef"
    task_dir.mkdir(parents=True)
    registry = ToolRegistry(tmp_path)
    registry.register_plan_tool(task_dir)
    fn = registry._tools["submit_plan"].fn
    result = fn(objective="目标", steps=["读 README", {"title": "分析", "kind": "read"}],
                acceptance_criteria=["说明用途"])
    assert "plan proposed" in result
    plan = load_plan(task_dir / "plan" / "plan.v1.json")
    assert [step.step_id for step in plan.steps] == ["s1", "s2"]
    assert plan.steps[0].title == "读 README"
    assert plan.steps[1].title == "分析"
    assert plan.acceptance_criteria[0]["confirmed"] is False
