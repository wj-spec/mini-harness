import asyncio
from pathlib import Path

import pytest


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.block = None
        self.started = asyncio.Event()
        self.killed = 0
        self.reaped = 0

    async def spawn(self, *argv, **kwargs):
        self.calls.append((argv, kwargs))
        owner = self
        command = list(argv[3:] if argv[1:2] == ("--host",) else argv[1:])
        action = command[0]
        code, data = self.responses.get(action, (0, b""))
        if action == "image":
            code, data = self.responses.get("image", (0, b"sha256:fixture\n"))
        blocking = self.block == action

        class Process:
            returncode = None

            def __init__(self):
                self.stdout = asyncio.StreamReader()
                if not blocking:
                    self.stdout.feed_data(data)
                    self.stdout.feed_eof()
                    self.returncode = code

            def kill(self):
                owner.killed += 1
                self.returncode = -9
                self.stdout.feed_eof()

            async def wait(self):
                owner.reaped += 1
                while self.returncode is None:
                    await asyncio.sleep(0)
                return self.returncode

        proc = Process()
        if blocking:
            self.started.set()
        return proc


def commands(fake):
    return [list(argv[3:] if argv[1:2] == ("--host",) else argv[1:]) for argv, _ in fake.calls]


def test_open_extracts_image_and_mounts_only_workspace(tmp_path, monkeypatch):
    from app.eval_container import DockerSandbox

    fake = FakeDocker()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake.spawn)

    async def scenario():
        box = DockerSandbox(tmp_path, "fixture:latest", docker_host="unix:///fixture.sock")
        await box.open()
        assert box.image_id == "sha256:fixture"
        await box.close()

    asyncio.run(scenario())
    calls = commands(fake)
    assert [c[0] for c in calls] == ["image", "create", "cp", "rm", "run", "rm"]
    run = next(c for c in calls if c[0] == "run")
    assert run[run.index("--network") + 1] == "none"
    assert run[run.index("--platform") + 1] == "linux/amd64"
    assert run[run.index("--cpus") + 1] == "2"
    assert run[run.index("--memory") + 1] == "2g"
    assert run[run.index("--pids-limit") + 1] == "256"
    assert run.count("-v") == 1
    assert run[run.index("-v") + 1] == f"{tmp_path.resolve()}:/testbed"
    assert "-e" not in run and "--env" not in run
    assert run[-2:] == ["-c", "tail -f /dev/null"]
    assert all(argv[:3] == ("docker", "--host", "unix:///fixture.sock") for argv, _ in fake.calls)
    assert all("shell" not in opts for _, opts in fake.calls)


def test_run_uses_conda_inside_container_and_bounds_output(tmp_path, monkeypatch):
    from app.eval_container import DockerSandbox

    fake = FakeDocker()
    fake.responses["exec"] = (3, b"prefix" + b"x" * 20_000 + b"tail")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake.spawn)

    async def scenario():
        box = DockerSandbox(tmp_path, "fixture:latest")
        await box.open()
        code, output, timed_out = await box.run("printf '$(echo sensitive)'", cwd=".", timeout=9)
        assert code == 3 and not timed_out
        assert "truncated" in output and output.endswith("tail")
        assert len(output) < 10_100
        await box.close()

    asyncio.run(scenario())
    execution = next(c for c in commands(fake) if c[0] == "exec")
    assert execution[1:3] == ["-w", "/testbed"]
    assert execution[-3:-1] == ["bash", "-lc"]
    assert execution[-1] == "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && printf '$(echo sensitive)'"


