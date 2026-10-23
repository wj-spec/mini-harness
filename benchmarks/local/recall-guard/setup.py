"""把 recall 校验退回到不安全版本，作为 benchmark 的初始 bug。"""
from pathlib import Path

path = Path("core/context.py")
source = path.read_text()
old = """        elif self.offload_dir is not None:
            if not _OFFLOAD_ID.fullmatch(ref):
                raise ValueError("invalid offload reference")
            root = self.offload_dir.resolve()
            path = (root / f"{ref}.json").resolve()
            if path.parent != root:
                raise ValueError("offload reference outside offload directory")
            if not path.is_file():
                raise KeyError(ref)
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("offload exceeds 2 MiB limit")
            raw = json.loads(path.read_text(encoding="utf-8"))
            messages = [message_from_dict(d) for d in raw]
"""
new = """        elif self.offload_dir is not None:
            path = self.offload_dir / f"{ref}.json"
            if not path.is_file():
                raise KeyError(ref)
            raw = json.loads(path.read_text(encoding="utf-8"))
            messages = [message_from_dict(d) for d in raw]
"""
assert source.count(old) == 1, "recall guard source changed; update benchmarks/local/recall-guard/setup.py"
path.write_text(source.replace(old, new, 1))
print("seeded: recall guard removed")
