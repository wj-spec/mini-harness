import json
from pathlib import Path

import pytest

from app.runtime.inspection import InspectionStore


def store_at(tmp_path):
    session = tmp_path / '.vibe' / 'sessions' / 'test-session'
    session.mkdir(parents=True)
    return InspectionStore(session, tmp_path)


def test_records_keep_requests_commands_and_actual_failure_separate(tmp_path):
    store = store_at(tmp_path)
    first = store.start('request-1', '中文任务')
    call = {'id': 'shared', 'name': 'bash', 'args': {'command': 'exit 2', 'verification': True}}
    first.observe('tool_call', call=call)
    first.observe('tool_result', call=call, ok=False, output='模型声称完成', execution={'status': 'failed', 'exit_code': 2})
    first.finish({'status': 'stopped', 'run_id': 'trace-1', 'stop_reason': 'verification_failed', 'verified': False})
    second = store.start('request-2', '后续任务')
    second.observe('tool_result', call={**call, 'args': {'command': 'echo ok'}}, ok=True, output='ok', execution={'status': 'succeeded', 'exit_code': 0})
    second.finish({'status': 'done', 'run_id': 'trace-2', 'verified': None})
    restored = InspectionStore(store.session_root, tmp_path)
    old = restored.read('request-1')
    assert old['scope']['requestId'] == 'request-1'
    assert old['scope']['traceRunId'] == 'trace-1'
    assert old['verification']['verified'] is False
    assert old['verification']['commands'][0]['execution'] == {'status': 'failed', 'exit_code': 2}
    assert old['verification']['commands'][0]['output'] == '模型声称完成'
    assert restored.read('request-2')['verification']['verified'] is None
    assert restored.read('request-2')['verification']['commands'][0]['command'] == 'echo ok'


def test_unfinished_checkpoint_preserves_pending_command_without_claiming_completion(tmp_path):
    store = store_at(tmp_path)
    record = store.start('pending', '未完成')
    record.observe('tool_call', call={'id': 'c1', 'name': 'bash', 'args': {'command': 'long task'}})
    snapshot = InspectionStore(store.session_root, tmp_path).read('pending')
    assert snapshot['scope']['status'] == 'unknown'
    assert snapshot['verification']['status'] is None
    assert snapshot['verification']['verified'] is None
    assert snapshot['verification']['commands'][0]['pending'] is True


def test_pages_keep_a_stable_cursor_when_new_runs_arrive_and_filter_original_task(tmp_path):
    store = store_at(tmp_path)
    for index in range(5):
        record = store.start(f'request-{index}', str(index), task={'task_id': 'original' if index % 2 else 'other', 'segment': index + 1, 'status': 'running'})
        record.finish({'status': 'done', 'run_id': f'trace-{index}'})
    page = store.page(limit=2)
    assert [item['scope']['requestId'] for item in page['records']] == ['request-4', 'request-3']
    store.start('new', 'new')
    before = sorted((str(p), p.read_bytes()) for p in store.root.iterdir())
    next_page = store.page(before=page['next_cursor'], limit=2, task_id='original')
    assert [item['scope']['requestId'] for item in next_page['records']] == ['request-1']
    assert before == sorted((str(p), p.read_bytes()) for p in store.root.iterdir())
    with pytest.raises(ValueError):
        store.page(before='../../outside')
    with pytest.raises(ValueError):
        store.page(limit=0)


def test_corruption_and_another_workspace_are_reported_without_fabricated_records(tmp_path):
    store = store_at(tmp_path)
    store.start('one', 'first').finish({'status': 'done'})
    file = next(store.root.glob('*.json'))
    envelope = json.loads(file.read_text())
    envelope['snapshot']['verification']['verified'] = True
    file.write_text(json.dumps(envelope))
    assert store.read('one') is None
    assert store.page()['skipped'] == 1
    assert store.page()['records'] == []
    store.start('two', 'second').finish({'status': 'done'})
    wrong = InspectionStore(store.session_root, tmp_path / 'other')
    assert wrong.page()['records'] == []
    assert wrong.page()['skipped'] == 2


