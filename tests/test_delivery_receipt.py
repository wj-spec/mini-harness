from core.state import TaskState, classify_task, delivery_receipt


def test_classify_task_separates_action_and_qa():
    assert classify_task("创建 result.txt") == "action"
    assert classify_task("fix the failing test") == "action"
    assert classify_task("如何运行测试？") == "qa"
    assert classify_task("what is a context snapshot") == "qa"
    assert classify_task("") == "qa"


def test_receipt_separates_run_end_and_acceptance():
    state = TaskState(goal="创建 result.txt")
    state.files_changed.append("result.txt")
    state.command_results.append({"command": "pytest -q", "verification": True,
                                  "status": "succeeded", "exit_code": 0, "output": "ok"})
    receipt = delivery_receipt(state, status="done", steps=3, usage={"total_tokens": 120},
                               cost_usd=0.01, tool_calls=2, task_kind="action")
    run_line, accept_line = receipt.splitlines()[:2]
    assert run_line.startswith("[运行结束]") and "status=done" in run_line and "tokens=120" in run_line
    assert accept_line.startswith("[任务验收]") and "请核对" in accept_line
    assert "实际验证（含退出码）：pytest -q [succeeded, exit=0]" in receipt
    assert "本轮改动（工具记录）：result.txt" in receipt


def test_receipt_reports_unverified_changes_and_blockers():
    state = TaskState(goal="创建 result.txt")
    state.files_changed.append("result.txt")
    receipt = delivery_receipt(state, status="done", steps=1, tool_calls=1, task_kind="action")
    assert "本轮改动均未经验证" in receipt
    assert "未运行验证命令" in receipt

    denied = TaskState(goal="创建 result.txt")
    receipt2 = delivery_receipt(denied, status="stopped", steps=1, tool_calls=0,
                                denied=True, task_kind="action")
    assert "动作任务没有已执行的操作：审批拒绝（决定已尊重，未重试）" in receipt2
    assert "stopped 结束" in receipt2

    qa = TaskState(goal="解释预算机制")
    receipt3 = delivery_receipt(qa, status="done", steps=1, tool_calls=0, task_kind="qa")
    assert "动作任务" not in receipt3
    assert "未验证项：无" in receipt3
