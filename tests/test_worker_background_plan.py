from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.runtime.plan import Plan, approve_plan, propose_plan
from app.runtime.plan_execution import StepLedger, save_ledger
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def test_worker_completes_background_plan_with_acceptance(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".vibe").mkdir()
    (repo / ".vibe" / "unattended.json").write_text('{"schema_version":1,"allow_exec":true}', encoding="utf-8")
    session = SessionStore.new(repo, "fake", purpose="test")
    store = TaskStore(session.root)
    plan_task = store.create(kind="run", objective="计划", workspace=str(repo), mode="act")
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-bg-e2e", "task_id": plan_task.task_id,
        "objective": "后台验收计划",
        "acceptance_criteria": [{"id": "ac1", "text": "真", "confirmed": True}],
        "verification_plan": [{"id": "v1", "covers": ["ac1"], "command": "true",
                               "source": "plan_approval"}],
        "steps": [{"step_id": "s1", "title": "读"}],
    })
    task_dir = store.task_dir(plan_task.task_id)
    propose_plan(task_dir, plan)
    approve_plan(task_dir, plan, approved_by="user")
    ledger = StepLedger.from_plan(plan)
    ledger.start_step("s1")
    ledger.add_evidence("s1", kind="manual", ref="precompleted")
    ledger.complete_step(plan, "s1")
    save_ledger(task_dir, ledger)
    background = store.create(kind="run", objective="后台执行", workspace=str(repo),
                              mode="act", plan_id="pl-bg-e2e", plan_version=1, session_id=session.id)
    env = {
        **os.environ,
        "VIBE_TASK_RUNTIME": "1",
        "VIBE_ALLOW_NON_GIT": "1",
        "VIBE_AUTONOMY": "full",
        "VIBE_WORKER_SOCKET_DIR": str(tmp_path / "sockets"),
        "VIBE_WORKER_HEARTBEAT_SECONDS": "0.2",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "app", "--worker", "--fake", "--workspace", str(repo),
         "--session", session.id, "--task-id", background.task_id],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
        env=env, timeout=60,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    task = store.get(background.task_id)
    assert task.status == "completed", (task.status, proc.stdout, proc.stderr)
    accepted = json.loads((task_dir / "acceptance.v1.json").read_text(encoding="utf-8"))
    assert accepted["status"] is True
    from app.runtime.notifications import read_notifications
    bindings = [item for item in read_notifications(repo) if item["kind"] == "plan_binding"]
    assert bindings and bindings[-1]["executable"] is True
