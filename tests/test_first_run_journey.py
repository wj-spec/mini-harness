"""真实进程的首次启动旅程：无配置进入 Ink 后端 → 审批保存 → 进入项目。

不联网、不调用真实模型：只验证启动状态、审批、进程内 exec 与能力切换。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Process:
    def __init__(self, workspace: Path, environ: dict[str, str]):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "app", "--stdio", "--workspace", str(workspace)],
            cwd=ROOT, env=environ, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self.events: list[dict] = []
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def read(self, timeout: float = 30) -> dict:
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"no event within {timeout}s; last={self.events[-3:]}") from None
        if line is None:
            raise AssertionError(f"backend exited; last={self.events[-3:]}")
        event = json.loads(line)
        self.events.append(event)
        return event

    def until(self, kind: str, *, request_id: str | None = None, limit: int = 40) -> dict:
        for _ in range(limit):
            event = self.read()
            if event["type"] == kind and (request_id is None or event["id"] == request_id):
                return event
        raise AssertionError(f"missing {kind} {request_id}")

    def send(self, **frame) -> None:
        payload = {"v": 1, **frame}
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="select-based journey helper")
@pytest.mark.parametrize("filename", [".env", "config.toml"])
def test_first_run_journey_from_missing_config_to_project(tmp_path, filename):
    workspace = tmp_path / "project"
    workspace.mkdir()
    config = tmp_path / filename
    clean = {name: value for name, value in os.environ.items()
             if not name.startswith(("VIBE_", "OPENAI_", "DEEPSEEK_"))}
    clean.update({"VIBE_CONFIG_FILE": str(config), "PYTHONPATH": str(ROOT),
                  "VIBE_MODEL_ENV_KEYS": "[]", "VIBE_SETTINGS_ENV_KEYS": "[]"})
    run = Process(workspace, clean)
    try:
        ready = run.until("ready")
        assert ready["workspace"] == str(workspace.resolve())
        assert ready["bootstrap"]["reason"] == "missing_key"
        assert "config-bootstrap" in ready["capabilities"]
        assert "run" not in ready["capabilities"]
        run.until("status")
        assert not (workspace / ".vibe" / "sessions").exists()

        run.send(type="command", id="save", command="model", args={
            "action": "save", "model": "journey-model", "base_url": "http://127.0.0.1:1/v1",
            "api_key": "sk-journey-test", "trust_env": False, "revision": "0" * 64,
        })
        failure = run.until("error", request_id="save")
        assert failure["code"] == "model_settings_error"
        assert "已变化" in failure["message"]  # 未刷新版本时不写文件

        run.send(type="command", id="list", command="model", args={"action": "list"})
        overview = run.until("model_settings", request_id="list")
        run.send(type="command", id="save2", command="model", args={
            "action": "save", "model": "journey-model", "base_url": "http://127.0.0.1:1/v1",
            "api_key": "sk-journey-test", "trust_env": False, "revision": overview["revision"],
        })
        approval = run.until("approval_request")
        assert approval["call"]["name"] == "write_file"
        assert "sk-journey-test" not in json.dumps(approval, ensure_ascii=False)
        run.send(type="approval", id="ok", approval_id=approval["approval_id"], decision="once")
        if filename == "config.toml":
            credentials_approval = run.until("approval_request")
            assert credentials_approval["call"]["args"]["path"] == str(tmp_path / "credentials.json")
            run.send(type="approval", id="ok-credentials", approval_id=credentials_approval["approval_id"], decision="once")
        saved = run.until("model_settings", request_id="save2")
        assert "已保存" in saved["message"]
        assert config.exists()
        assert not (workspace / ".vibe" / "sessions").exists()

        run.send(type="command", id="enter", command="bootstrap", args={"action": "activate"})
        run.until("bootstrap_activation", request_id="enter")
        refreshed = run.until("ready")
        assert refreshed["capabilities"] and "run" in refreshed["capabilities"]
        assert "bootstrap" not in refreshed
        assert refreshed["mcp"] == {"configured": 0, "connected": 0}
        run.send(type="shutdown", id="bye")
        run.until("closed", request_id="bye")
        assert run.process.wait(timeout=10) == 0
        assert "sk-journey-test" not in run.process.stderr.read()
    finally:
        run.close()

    if filename == "config.toml":
        from app.runtime.user_config import startup_environment
        assert "sk-journey-test" not in config.read_text()
        assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600
        other = tmp_path / "another-project"
        other.mkdir()
        reopened = Process(other, startup_environment(ROOT, other, clean))
        try:
            ready = reopened.until("ready")
            assert "bootstrap" not in ready
            assert ready["workspace"] == str(other.resolve())
            reopened.send(type="command", id="model", command="model", args={"action": "list"})
            current = reopened.until("model_settings", request_id="model")
            assert current["model"] == "journey-model"
            assert current["key_present"] is True
            assert current["sources"]["model"] == "用户配置"
            reopened.send(type="shutdown", id="bye")
            reopened.until("closed", request_id="bye")
            assert reopened.process.wait(timeout=10) == 0
        finally:
            reopened.close()