def test_commands_and_patch_export_trust_only_testbed_git_directory(tmp_path, monkeypatch):
    from app.eval_container import DockerSandbox

    fake = FakeDocker()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake.spawn)

    async def scenario():
        box = DockerSandbox(tmp_path, "fixture:latest")
        await box.open()
        await box.run("git rev-parse HEAD")
        await box.export_patch()
        await box.close()

    asyncio.run(scenario())
    executions = [command for command in commands(fake) if command[0] == "exec"]
    assert len(executions) == 2
    for command in executions:
        env_values = [command[index + 1] for index, value in enumerate(command) if value == "--env"]
        assert env_values == ["GIT_CONFIG_COUNT=1", "GIT_CONFIG_KEY_0=safe.directory", "GIT_CONFIG_VALUE_0=/testbed"]


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancel_remove_container_and_reap_cli(tmp_path, monkeypatch, cancel):
    from app.eval_container import DockerSandbox
    from app.sandbox import CommandCancelled

    async def scenario():
        fake = FakeDocker()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake.spawn)
        box = DockerSandbox(tmp_path, "fixture:latest")
        await box.open()
        fake.block = "exec"
        task = asyncio.create_task(box.run("sleep 20 & wait", timeout=20 if cancel else .01))
        await fake.started.wait()
        if cancel:
            task.cancel()
            with pytest.raises(CommandCancelled):
                await asyncio.wait_for(task, 1)
        else:
            _, _, timed_out = await asyncio.wait_for(task, 1)
            assert timed_out
        assert fake.killed == 1
        assert fake.reaped == len(fake.calls)
        removed = [c[-1] for c in commands(fake) if c[:2] == ["rm", "-f"]]
        run_name = next(c[c.index("--name") + 1] for c in commands(fake) if c[0] == "run")
        assert run_name in removed
        before = len(fake.calls)
        await box.close()
        assert len(fake.calls) == before
        with pytest.raises(RuntimeError, match="not running"):
            await box.run("echo cannot reuse a stopped sandbox")

    asyncio.run(scenario())


def test_export_patch_keeps_new_and_deleted_files_and_fails_on_oversize(tmp_path, monkeypatch):
    from app.eval_container import DockerSandbox

    fake = FakeDocker()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake.spawn)
    patch = "diff --git a/a.py b/a.py\nnew file mode 100644\n" + "x" * 20_000

    async def scenario():
        box = DockerSandbox(tmp_path, "fixture:latest")
        await box.open()
        fake.responses["exec"] = (0, patch.encode())
        assert await box.export_patch() == patch
        fake.responses["exec"] = (0, b"x" * (2 * 1024 * 1024 + 1))
        with pytest.raises(RuntimeError, match="patch.*limit"):
            await box.export_patch()
        await box.close()

    asyncio.run(scenario())
    script = [c[-1] for c in commands(fake) if c[0] == "exec"][0]
    assert "git add -N -- . ':!.vibe'" in script
    assert "git -c core.externalDiff= diff --no-ext-diff --binary HEAD -- . ':!.vibe'" in script


def test_registry_uses_native_files_and_container_commands_without_network_or_skills(tmp_path):
    from app.eval_container import ContainerToolRegistry
    from core.context import ContextBuilder
    from core.models import ToolCall
    from core.state import TaskState

    workspace = tmp_path / "work"
    workspace.mkdir()
    config = tmp_path / "skills.json"
    config.write_text('{"discover": false}')
    calls = []

    class Sandbox:
        async def run(self, command, cwd=".", timeout=30):
            calls.append((command, cwd, timeout))
            return 0, "tests passed", False

    sandbox = Sandbox()
    sandbox.workspace = workspace
    registry = ContainerToolRegistry(sandbox, ContextBuilder(), TaskState(goal="fix"), skills_config=str(config))
    assert set(registry.names()) == {"read_file", "list_files", "grep", "write_file", "edit_file", "bash", "recall", "update_task"}
    assert registry.risk("bash") == "exec"
    assert registry.risk("write_file") == "write"
    assert registry.skill_manager.available == []
    ok, _ = registry.execute(ToolCall("write_file", {"path": "new.py", "content": "pass\n"}))
    assert ok and (workspace / "new.py").read_text() == "pass\n"
    ok, output = asyncio.run(registry.execute_async(ToolCall("bash", {"command": "pytest", "verification": True})))
    assert ok and "tests passed" in output
    assert output.execution == {"status": "succeeded", "exit_code": 0}
    assert calls == [("pytest", ".", 30.0)]
    ok, output = registry.execute(ToolCall("bash", {"command": "pytest"}))
    assert not ok and "async" in output
    assert len(calls) == 1


