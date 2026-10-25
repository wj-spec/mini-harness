import os
import shutil
import subprocess

import pytest

from core.models import ToolCall
from app.tools import ToolRegistry

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")

ENV = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "LC_ALL": "C"})


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV, check=True)


def commit(repo, message="init"):
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message)


@pytest.fixture()
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("one\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("guide\n")
    git(tmp_path, "add", "-A")
    commit(tmp_path)
    return tmp_path


def test_git_status_reports_branch_changes_and_counts(repo):
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "new.txt").write_text("brand new\n")
    registry = ToolRegistry(repo)

    output = registry.git_status()

    assert "branch: " in output
    assert "files: 2" in output
    assert "a.txt +1 -0" in output
    assert "??  new.txt" in output
    ok, result = registry.execute(ToolCall("git_status", {}))
    assert ok is True
    assert "a.txt" in result


def test_git_status_is_clean_after_commit(repo):
    output = ToolRegistry(repo).git_status()
    assert "working tree clean" in output


def test_git_diff_shows_tracked_and_untracked_changes(repo):
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "new.txt").write_text("brand new\n")
    registry = ToolRegistry(repo)

    output = registry.git_diff()
    assert "diff --git" in output
    assert " one" in output
    assert "+two" in output

    untracked = registry.git_diff(path="new.txt")
    assert "brand new" in untracked
    assert "+brand new" in untracked

    only_a = registry.git_diff(path="a.txt")
    assert "a.txt" in only_a
    assert "new.txt" not in only_a


def test_git_diff_can_show_staged_only(repo):
    (repo / "a.txt").write_text("staged change\n")
    git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("staged change\nunstaged tail\n")
    registry = ToolRegistry(repo)

    staged = registry.git_diff(cached=True)
    assert "staged change" in staged
    assert "unstaged tail" not in staged

    everything = registry.git_diff()
    assert "staged change" in everything
    assert "unstaged tail" in everything


def test_git_tools_are_read_only_and_need_no_approval(repo):
    registry = ToolRegistry(repo)
    assert "git_status" in registry.names()
    assert "git_diff" in registry.names()
    assert registry.risk("git_status") == "read"
    assert registry.risk("git_diff") == "read"


def test_git_tools_reject_non_repositories_and_escaped_paths(tmp_path):
    registry = ToolRegistry(tmp_path)
    with pytest.raises(ValueError, match="not a git repository"):
        registry.git_status()
    with pytest.raises(ValueError, match="not a git repository"):
        registry.git_diff()
    with pytest.raises(ValueError, match="outside workspace"):
        registry.git_diff(path="../outside.txt")


def test_git_diff_truncates_large_output(repo):
    (repo / "big.txt").write_text("original\n")
    git(repo, "add", "big.txt")
    commit(repo, "big")
    (repo / "big.txt").write_text("".join(f"line {index}\n" for index in range(1500)))
    registry = ToolRegistry(repo)

    output = registry.git_diff(path="big.txt")
    assert "[diff truncated at 1200 lines]" in output or "[diff truncated at 40k chars]" in output
