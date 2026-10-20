import pytest

from core.budget import BudgetPolicy


def test_default_allocation_accounts_for_the_whole_window():
    policy = BudgetPolicy.for_context_window(1_000_000)
    assert policy.system_cap == 8_000
    assert policy.memory_cap == 64_000
    assert policy.tools_cap == 16_000
    assert policy.history_cap is None
    assert policy.output_cap == 32_000
    assert policy.scratch_cap == 8_000
    assert policy.prompt_cap == 968_000
    caps = policy.caps()
    assert caps["history"] == 872_000
    assert caps["unallocated"] == 0
    assert sum(caps[name] for name in (
        "system", "memory", "tools", "history", "output", "scratch", "unallocated"
    )) == policy.context_window


def test_auto_history_borrows_unused_partitions():
    policy = BudgetPolicy.for_context_window(1_000_000)
    assert policy.history_budget() == 960_000
    assert policy.history_budget(8_000, 0, 0) == 952_000
    assert policy.history_budget(8_000, 64_000, 16_000) == 872_000
    assert policy.history_budget(1_000_000, 64_000, 16_000) == 0


def test_explicit_history_cap_remains_a_hard_limit():
    policy = BudgetPolicy.for_context_window(history_cap=256_000)
    assert policy.history_budget() == 256_000
    assert policy.caps()["history"] == 256_000
    assert policy.caps()["unallocated"] == 616_000


def test_legacy_factory_remains_compatible():
    assert BudgetPolicy.from_book_ratios() == BudgetPolicy.for_context_window()


def test_explicit_history_limit_yields_to_reserved_partitions():
    tight = BudgetPolicy.for_context_window(
        80_000, output_cap=10_000, system_cap=8_000, memory_cap=20_000,
        tools_cap=10_000, history_cap=40_000,
    )
    assert tight.history_budget(8_000, 20_000, 10_000) == 31_360


@pytest.mark.parametrize("window", [0, 1, 1_000, 80_000, 128_000, 1_000_000, 2_000_000])
def test_default_allocations_scale_down_without_losing_tokens(window):
    policy = BudgetPolicy.for_context_window(window)
    caps = policy.caps()
    allocations = [caps[name] for name in (
        "system", "memory", "tools", "history", "output", "scratch", "unallocated"
    )]
    assert min(allocations) >= 0
    assert sum(allocations) == window
    assert policy.history_budget() <= window


def test_small_window_scales_default_reservations():
    policy = BudgetPolicy.for_context_window(100_000)
    assert policy.output_cap == 3_200
    assert policy.system_cap == 800
    assert policy.memory_cap == 6_400
    assert policy.tools_cap == 1_600
    assert policy.scratch_cap == 800
    assert policy.caps()["history"] == 87_200


def test_explicit_reservations_fit_inside_the_window():
    policy = BudgetPolicy.for_context_window(
        1_000, output_cap=2_000, system_cap=2_000, memory_cap=2_000,
        tools_cap=2_000, scratch_cap=2_000, history_cap=2_000,
    )
    caps = policy.caps()
    assert sum(caps[name] for name in (
        "system", "memory", "tools", "history", "output", "scratch", "unallocated"
    )) == 1_000
    assert policy.history_budget() <= 2_000


def test_negative_budgets_are_rejected():
    with pytest.raises(ValueError):
        BudgetPolicy.for_context_window(-1)
    with pytest.raises(ValueError):
        BudgetPolicy.for_context_window(memory_cap=-1)