@pytest.mark.parametrize("args", [
    {"command": "sudo echo blocked"}, {"command": "echo x", "cwd": "../outside"},
    {"command": "echo x", "cwd": "/tmp"}, {"command": "echo x", "timeout": float("inf")},
])
def test_invalid_commands_never_reach_docker_exec(tmp_path, monkeypatch, args):
    from app.eval_container import DockerSandbox

    fake = FakeDocker()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake.spawn)

    async def scenario():
        box = DockerSandbox(tmp_path, "fixture")
        await box.open()
        with pytest.raises((PermissionError, ValueError)):
            await box.run(**args)
        await box.close()

    asyncio.run(scenario())
    assert not any(c[0] == "exec" for c in commands(fake))


def test_cleanup_failure_still_reaps_timed_out_cli(tmp_path, monkeypatch):
    from app.eval_container import DockerSandbox

    async def scenario():
        fake = FakeDocker()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake.spawn)
        box = DockerSandbox(tmp_path, "fixture")
        await box.open()
        fake.block = "exec"
        fake.responses["rm"] = (1, b"daemon unavailable")
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await box.run("sleep 100", timeout=.01)
        assert fake.killed == 1
        assert fake.reaped == len(fake.calls)
        fake.responses["rm"] = (0, b"")
        await box.close()

    asyncio.run(scenario())


def test_registry_does_not_follow_predictable_temporary_symlink(tmp_path):
    from app.eval_container import ContainerToolRegistry
    from core.models import ToolCall

    workspace = tmp_path / "work"
    workspace.mkdir()
    config = tmp_path / "skills.json"
    config.write_text('{"discover": false}')
    secret = tmp_path / "secret"
    secret.write_text("private")
    (workspace / "new.py.tmp").symlink_to(secret)
    sandbox = type("Sandbox", (), {"workspace": workspace})()
    registry = ContainerToolRegistry(sandbox, skills_config=str(config))
    ok, output = registry.execute(ToolCall("write_file", {"path": "new.py", "content": "pass"}))
    assert ok, output
    assert secret.read_text() == "private"
    assert (workspace / "new.py").read_text() == "pass"


def test_registry_rejects_backup_directory_symlink_escape(tmp_path):
    from app.eval_container import ContainerToolRegistry
    from core.models import ToolCall

    workspace = tmp_path / "work"
    workspace.mkdir()
    config = tmp_path / "skills.json"
    config.write_text('{"discover": false}')
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".vibe").symlink_to(outside, target_is_directory=True)
    (workspace / "old.py").write_text("before")
    sandbox = type("Sandbox", (), {"workspace": workspace})()
    registry = ContainerToolRegistry(sandbox, skills_config=str(config))
    ok, output = registry.execute(ToolCall("edit_file", {"path": "old.py", "old": "before", "new": "after"}))
    assert not ok and "outside workspace" in output
    assert list(outside.iterdir()) == []
    assert (workspace / "old.py").read_text() == "before"


def test_missing_docker_has_actionable_error(tmp_path, monkeypatch):
    from app.eval_container import DockerSandbox

    async def missing(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)
    with pytest.raises(RuntimeError, match="Docker CLI unavailable"):
        asyncio.run(DockerSandbox(tmp_path, "fixture").open())


