import json
from pathlib import Path

from app.runtime.inspection import InspectionStore
from app.session.store import SessionStore


def test_saved_file_source_is_actual_tools_directory_and_does_not_follow_replacement(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    worktree = project / 'task-worktree'; worktree.mkdir()
    session = SessionStore.new(project, 'fake', purpose='test')
    store = InspectionStore(session.root, project)
    run = store.start('origin', '修改文件', tools_root=worktree)
    source = run.snapshot['fileSource']
    assert source['root'] == str(worktree.resolve())
    assert source['identity'] == f'{worktree.stat().st_dev}:{worktree.stat().st_ino}'
    run.finish({'status': 'done'})
    worktree.rename(project / 'old-worktree'); worktree.mkdir()
    assert store.read('origin')['fileSource'] == source
    assert source['identity'] != f'{worktree.stat().st_dev}:{worktree.stat().st_ino}'
    assert store.read('origin')['workspace'] == str(project.resolve())


def test_legacy_and_missing_file_sources_stay_unknown_without_rewriting_record(tmp_path):
    import hashlib
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    store = InspectionStore(session.root, tmp_path)
    run = store.start('legacy', '旧记录')
    envelope = json.loads(run.file.read_text()); del envelope['snapshot']['fileSource']
    envelope['checksum'] = hashlib.sha256(json.dumps(envelope['snapshot'], ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    run.file.write_text(json.dumps(envelope)); before = run.file.read_bytes()
    assert store.read('legacy')['fileSource'] is None
    assert run.file.read_bytes() == before
    assert store.start('missing', '失效目录', tools_root=tmp_path / 'missing').snapshot['fileSource'] is None


def test_attach_bootstrap_uses_worktree_metadata_and_does_not_substitute_project(tmp_path):
    from app.runtime.attach_inspection import AttachInspection
    from tests.test_attach_inspection import seed
    session, task, _, _ = seed(tmp_path)
    worktree = tmp_path / 'worktree'; worktree.mkdir()
    metadata = session.root / 'tasks' / task.task_id / 'worktree.json'
    metadata.write_text(json.dumps({'path': str(worktree), 'branch': 'vibe/test', 'base_commit': 'a' * 40}))
    query = AttachInspection(session.root, task.task_id)
    ready, snapshot, *_ = query.bootstrap(offline=True, control=False)
    assert ready['file_source']['root'] == str(worktree.resolve())
    assert snapshot['file_source'] == ready['file_source']
    worktree.rmdir()
    assert query.bootstrap(offline=True, control=False)[0]['file_source'] is None
