from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.runtime.agent import AgentResult
from app.runtime.plan import Plan, PlanError, content_hash, load_plan, plan_path, propose_plan
from app.runtime.plan_review import PlanReview
from app.runtime.task_runtime import TaskRuntime
from app.runtime.task_runtime import TaskRuntimeError
from app.runtime.tasks import TaskStore


def _setup(tmp_path):
    from app.runtime.plan_revision import PlanRevision

    root = tmp_path / '.vibe/sessions/session-1'
    store = TaskStore(root)
    source = store.create(kind='run', mode='plan', objective='Read the file', workspace=str(tmp_path))
    plan = Plan.from_dict({'plan_id': 'pl-1', 'task_id': source.task_id,
                          'objective': 'Read the file', 'steps': [{'step_id': 's1', 'title': 'Read'}]})
    path = propose_plan(store.task_dir(source.task_id), plan)
    runtime = TaskRuntime(store, workspace=tmp_path)
    ref = PlanReview(root, session_id=root.name, task_runtime=runtime).read_review()['target']
    revisions = PlanRevision(root, session_id=root.name, task_runtime=runtime)
    return store, runtime, revisions, ref, plan, path


def test_revision_request_creates_one_bound_plan_run_and_query_is_readonly(tmp_path):
    from app.runtime.plan_revision import PlanRevision

    store, runtime, revisions, ref, plan, path = _setup(tmp_path)
    before = path.read_bytes()
    missing = revisions.read(ref, 'Check empty files')
    assert missing['outcome'] == 'absent'
    assert len(store.list()) == 1
    first = revisions.request(ref, 'Check empty files', 'revision-1')
    assert first.run_task is not None, first.receipt
    assert first.receipt['outcome'] == 'running' and first.receipt['task']['run_started'] is True
    task = store.get(first.run_task.task_id)
    assert task.mode == 'plan' and task.parent_id == plan.task_id
    assert task.plan_id == plan.plan_id and task.plan_version == 1
    assert task.extra['creation']['target']['source'] == ref
    assert task.extra['creation']['target']['feedback'] == 'Check empty files'
    assert path.read_bytes() == before and not plan_path(path.parent.parent, 2).exists()

    reopened = PlanRevision(store.root, session_id=store.root.name, task_runtime=runtime)
    files = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    result = reopened.read(ref, 'Check empty files')
    assert result['task']['task_id'] == task.task_id and result['task']['run_started'] is False
    assert files == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    retry = reopened.request(ref, 'Check empty files', 'revision-retry')
    assert retry.run_task is None and retry.receipt['task']['task_id'] == task.task_id
    assert len(store.list()) == 2 and len(store.get(task.task_id).segments) == 1
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))


def test_revision_history_recovers_feedback_without_knowing_it_or_writing(tmp_path):
    from app.runtime.plan_revision import PlanRevision

    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    first = revisions.request(ref, '保留中文反馈', 'revision-1')
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))
    files = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    reopened = PlanRevision(store.root, session_id=store.root.name, task_runtime=runtime)
    result = reopened.history(ref)
    assert result['target'] == ref and result['code'] is None and result['next_cursor'] is None
    assert len(result['records']) == 1
    record = result['records'][0]
    assert record['feedback'] == '保留中文反馈' and record['target'] == ref
    assert record['task']['task_id'] == first.run_task.task_id
    assert record['task']['segment'] == 1 and record['task']['run_started'] is False
    assert record['outcome'] == 'paused'
    assert files == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}


