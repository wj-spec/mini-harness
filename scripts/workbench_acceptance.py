"""隔离的工作台真人验收环境。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from core.contracts import ToolCall


def prepare(root: Path, actions, environ):
    base = root / ".vibe" / "workbench-acceptance"
    actions.permit(ToolCall("write_file", {"path":str(base), "purpose":"隔离验收环境"}),
                   "在 .vibe/workbench-acceptance 创建临时工作区与虚构配置。")
    base.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    (fixture / "workspace").mkdir()
    python = str(root / ".venv" / "bin" / "python3") if (root / ".venv/bin/python3").exists() else sys.executable
    config = {"mcpServers": {name: {"command":python, "args":[str(root / "examples/mcp-demo/server.py")],
              "env":{"DEMO_LABEL":name}, "allowed_tools":["echo"], "risk":"read", "timeout":30}
              for name in ("alpha", "beta")}}
    values = {"VIBE_PROVIDER":"fake", "VIBE_MODEL":"acceptance-demo", "VIBE_API_KEY":"fixture-only-key",
              "VIBE_BASE_URL":"http://127.0.0.1:1/v1", "VIBE_TRUST_ENV":"0",
              "VIBE_MCP_CONFIG":str(fixture / "mcp.json"), "VIBE_AUTONOMY":"ask", "VIBE_ACCEPTANCE":"1"}
    import shlex
    files = {"mcp.json":json.dumps(config, ensure_ascii=False, indent=2)+"\n",
             ".env":"\n".join(key+"="+shlex.quote(value) for key,value in values.items())+"\n"}
    for name, content in files.items():
        path = fixture / name
        actions.permit(ToolCall("write_file", {"path":str(path), "purpose":"虚构验收配置"}), "创建验收配置 "+str(path))
        with path.open("x", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
    env = {key:value for key,value in environ.items()
           if not key.startswith(("VIBE_", "OPENAI_", "DEEPSEEK_"))}
    env.update(values)
    env.update({"PYTHON":python, "VIBE_CONFIG_FILE":str(fixture / ".env"), "VIBE_MODEL_ENV_KEYS":"[]",
                "VIBE_SETTINGS_ENV_KEYS":"[]", "VIBE_CONFIG_LOADED":"1", "VIBE_FAKE_DEMO":"1", "VIBE_TASK_RUNTIME":"1",
                "VIBE_PERMISSIONS":""})
    return fixture, env


def launch(root, actions, environ):
    fixture, env = prepare(root, actions, environ)
    print("验收工作区："+str(fixture / "workspace"))
    print("使用 Demo 模型和两个本地 MCP；启动时不会自动连接，进入后打开 /mcp 选择服务并连接，启动审批出现时选择本次允许。")
    print("验收步骤：docs/WORKBENCH-ACCEPTANCE.md；退出按 Ctrl+Q。")
    return actions.commands([
        ["bash", str(root / "run_ink.sh"), "--fake", "--workspace", str(fixture / "workspace"), "--test-session"]],
        env=env)