def test_failed_copy_cleans_only_its_source_container(tmp_path, monkeypatch):
    from app.eval_container import DockerSandbox

    fake = FakeDocker()
    fake.responses["cp"] = (1, b"missing /testbed")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake.spawn)
    with pytest.raises(RuntimeError, match="Docker cp failed"):
        asyncio.run(DockerSandbox(tmp_path, "fixture").open())
    calls = commands(fake)
    name = next(c[c.index("--name") + 1] for c in calls if c[0] == "create")
    assert [c for c in calls if c[0] == "rm"] == [["rm", "-f", name]]
    assert name.startswith("mini-harness-eval-") and name.endswith("-source")
    assert not any(c[0] == "run" for c in calls)


def test_cancel_during_subprocess_creation_still_cleans_and_reaps(tmp_path, monkeypatch):
    from app.eval_container import DockerSandbox
    from app.sandbox import CommandCancelled

    async def scenario():
        fake = FakeDocker()
        spawning = asyncio.Event()
        release = asyncio.Event()

        async def spawn(*argv, **kwargs):
            if "exec" in argv:
                spawning.set()
                await release.wait()
            return await fake.spawn(*argv, **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        box = DockerSandbox(tmp_path, "fixture")
        await box.open()
        fake.block = "exec"
        task = asyncio.create_task(box.run("sleep 100"))
        await spawning.wait()
        task.cancel()
        release.set()
        with pytest.raises(CommandCancelled):
            await asyncio.wait_for(task, 1)
        assert fake.killed == 1 and fake.reaped == len(fake.calls)
        assert commands(fake)[-1][:2] == ["rm", "-f"]

    asyncio.run(scenario())


@pytest.mark.parametrize("config_text", ['{}', '{"discover": true}', '{"discover": false, "paths": ["/tmp"]}'])
def test_registry_requires_explicitly_disabled_skill_discovery(tmp_path, config_text):
    from app.eval_container import ContainerToolRegistry

    workspace = tmp_path / "work"
    workspace.mkdir()
    config = tmp_path / "skills.json"
    config.write_text(config_text)
    sandbox = type("Sandbox", (), {"workspace": workspace})()
    with pytest.raises(ValueError, match="disable.*skills"):
        ContainerToolRegistry(sandbox, skills_config=str(config))


def test_registry_recall_rejects_host_path_traversal(tmp_path):
    from app.eval_container import ContainerToolRegistry
    from core.context import ContextBuilder
    from core.models import ToolCall

    workspace = tmp_path / "work"
    workspace.mkdir()
    config = tmp_path / "skills.json"
    config.write_text('{"discover": false}')
    offloads = workspace / ".vibe" / "offloads"
    offloads.mkdir(parents=True)
    secret = tmp_path / "secret.json"
    secret.write_text('[{"role": "user", "content": "private"}]')
    sandbox = type("Sandbox", (), {"workspace": workspace})()
    registry = ContainerToolRegistry(sandbox, ContextBuilder(offload_dir=offloads), skills_config=str(config))
    ok, output = registry.execute(ToolCall("recall", {"ref": "../../../secret"}))
    assert not ok and "invalid" in output
    assert "private" not in output


def test_registry_recall_rejects_nested_host_path_traversal(tmp_path):
    from app.eval_container import ContainerToolRegistry
    from core.context import ContextBuilder
    from core.models import ToolCall

    workspace = tmp_path / "work"
    workspace.mkdir()
    config = tmp_path / "skills.json"
    config.write_text('{"discover": false}')
    offloads = workspace / ".vibe" / "offloads"
    offloads.mkdir(parents=True)
    (tmp_path / "secret.json").write_text('[{"role": "user", "content": "private"}]')
    (offloads / "off-12345678.json").write_text('[{"role": "assistant", "content": "[offloaded: ../../../secret]", "name": "history_archive_ref"}]')
    sandbox = type("Sandbox", (), {"workspace": workspace})()
    registry = ContainerToolRegistry(sandbox, ContextBuilder(offload_dir=offloads), skills_config=str(config))
    ok, output = registry.execute(ToolCall("recall", {"ref": "off-12345678"}))
    assert not ok and "invalid" in output
    assert "private" not in output