def test_revision_history_pages_immutable_ids_and_keeps_original_sources_after_publication(tmp_path):
    from app.runtime.plan import propose_revision

    store, runtime, revisions, ref, plan, path = _setup(tmp_path)
    decisions = []
    for index in range(12):
        decision = revisions.request(ref, f'反馈 {index}', f'request-{index}')
        runtime.finish_run(decision.run_task, AgentResult('cancelled', 0, 'cancelled'))
        decisions.append(decision)
    last = decisions[-1]
    propose_revision(path.parent.parent, plan, content={'objective': 'Revised', 'steps': [{'step_id': 's1', 'title': 'Read'}]},
                                feedback='反馈 11', revision_id=last.receipt['revision_id'])
    current_ref = revisions.review.read_review()['target']
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    first = revisions.history(current_ref)
    assert first['code'] is None and len(first['records']) == 10 and first['next_cursor']
    second = revisions.history(current_ref, first['next_cursor'])
    assert second['code'] is None and len(second['records']) == 2 and second['next_cursor'] is None
    records = first['records'] + second['records']
    assert {item['feedback'] for item in records} == {f'反馈 {i}' for i in range(12)}
    assert all(item['target'] == ref and (not item['task'] or not item['task']['run_started']) for item in records)
    order = [decision.receipt['feedback'] for decision in sorted(decisions, key=lambda item: item.receipt['revision_id'])]
    assert [item['feedback'] for item in records] == order
    assert sum(item['code'] == 'superseded' for item in records) == 11
    submitted, = [item for item in records if item['outcome'] == 'submitted']
    assert submitted['result_target'] == current_ref
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['family', 'json', 'oversize', 'symlink'])
def test_revision_history_reports_corrupt_or_foreign_records_without_empty_success(tmp_path, damage):
    store, runtime, revisions, ref, _, path = _setup(tmp_path)
    first = revisions.request(ref, '不能丢失的反馈', 'revision')
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))
    operation, = (path.parent / 'review-revisions').glob('*.json')
    if damage == 'family':
        data = json.loads(operation.read_text())
        data['target']['source']['plan_id'] = 'other-plan'
        operation.write_text(json.dumps(data))
    elif damage == 'symlink':
        outside = tmp_path / 'outside.json'
        outside.write_text('PRIVATE SENTINEL')
        operation.unlink(); operation.symlink_to(outside)
    else:
        operation.write_text('!' if damage == 'json' else ' ' * 65537)
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    result = revisions.history(ref)
    assert result['code'] and result['records'] == [] and result['next_cursor'] is None
    assert 'PRIVATE SENTINEL' not in result['message']
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}


def test_revision_context_publishes_bound_output_and_retry_never_starts_again(tmp_path):
    import asyncio
    from app.tools.registry import ToolRegistry
    from core.contracts import ToolCall

    store, runtime, revisions, ref, plan, path = _setup(tmp_path)
    decision = revisions.request(ref, 'Check empty files', 'revision-1')
    context = revisions.context(decision.run_task)
    assert content_hash(context.plan) == ref['plan_hash']
    assert context.feedback == 'Check empty files' and context.task_dir == path.parent.parent
    tools = ToolRegistry(tmp_path)
    tools.register_plan_tool(context.task_dir, revision=context.plan,
                             feedback=context.feedback, revision_id=context.revision_id)
    ok, result = asyncio.run(tools.execute_async(ToolCall('submit_plan', {
        'objective': 'Read and check empty files', 'steps': [{'step_id': 's1', 'title': 'Check empty files'}],
    })))
    assert ok, result
    runtime.finish_run(decision.run_task, AgentResult('response lost', 1, 'stopped'))
    files = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    receipt = revisions.read(ref, 'Check empty files')
    assert receipt['outcome'] == 'submitted'
    assert receipt['result_target']['plan_id'] == plan.plan_id and receipt['result_target']['plan_version'] == 2
    assert receipt['result_target']['plan_hash'] == content_hash(load_plan(plan_path(context.task_dir, 2)))
    assert files == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    retry = revisions.request(ref, 'Check empty files', 'retry')
    assert retry.run_task is None and retry.receipt['outcome'] == 'submitted'
    assert len(store.get(decision.run_task.task_id).segments) == 1


def test_different_feedback_cannot_start_another_active_revision(tmp_path):
    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    first = revisions.request(ref, 'First feedback', 'first')
    second = revisions.request(ref, 'Different feedback', 'second')
    assert second.run_task is None and second.receipt['code'] == 'revision_busy'
    assert len(store.list()) == 2
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))
    later = revisions.request(ref, 'Different feedback', 'later')
    assert later.run_task is not None and later.run_task.task_id != first.run_task.task_id
    runtime.finish_run(later.run_task, AgentResult('cancelled', 0, 'cancelled'))


