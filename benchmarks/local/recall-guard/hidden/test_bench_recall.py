import json

import pytest

from app.tools import ToolRegistry
from core.context import ContextBuilder
from core.models import ToolCall


def _registry(tmp_path):
    workspace = tmp_path / "work"
    offload = workspace / ".vibe" / "offload"
    offload.mkdir(parents=True)
    return workspace, offload, ToolRegistry(workspace, context=ContextBuilder(offload_dir=offload))


def test_bench_symlinked_offload_cannot_escape_directory(tmp_path):
    _, offload, registry = _registry(tmp_path)
    secret = tmp_path / "secret.json"
    secret.write_text('[{"role": "user", "content": "private"}]', encoding="utf-8")
    (offload / "off-abcdef12.json").symlink_to(secret)

    with pytest.raises(ValueError):
        registry.recall("off-abcdef12")
    ok, output = registry.execute(ToolCall("recall", {"ref": "off-abcdef12"}))
    assert not ok and "private" not in output


def test_bench_rejects_non_offload_names_and_keeps_valid_files(tmp_path):
    _, offload, registry = _registry(tmp_path)
    outside = tmp_path / "notes.json"
    outside.write_text(json.dumps([{"role": "user", "content": "outside"}]), encoding="utf-8")

    for ref in (str(outside), "off-ZZZZZZZZ", "..%2F..%2Fnotes"):
        with pytest.raises(ValueError):
            registry.recall(ref)

    (offload / "off-12345678.json").write_text(
        '[{"role": "user", "content": "kept"}]', encoding="utf-8",
    )
    assert "kept" in registry.recall("off-12345678")
