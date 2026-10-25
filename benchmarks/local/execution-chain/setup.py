"""把执行证据链在五个位置退回缺失版本，作为 benchmark 的初始 bug。"""
from pathlib import Path

# 1. Agent 不再把 execution 附到工具消息
path = Path("app/agent.py")
source = path.read_text()
old = '''            tool_message = Message(
                "tool", str(output), name=call.name, tool_call_id=call.id, execution=execution,
            )'''
new = '''            tool_message = Message(
                "tool", str(output), name=call.name, tool_call_id=call.id,
            )'''
assert source.count(old) == 1, "agent tool message changed; update execution-chain setup.py"
path.write_text(source.replace(old, new, 1))

# 2. TaskState 不再记录带退出码的命令结果
path = Path("core/state.py")
source = path.read_text()
old = '''        if name == "bash" and args.get("command") and execution is not None:
            command = str(args["command"])
            verification = args.get("verification") is True
            self.command_results.append({"command": command, "cwd": str(args.get("cwd", ".")),
                                         "verification": verification, "output": str(output), **execution})
            if verification and command not in self.tests_run:
                self.tests_run.append(command)'''
new = '''        if name == "bash" and args.get("command"):
            command = str(args["command"])
            verification = args.get("verification") is True
            if verification and command not in self.tests_run:
                self.tests_run.append(command)'''
assert source.count(old) == 1, "state observe_tool changed; update execution-chain setup.py"
path.write_text(source.replace(old, new, 1))

# 3. stdio 不再把 execution 转发给界面
path = Path("app/stdio.py")
source = path.read_text()
old = '''            self.send("tool_result", request_id, call=asdict(call), ok=ok, output=output,
                      **({"execution": execution} if execution is not None else {}))'''
new = '''            self.send("tool_result", request_id, call=asdict(call), ok=ok, output=output)'''
assert source.count(old) == 1, "stdio tool_result changed; update execution-chain setup.py"
path.write_text(source.replace(old, new, 1))

# 4. Ink 状态层不再保存 execution
path = Path("tui/src/view-state.ts")
source = path.read_text()
old = '''    uncertain: message.content.startsWith('ERROR interrupted:'), execution: message.execution ?? undefined,
    ok: message.execution ? message.execution.status === 'succeeded' : undefined};'''
new = '''    uncertain: message.content.startsWith('ERROR interrupted:')};'''
assert source.count(old) == 1, "view-state history entry changed; update execution-chain setup.py"
source = source.replace(old, new, 1)
old2 = "call: event.call, output: event.output, ok: event.ok, pending: false, runId: state.activeId ?? undefined, execution: event.execution};"
new2 = "call: event.call, output: event.output, ok: event.ok, pending: false, runId: state.activeId ?? undefined};"
assert source.count(old2) == 1, "view-state tool_result entry changed; update execution-chain setup.py"
path.write_text(source.replace(old2, new2, 1))

# 5. Ink 展示层不再显示执行状态
path = Path("tui/src/workbench.tsx")
source = path.read_text()
old = '''  if (entry.execution) {
    const label = {succeeded: '零退出', failed: '非零退出', timed_out: '已超时', cancelled: '已取消', unknown: '执行结果未知'}[entry.execution.status];
    return `${label}${entry.execution.exit_code !== null ? ` · exit=${entry.execution.exit_code}` : ''}`;
  }
'''
assert source.count(old) == 1, "workbench toolState changed; update execution-chain setup.py"
path.write_text(source.replace(old, "", 1))
print("seeded: execution evidence chain removed in agent/core/stdio/tui-state/tui-view")
