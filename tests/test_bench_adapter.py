import json
from pathlib import Path

import pytest

from bench import support

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_split_model_name_accepts_provider_prefix():
    assert support.split_model_name("deepseek/deepseek-chat") == ("deepseek", "deepseek-chat")
    assert support.split_model_name("openai/gpt-4.1") == ("openai", "gpt-4.1")
    assert support.split_model_name("deepseek-chat") == (None, "deepseek-chat")
    assert support.split_model_name("") == (None, "")


def test_resolve_settings_infers_deepseek_and_maps_env():
    resolved = support.resolve_agent_settings(
        {"DEEPSEEK_API_KEY": "sk-test"},
        model_name="deepseek/deepseek-chat",
    )
    assert resolved["provider"] == "deepseek"
    assert resolved["model"] == "deepseek-chat"
    assert resolved["base_url"] == "https://api.deepseek.com/v1"
    assert resolved["api_key"] == "sk-test"
    assert resolved["env"]["VIBE_MODEL"] == "deepseek-chat"
    assert resolved["env"]["VIBE_MAX_STEPS"] == "300"
    assert resolved["env"]["VIBE_MAX_RUN_SECONDS"] == "1500.0"


def test_resolve_settings_infers_openai_and_honours_overrides():
    resolved = support.resolve_agent_settings(
        {"OPENAI_API_KEY": "sk-openai", "VIBE_BENCH_MAX_STEPS": "12"},
        model_name="openai/gpt-4.1",
        base_url="http://127.0.0.1:8080/v1",
        max_run_seconds=30,
    )
    assert resolved["provider"] == "openai"
    assert resolved["base_url"] == "http://127.0.0.1:8080/v1"
    assert resolved["env"]["VIBE_MAX_STEPS"] == "12"
    assert resolved["env"]["VIBE_MAX_RUN_SECONDS"] == "30.0"


def test_resolve_settings_rejects_prefix_conflict_and_missing_key():
    with pytest.raises(ValueError):
        support.resolve_agent_settings(
            {"DEEPSEEK_API_KEY": "k"}, model_name="openai/gpt-4.1", provider="deepseek",
        )
    with pytest.raises(RuntimeError):
        support.resolve_agent_settings({}, model_name="deepseek/deepseek-chat")


def test_read_telemetry_validates_shape(tmp_path):
    path = tmp_path / "telemetry.json"
    path.write_text(json.dumps({"status": "done", "usage": {"total_tokens": 1}}), encoding="utf-8")
    assert support.read_telemetry(path)["status"] == "done"
    path.write_text(json.dumps({"status": "", "usage": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        support.read_telemetry(path)
    path.write_text(json.dumps({"status": "done"}), encoding="utf-8")
    with pytest.raises(ValueError):
        support.read_telemetry(path)


def test_agent_version_env_override(monkeypatch):
    monkeypatch.setenv("VIBE_BENCH_AGENT_VERSION", "test-sha")
    assert support.agent_version(REPO_ROOT) == "test-sha"


def test_adapter_source_compiles_and_requirements_are_pinned():
    source = (REPO_ROOT / "bench" / "adapter.py").read_text(encoding="utf-8")
    compile(source, "bench/adapter.py", "exec")
    requirements = (REPO_ROOT / "bench" / "requirements-bench.txt").read_text(encoding="utf-8")
    for pin in ("ddgs==", "fastmcp==", "httpx=="):
        assert pin in requirements