def test_active_execution_blocks_revision_and_active_revision_blocks_review(tmp_path):
    store, runtime, revisions, ref, _, path = _setup(tmp_path)
    review = PlanReview(store.root, session_id=store.root.name, task_runtime=runtime)
    execution = review.decide_review(ref, 'approve-execute', 'execute')
    assert execution.run_task is not None
    rejected = revisions.request(ref, 'Change scope', 'revise-during-execution')
    assert rejected.run_task is None and rejected.receipt['code'] == 'plan_busy'
    runtime.finish_run(execution.run_task, AgentResult('done', 0, 'done'))
    revision = revisions.request(ref, 'Change scope', 'revise-after-execution')
    assert revision.run_task is not None
    # Another proposed plan isolates the approval side from the already-launched execution.
    other = Plan.from_dict({'plan_id': 'pl-2', 'task_id': store.reserve_task_id(),
                           'objective': 'Another plan', 'steps': [{'step_id': 's1', 'title': 'Read'}]})
    other_path = propose_plan(store.task_dir(other.task_id), other)
    other_ref = review.read_review()['target']
    pending = revisions.request(other_ref, 'Change this plan', 'other-revision')
    assert pending.run_task is not None
    before = other_path.read_bytes()
    approval = review.decide_review(other_ref, 'approve-only', 'approve-during-revision')
    assert approval.receipt['code'] == 'revision_busy'
    assert other_path.read_bytes() == before
    runtime.finish_run(revision.run_task, AgentResult('cancelled', 0, 'cancelled'))
    runtime.finish_run(pending.run_task, AgentResult('cancelled', 0, 'cancelled'))


def test_legacy_runtime_start_cannot_execute_during_revision_or_from_old_version(tmp_path):
    from app.runtime.plan import approve_plan, propose_revision

    store, runtime, revisions, ref, plan, path = _setup(tmp_path)
    plan = load_plan(path)
    approve_plan(path.parent.parent, plan, approved_by='user')
    revision = revisions.request(ref, 'Check empty files', 'revision')
    meta = {'kind': 'run', 'mode': 'act', 'objective': plan.objective,
            'plan_id': plan.plan_id, 'plan_version': plan.version}
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    with pytest.raises(TaskRuntimeError, match='revision_busy'):
        runtime.prepare_run('legacy-execute', meta)
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    context = revisions.context(revision.run_task)
    propose_revision(context.task_dir, context.plan, content={'objective': 'Changed', 'steps': [s.to_dict() for s in plan.steps]},
                     feedback=context.feedback, revision_id=context.revision_id)
    runtime.finish_run(revision.run_task, AgentResult('done', 1, 'done'))
    with pytest.raises(TaskRuntimeError, match='superseded'):
        runtime.prepare_run('old-version', meta)
    assert len(store.list()) == 2


def test_background_preparation_reuses_task_and_excludes_revision(tmp_path):
    from app.runtime.plan import approve_plan

    store, runtime, revisions, ref, _, path = _setup(tmp_path)
    approve_plan(path.parent.parent, load_plan(path), approved_by='user')
    review = PlanReview(store.root, session_id=store.root.name, task_runtime=runtime)
    task, reused = review.prepare_background(ref, priority=7)
    assert not reused and task.mode == 'act' and task.priority == 7
    assert task.plan_id == ref['plan_id'] and task.plan_version == ref['plan_version']
    repeated, reused = review.prepare_background(ref, priority=9)
    assert reused and repeated.task_id == task.task_id and repeated.priority == 7
    assert len(store.list()) == 2 and not repeated.segments
    rejected = revisions.request(ref, 'Check empty files', 'revision')
    assert rejected.run_task is None and rejected.receipt['code'] == 'plan_busy'


def test_resume_requires_the_observed_segment_and_never_turns_retry_into_new_run(tmp_path):
    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    first = revisions.request(ref, 'Check empty files', 'first')
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))
    retry = revisions.request(ref, 'Check empty files', 'retry')
    assert retry.run_task is None and retry.receipt['outcome'] == 'paused'
    resumed = revisions.resume(ref, 'Check empty files', 'resume-1', expected_segment=1)
    assert resumed.run_task is not None and resumed.run_task.task_id == first.run_task.task_id
    assert resumed.run_task.segment == 2
    assert revisions.context(resumed.run_task).feedback == 'Check empty files'
    runtime.finish_run(resumed.run_task, AgentResult('cancelled', 0, 'cancelled'))
    duplicate = revisions.resume(ref, 'Check empty files', 'resume-retry', expected_segment=1)
    assert duplicate.run_task is None and duplicate.receipt['task']['segment'] == 2
    assert len(store.get(first.run_task.task_id).segments) == 2


