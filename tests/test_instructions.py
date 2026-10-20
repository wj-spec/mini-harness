from app.instructions import load_agents_md


def test_load_root_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("  use tabs  ", encoding="utf-8")
    assert load_agents_md(tmp_path) == "use tabs"


def test_fallback_to_vibe_agents_md(tmp_path):
    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "AGENTS.md").write_text("fallback", encoding="utf-8")
    assert load_agents_md(tmp_path) == "fallback"


def test_root_wins_over_vibe(tmp_path):
    (tmp_path / "AGENTS.md").write_text("root", encoding="utf-8")
    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "AGENTS.md").write_text("vibe", encoding="utf-8")
    assert load_agents_md(tmp_path) == "root"


def test_truncates(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x" * 100, encoding="utf-8")
    text = load_agents_md(tmp_path, max_chars=10)
    assert text is not None
    assert text.startswith("x" * 10)
    assert "truncated" in text


def test_missing_returns_none(tmp_path):
    assert load_agents_md(tmp_path) is None
