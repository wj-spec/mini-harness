import sys

import pytest

from app.config import Config
from core.budget import BudgetPolicy


def clear_budget_env(monkeypatch):
    for name in (
        "CONTEXT_WINDOW", "MAX_TOKENS", "RESERVE_OUTPUT", "SYSTEM_CAP",
        "MEMORY_CAP", "TOOLS_CAP", "HISTORY_CAP", "SCRATCH_CAP",
        "MAX_RUN_TOKENS", "COMPACT_INTERVAL_TOKENS",
    ):
        monkeypatch.delenv("VIBE_" + name, raising=False)


def test_config_uses_the_shared_budget_defaults(monkeypatch):
    clear_budget_env(monkeypatch)
    assert Config().budget_policy() == BudgetPolicy.for_context_window()
    assert Config.from_env().budget_policy() == BudgetPolicy.for_context_window()
    assert Config().max_run_tokens == 1_000_000
    assert Config.from_env().max_run_tokens == 1_000_000
    assert Config().compact_interval_tokens == 200_000
    assert Config.from_env().compact_interval_tokens == 200_000


def test_task_budget_and_compaction_interval_are_independent(monkeypatch):
    clear_budget_env(monkeypatch)
    monkeypatch.setenv("VIBE_MAX_RUN_TOKENS", "750000")
    monkeypatch.setenv("VIBE_COMPACT_INTERVAL_TOKENS", "100000")
    config = Config.from_env()

    assert config.max_run_tokens == 750_000
    assert config.compact_interval_tokens == 100_000
    assert config.context_window == 1_000_000


def test_compaction_interval_can_be_disabled_without_disabling_task_limit(monkeypatch):
    clear_budget_env(monkeypatch)
    monkeypatch.setenv("VIBE_COMPACT_INTERVAL_TOKENS", "0")

    assert Config.from_env().compact_interval_tokens == 0
    assert Config.from_env().max_run_tokens == 1_000_000


def test_context_window_env_takes_precedence_over_the_legacy_alias(monkeypatch):
    clear_budget_env(monkeypatch)
    monkeypatch.setenv("VIBE_CONTEXT_WINDOW", "100000")
    monkeypatch.setenv("VIBE_MAX_TOKENS", "80000")
    config = Config.from_env()
    assert config.context_window == 100_000
    assert config.budget_policy() == BudgetPolicy.for_context_window(100_000)


def test_legacy_max_tokens_env_sets_context_window(monkeypatch):
    clear_budget_env(monkeypatch)
    monkeypatch.setenv("VIBE_MAX_TOKENS", "80000")
    config = Config.from_env()
    assert config.context_window == 80_000
    assert config.budget_policy() == BudgetPolicy.for_context_window(80_000)


def test_budget_env_overrides_stay_explicit(monkeypatch):
    clear_budget_env(monkeypatch)
    monkeypatch.setenv("VIBE_CONTEXT_WINDOW", "100000")
    monkeypatch.setenv("VIBE_HISTORY_CAP", "10000")
    monkeypatch.setenv("VIBE_RESERVE_OUTPUT", "5000")
    monkeypatch.setenv("VIBE_SCRATCH_CAP", "2000")
    config = Config.from_env()
    policy = config.budget_policy()
    assert policy.history_budget() == 10_000
    assert policy.output_cap == 5_000
    assert policy.scratch_cap == 2_000
    assert policy.system_cap == 800


def test_direct_config_and_env_config_produce_the_same_policy(monkeypatch):
    clear_budget_env(monkeypatch)
    monkeypatch.setenv("VIBE_CONTEXT_WINDOW", "80000")
    monkeypatch.setenv("VIBE_SYSTEM_CAP", "1000")
    monkeypatch.setenv("VIBE_TOOLS_CAP", "2000")
    monkeypatch.setenv("VIBE_MEMORY_CAP", "5000")
    assert Config(
        context_window=80_000, system_cap=1_000, tools_cap=2_000, memory_cap=5_000,
    ).budget_policy() == Config.from_env().budget_policy()


def test_runtime_count_limits_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VIBE_MAX_STEPS", raising=False)

    for config in (Config(), Config.from_env()):
        assert config.max_steps == 0
        assert not hasattr(config, "max_tool_calls")
        assert not hasattr(config, "max_repeats")
        assert config.max_run_tokens == 1_000_000
        assert config.max_run_seconds == 600.0


def test_removed_tool_count_env_is_ignored(monkeypatch):
    monkeypatch.setenv("VIBE_MAX_TOOL_CALLS", "obsolete")
    monkeypatch.setenv("VIBE_MAX_REPEATS", "obsolete")
    monkeypatch.setenv("VIBE_MAX_STEPS", "17")

    config = Config.from_env()

    assert config.max_steps == 17
    assert not hasattr(config, "max_tool_calls")
    assert not hasattr(config, "max_repeats")


@pytest.mark.parametrize("max_steps", [0, 31])
def test_cli_max_steps_overrides_environment_even_when_zero(tmp_path, monkeypatch, max_steps):
    from app import __main__ as entrypoint

    monkeypatch.setattr(Config, "from_env", lambda: Config(workspace=tmp_path, max_steps=17))
    captured = []
    monkeypatch.setattr(entrypoint, "_run", lambda config, *args, **kwargs: captured.append(config.max_steps))
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--stdio", "--max-steps", str(max_steps)])

    entrypoint.main()

    assert captured == [max_steps]