def test_completed_run_without_submission_is_reported_incomplete(tmp_path):
    _, runtime, revisions, ref, _, _ = _setup(tmp_path)
    first = revisions.request(ref, 'Check empty files', 'first')
    runtime.finish_run(first.run_task, AgentResult('No plan submitted', 0, 'done'))
    receipt = revisions.read(ref, 'Check empty files')
    assert receipt['outcome'] == 'incomplete' and receipt['code'] == 'revision_not_submitted'
    assert receipt['result_target'] is None
    assert revisions.request(ref, 'Check empty files', 'retry').run_task is None


def test_lost_start_receipt_hands_off_once_and_allows_explicit_resume(tmp_path, monkeypatch):
    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    replace = os.replace

    def fail_receipt(source, destination, *args, **kwargs):
        if Path(destination).parent.name == 'review-revisions':
            if json.loads(Path(source).read_text())['stage'] == 'started':
                raise OSError('receipt save failed')
        return replace(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', fail_receipt)
        first = revisions.request(ref, 'Check empty files', 'first')
    assert first.run_task is not None and first.receipt['code'] == 'storage_error'
    assert revisions.request(ref, 'Check empty files', 'retry').run_task is None
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))
    resumed = revisions.resume(ref, 'Check empty files', 'resume', expected_segment=1)
    assert resumed.run_task is not None, resumed.receipt
    assert resumed.run_task.task_id == first.run_task.task_id and resumed.run_task.segment == 2
    assert len(store.list()) == 2
    runtime.finish_run(resumed.run_task, AgentResult('cancelled', 0, 'cancelled'))


@pytest.mark.parametrize('field,value', [('workspace', '/another-workspace'), ('objective', 'Another task'),
                                        ('creation', ['invalid'])])
def test_context_rejects_changed_task_scope_or_malformed_credential(tmp_path, field, value):
    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    first = revisions.request(ref, 'Check empty files', 'first')
    meta = store.task_dir(first.run_task.task_id) / 'meta.json'
    data = json.loads(meta.read_text())
    data[field] = value
    meta.write_text(json.dumps(data))
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    with pytest.raises(PlanError) as error:
        revisions.context(first.run_task)
    assert error.value.code == 'needs_reconciliation'
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))


def test_context_rechecks_execution_conflict_before_model_handoff(tmp_path):
    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    first = revisions.request(ref, 'Check empty files', 'first')
    # A legacy caller can still create a bound execution without using PlanReview.
    store.create(kind='run', mode='act', objective='Read the file', workspace=str(tmp_path),
                 plan_id=ref['plan_id'], plan_version=1)
    with pytest.raises(PlanError) as error:
        revisions.context(first.run_task)
    assert error.value.code == 'plan_busy'
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))


def test_invalid_source_goal_binding_is_rejected_before_reserving_operation(tmp_path):
    store, _, revisions, _, _, path = _setup(tmp_path)
    data = json.loads(path.read_text())
    data['goal_id'] = {'invalid': 'binding'}
    path.write_text(json.dumps(data))
    ref = PlanReview(store.root, session_id=store.root.name).read_review()['target']
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    result = revisions.request(ref, 'Check empty files', 'first')
    assert result.run_task is None and result.receipt['code'] == 'invalid_plan'
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['invalid-json', 'symlink'])
def test_damaged_revision_storage_fails_closed_without_mutation(tmp_path, damage):
    store, runtime, revisions, ref, _, path = _setup(tmp_path)
    first = revisions.request(ref, 'Check empty files', 'first')
    operation = next((path.parent / 'review-revisions').glob('*.json'))
    if damage == 'invalid-json':
        operation.write_text('{')
    else:
        external = tmp_path / 'external.json'
        external.write_bytes(operation.read_bytes())
        operation.unlink()
        operation.symlink_to(external)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert revisions.read(ref, 'Check empty files')['code'] == 'needs_reconciliation'
    assert revisions.request(ref, 'Check empty files', 'retry').run_task is None
    with pytest.raises(PlanError) as error:
        revisions.context(first.run_task)
    assert error.value.code == 'needs_reconciliation'
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))


