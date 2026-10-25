"""把恢复守卫退回无校验版本，作为 benchmark 的初始 bug。"""
from pathlib import Path

path = Path("app/tools.py")
source = path.read_text()
old = '''    def _assert_fresh(self, target: Path, content: str) -> None:
        previous = self._read_hashes.get(target)
        if previous is None and self._restore_guard:
            rel = target.relative_to(self.root)
            raise ValueError(f"restored session: re-read before editing: {rel}")
        if previous is not None and self._hash(content) != previous:
'''
new = '''    def _assert_fresh(self, target: Path, content: str) -> None:
        previous = self._read_hashes.get(target)
        if previous is not None and self._hash(content) != previous:
'''
assert source.count(old) == 1, "restore guard changed; update benchmarks/local/restore-guard/setup.py"
path.write_text(source.replace(old, new, 1))
print("seeded: restore guard removed")
