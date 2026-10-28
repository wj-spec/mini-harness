import pytest


@pytest.fixture(autouse=True)
def isolated_skill_home(tmp_path, monkeypatch):
    """测试不能自动装载开发机的个人技能。"""
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(tmp_path / "skill-home"))
    # 默认隔离开发机的个人审批策略；需要时由用例显式设置。
    monkeypatch.setenv("VIBE_PERMISSIONS", "")