def test_bounded_command_output_and_missing_results_remain_explicit(tmp_path):
    store = store_at(tmp_path)
    record = store.start('bounded', 'limit')
    for index in range(25):
        call = {'id': str(index), 'name': 'bash', 'args': {'command': f'command-{index}'}}
        record.observe('tool_result', call=call, ok=True, output='中文🙂' * 2000, execution={'status': 'succeeded', 'exit_code': 0})
    record.finish({'status': 'done'})
    saved = store.read('bounded')
    assert len(saved['verification']['commands']) == 20
    assert saved['verification']['omittedCommands'] == 5
    assert all(command['truncated'] for command in saved['verification']['commands'])
    assert all(len(command['output'].encode()) <= 2000 for command in saved['verification']['commands'])
    assert next(store.root.glob('*.json')).stat().st_size < 256 * 1024


def test_failed_checkpoint_does_not_erase_previous_evidence_and_later_save_can_recover(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    record = store.start('recover', 'recover')
    replace = __import__('os').replace
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr('os.replace', fail)
    record.observe('tool_result', call={'id': 'c', 'name': 'bash', 'args': {'command': 'test'}}, ok=False, output='failed')
    assert record.error
    assert store.read('recover')['scope']['status'] == 'unknown'
    monkeypatch.setattr('os.replace', replace)
    record.finish({'status': 'stopped', 'verified': False})
    assert record.error is None
    assert store.read('recover')['verification']['commands'][0]['output'] == 'failed'
    assert list(store.root.glob('*.tmp')) == []


def test_request_identity_cannot_overwrite_an_earlier_record(tmp_path):
    store = store_at(tmp_path)
    store.start('same', 'original').finish({'status': 'done'})
    with pytest.raises(ValueError):
        store.start('same', 'replacement')
    assert store.read('same')['scope']['objective'] == 'original'


def test_duplicate_saved_identity_is_rejected_by_both_readers(tmp_path):
    store = store_at(tmp_path)
    store.start('duplicate', 'original').finish({'status': 'done'})
    original = next(store.root.glob('*.json'))
    duplicate = original.with_name(f'{int(original.name[:20]) + 1:020d}' + original.name[20:])
    duplicate.write_bytes(original.read_bytes())
    assert store.read('duplicate') is None
    page = store.page()
    assert page['records'] == [] and page['skipped'] == 2


def test_patch_records_include_deleted_files_and_ignore_denied_writes(tmp_path):
    store = store_at(tmp_path)
    record = store.start('delete', 'delete')
    record.observe('tool_result', call={'name': 'apply_patch', 'args': {'patch': 'diff --git a/old.txt b/old.txt\n--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old'}}, ok=True)
    record.observe('tool_result', call={'name': 'write_file', 'args': {'path': 'denied.txt'}}, ok=False)
    record.finish({'status': 'done'})
    assert store.read('delete')['files'] == ['old.txt']


def test_bound_task_objective_matches_runtime_and_cannot_be_replaced_by_another_segment(tmp_path):
    store = store_at(tmp_path)
    task = {'task_id': 'revision-task', 'segment': 1, 'status': 'running', 'objective': '原任务目标'}
    record = store.start('revision', '按反馈修订原计划：补充验证', task=task)
    assert store.read('revision')['scope']['objective'] == '原任务目标'
    record.observe('task_state', task={**task, 'status': 'completed'})
    record.observe('task_state', task={**task, 'segment': 2, 'objective': '另一段'})
    record.observe('task_state', task={**task, 'task_id': 'another', 'objective': '另一任务'})
    saved = store.read('revision')['scope']
    assert saved['objective'] == '原任务目标'
    assert saved['taskStatus'] == 'completed' and saved['segment'] == 1


def test_mixed_patch_records_all_actual_targets_including_deletions(tmp_path):
    store = store_at(tmp_path)
    record = store.start('mixed', 'mixed')
    patch = ('diff --git a/old.txt b/old.txt\n--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n'
             'diff --git a/new.txt b/new.txt\n--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+new\n+++ b/phantom.txt\n')
    record.observe('tool_result', call={'name': 'apply_patch', 'args': {'patch': patch}}, ok=True)
    record.finish({'status': 'done'})
    assert store.read('mixed')['files'] == ['old.txt', 'new.txt']