def test_task_creation_audit_failure_reuses_persisted_task(tmp_path, monkeypatch):
    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    open_path = Path.open

    def fail_event(path, mode='r', *args, **kwargs):
        if path.name == 'events.jsonl' and mode == 'a':
            raise OSError('task_created audit failed')
        return open_path(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', fail_event)
        first = revisions.request(ref, 'Check empty files', 'first')
    assert first.run_task is None and first.receipt['code'] == 'storage_error'
    pending = revisions.read(ref, 'Check empty files')
    assert pending['outcome'] == 'pending' and pending['task']['segment'] == 0
    retry = revisions.request(ref, 'Check empty files', 'retry')
    assert retry.run_task is not None, retry.receipt
    assert retry.run_task.task_id == pending['task']['task_id'] and len(store.list()) == 2
    runtime.finish_run(retry.run_task, AgentResult('cancelled', 0, 'cancelled'))


@pytest.mark.parametrize('case,code', [('busy', 'busy'), ('disabled', 'tasks_disabled'),
                                     ('hash', 'content_changed'), ('session', 'target_mismatch'),
                                     ('feedback', 'invalid_request'), ('superseded', 'superseded')])
def test_invalid_initial_request_has_no_persistent_side_effects(tmp_path, case, code):
    store, runtime, revisions, ref, plan, path = _setup(tmp_path)
    feedback = 'Check empty files'
    if case == 'disabled':
        runtime.enabled = False
    elif case == 'hash':
        ref = {**ref, 'plan_hash': '0' * 64}
    elif case == 'session':
        ref = {**ref, 'session_id': 'another-session'}
    elif case == 'feedback':
        feedback = '反馈' * 2001
    elif case == 'superseded':
        newer = Plan.from_dict({**plan.to_dict(), 'version': 2})
        propose_plan(path.parent.parent, newer)
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    result = revisions.request(ref, feedback, 'first', busy=case == 'busy')
    assert result.run_task is None and result.receipt['code'] == code
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}


