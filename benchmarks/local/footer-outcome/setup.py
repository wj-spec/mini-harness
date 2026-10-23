"""把页脚去重退回到重复显示版本，作为 benchmark 的初始 bug。"""
from pathlib import Path

path = Path("tui/src/app.tsx")
source = path.read_text()
old = "${resultReason(state.lastResult) ? ` · ${resultReason(state.lastResult)}` : ''}"
new = "${state.lastResult ? ` · ${state.lastResult}` : ''}"
assert source.count(old) == 1, "footer source changed; update benchmarks/local/footer-outcome/setup.py"
path.write_text(source.replace(old, new, 1))
print("seeded: footer appends the full result twice")
