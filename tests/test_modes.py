from __future__ import annotations

import pytest

from app.runtime.modes import (
    MODE_NAMES, ModeError, resolve_profile, validate_profile_tools,
)


def test_profiles_are_known_and_consistent():
    for mode in MODE_NAMES:
        profile = resolve_profile(mode)
        assert profile.mode == mode
        assert profile.system in {"default", "chat_only", "plan_only"}


def test_chat_has_no_tools_and_act_has_all():
    assert resolve_profile("chat").tools == ()
    assert resolve_profile("act").tools == "*"
    assert "plan.review" in resolve_profile("plan").capability


def test_unknown_mode_and_missing_tools_rejected():
    with pytest.raises(ModeError):
        resolve_profile("bogus")
    with pytest.raises(ModeError):
        validate_profile_tools(resolve_profile("plan"), ("read_file",))
