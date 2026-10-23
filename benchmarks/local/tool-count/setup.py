"""把 stdio 的工具计数退回未累加版本，作为 benchmark 的初始 bug。"""
from pathlib import Path

path = Path("app/stdio.py")
source = path.read_text()
old = """        def tool_result(call, ok, output):
            nonlocal observed_tools
            observed_tools += 1
            execution = executions.pop(id(call), None)
"""
new = """        def tool_result(call, ok, output):
            execution = executions.pop(id(call), None)
"""
assert source.count(old) == 1, "tool_result source changed; update benchmarks/local/tool-count/setup.py"
path.write_text(source.replace(old, new, 1))
print("seeded: observed tool count no longer increments")
