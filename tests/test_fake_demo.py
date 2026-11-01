from app import __main__ as entrypoint
from app.config import Config


def test_fake_demo_script_covers_write_approval_and_verification(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_FAKE_DEMO", "1")
    provider = entrypoint.build_provider(Config(workspace=tmp_path), True)
    assert len(provider.script) == 3
    write = provider.script[0].tool_calls[0]
    check = provider.script[1].tool_calls[0]
    assert write.name == "write_file"
    assert write.args["path"].startswith(".vibe/")
    assert check.name == "bash"
    assert check.args["verification"] is True
    assert "evidence" in provider.script[2]


def test_fake_provider_default_script_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.delenv("VIBE_FAKE_DEMO", raising=False)
    provider = entrypoint.build_provider(Config(workspace=tmp_path), True)
    assert len(provider.script) == 1
    assert "fake provider" in provider.script[0]