def test_two_process_requests_create_only_one_initial_run(tmp_path):
    import subprocess
    import sys

    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    script = '''
import json, sys
from app.runtime.plan_revision import PlanRevision
from app.runtime.task_runtime import TaskRuntime
from app.runtime.tasks import TaskStore
store = TaskStore(sys.argv[1])
runtime = TaskRuntime(store, workspace=sys.argv[2])
revision = PlanRevision(store.root, session_id=store.root.name, task_runtime=runtime)
print('ready', flush=True)
sys.stdin.readline()
result = revision.request(json.loads(sys.argv[3]), 'Check empty files', sys.argv[4])
print(json.dumps({'started': result.run_task is not None, 'receipt': result.receipt}), flush=True)
'''
    children = [subprocess.Popen([sys.executable, '-c', script, str(store.root), str(tmp_path),
                                  json.dumps(ref), request_id], cwd=Path(__file__).resolve().parents[1],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for request_id in ('first-process', 'second-process')]
    try:
        for child in children:
            assert child.stdout.readline().strip() == 'ready'
        for child in children:
            child.stdin.write('start\n')
            child.stdin.flush()
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert sum(item['started'] for item in results) == 1
        receipt = revisions.read(ref, 'Check empty files')
        assert receipt['outcome'] == 'running' and receipt['task']['segment'] == 1
        assert len(store.list()) == 2
        assert revisions.request(ref, 'Check empty files', 'retry').run_task is None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        runtime.recover()


def test_process_exit_before_first_segment_reuses_reserved_task(tmp_path):
    import subprocess
    import sys

    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    script = '''
import json, os, sys
from pathlib import Path
from app.runtime.plan_revision import PlanRevision
from app.runtime.task_runtime import TaskRuntime
from app.runtime.tasks import TaskStore
replace = os.replace
def crash(source, destination, *args, **kwargs):
    if Path(destination).name == 'meta.json':
        payload = json.loads(Path(source).read_text())
        if payload['status'] == 'running' and not payload['segments']:
            os._exit(23)
    return replace(source, destination, *args, **kwargs)
os.replace = crash
store = TaskStore(sys.argv[1])
runtime = TaskRuntime(store, workspace=sys.argv[2])
revision = PlanRevision(store.root, session_id=store.root.name, task_runtime=runtime)
revision.request(json.loads(sys.argv[3]), 'Check empty files', 'crashed-run')
'''
    child = subprocess.run([sys.executable, '-c', script, str(store.root), str(tmp_path), json.dumps(ref)],
                           cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert child.returncode == 23, child.stderr
    pending = revisions.read(ref, 'Check empty files')
    assert pending['outcome'] == 'pending' and pending['task']['segment'] == 0
    retry = revisions.request(ref, 'Check empty files', 'retry-after-exit')
    assert retry.run_task is not None, retry.receipt
    assert retry.run_task.task_id == pending['task']['task_id'] and retry.run_task.segment == 1
    assert len(store.list()) == 2
    runtime.finish_run(retry.run_task, AgentResult('cancelled', 0, 'cancelled'))


@pytest.mark.parametrize('phase', ['transition', 'segment'])
def test_failed_initial_preparation_can_continue_same_reserved_task(tmp_path, monkeypatch, phase):
    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    replace = os.replace

    def fail_preparation(source, destination, *args, **kwargs):
        if Path(destination).name == 'meta.json':
            payload = json.loads(Path(source).read_text())
            matches = payload['status'] == 'running' and bool(payload['segments']) == (phase == 'segment')
            if matches:
                raise OSError('injected preparation failure')
        return replace(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', fail_preparation)
        failed = revisions.request(ref, 'Check empty files', 'first')
    assert failed.run_task is None and failed.receipt['code'] == 'storage_error'
    tasks = [task for task in store.list() if task.extra.get('creation')]
    assert len(tasks) == 1 and not tasks[0].segments
    retry = revisions.request(ref, 'Check empty files', 'retry')
    assert retry.run_task is not None, retry.receipt
    assert retry.run_task.task_id == tasks[0].task_id and retry.run_task.segment == 1
    assert len(store.list()) == 2
    runtime.finish_run(retry.run_task, AgentResult('cancelled', 0, 'cancelled'))


def test_process_exit_preserves_revision_and_explicit_resume_closes_old_segment(tmp_path):
    import subprocess
    import sys

    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    script = '''
import json, sys
from app.runtime.plan_revision import PlanRevision
from app.runtime.task_runtime import TaskRuntime
from app.runtime.tasks import TaskStore
store = TaskStore(sys.argv[1])
runtime = TaskRuntime(store, workspace=sys.argv[2])
revision = PlanRevision(store.root, session_id=store.root.name, task_runtime=runtime)
result = revision.request(json.loads(sys.argv[3]), 'Check empty files', 'child-run')
assert result.run_task is not None, result.receipt
print(json.dumps(result.receipt))
'''
    child = subprocess.run([sys.executable, '-c', script, str(store.root), str(tmp_path), json.dumps(ref)],
                           cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert child.returncode == 0, child.stderr
    task_id = json.loads(child.stdout)['task']['task_id']
    assert revisions.request(ref, 'Check empty files', 'read-after-exit').run_task is None
    runtime.recover()
    old = store.get(task_id)
    assert old.status == 'interrupted' and old.segments[0].status == 'interrupted'
    resumed = revisions.resume(ref, 'Check empty files', 'resume-after-exit', expected_segment=1)
    assert resumed.run_task is not None and resumed.run_task.task_id == task_id
    assert resumed.run_task.segment == 2
    runtime.finish_run(resumed.run_task, AgentResult('cancelled', 0, 'cancelled'))


@pytest.mark.parametrize('phase', ['transition', 'segment'])
def test_failed_resume_preparation_keeps_history_and_can_continue(tmp_path, monkeypatch, phase):
    store, runtime, revisions, ref, _, _ = _setup(tmp_path)
    first = revisions.request(ref, 'Check empty files', 'first')
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))
    original_segment = store.get(first.run_task.task_id).segments[0].to_dict()
    replace = os.replace

    def fail_preparation(source, destination, *args, **kwargs):
        if Path(destination).name == 'meta.json':
            payload = json.loads(Path(source).read_text())
            if payload['status'] == 'running' and len(payload['segments']) == (2 if phase == 'segment' else 1):
                raise OSError('injected resume preparation failure')
        return replace(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', fail_preparation)
        failed = revisions.resume(ref, 'Check empty files', 'resume-1', expected_segment=1)
    assert failed.run_task is None and failed.receipt['code'] == 'storage_error'
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    busy = revisions.resume(ref, 'Check empty files', 'busy-retry', expected_segment=1, busy=True)
    assert busy.run_task is None and busy.receipt['code'] == 'busy'
    assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    retry = revisions.resume(ref, 'Check empty files', 'resume-retry', expected_segment=1)
    assert retry.run_task is not None, retry.receipt
    assert retry.run_task.task_id == first.run_task.task_id and retry.run_task.segment == 2
    assert store.get(first.run_task.task_id).segments[0].to_dict() == original_segment
    runtime.finish_run(retry.run_task, AgentResult('cancelled', 0, 'cancelled'))
