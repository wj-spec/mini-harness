#!/usr/bin/env python3
"""真实 Ink/Chromium 的确定性审批回归；模型仅使用本地 HTTP stub。"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import http.server
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import threading
import time
import uuid
from pathlib import Path

from scripts.browser_actions_smoke import fixture_server
from scripts.browser_acceptance_evidence import DRAFT, REJECTED_DRAFT, collect_report

ROOT = Path(__file__).resolve().parents[1]


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            # 追加中的尾行尚未写完。
            pass
    return result


@contextlib.contextmanager
def model_server(origin: str):
    state = {"calls": [], "requests": 0, "error": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.path == "/v1/chat/completions"
                assert self.headers.get("Authorization") == "Bearer local-stub-placeholder"
                tools = [item["function"]["name"] for item in payload.get("tools", [])]
                snapshots = []
                for message in payload["messages"]:
                    if message.get("role") == "tool":
                        content = str(message.get("content", "")).removeprefix("[untrusted browser data]\n")
                        try:
                            data = json.loads(content)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(data, dict) and "snapshot_id" in data:
                            snapshots.append(data)
                index = state["requests"]
                state["requests"] += 1
                plan = ["browser_navigate", "browser_click", "browser_type", "browser_select_option", None,
                        "browser_type", "browser_snapshot", None]
                if index < len(plan) and plan[index] is not None:
                    remote = plan[index]
                    name = next(tool for tool in tools if tool.startswith("mcp_browser_" + remote))
                    args = {"url": origin + "/"} if index == 0 else {}
                    if remote in {"browser_click", "browser_type", "browser_select_option"}:
                        snapshot = snapshots[-1]
                        label = "Increment" if remote == "browser_click" else "Shade" if remote == "browser_select_option" else "Note"
                        element = next(item for item in snapshot["elements"] if item["name"] == label)
                        args = {"snapshot_id": snapshot["snapshot_id"], "ref": element["ref"]}
                        if remote == "browser_type":
                            args["text"] = REJECTED_DRAFT if index == 5 else DRAFT
                        elif remote == "browser_select_option":
                            args["values"] = ["green"]
                    call = {"id": f"ink-{index}", "name": name, "remote": remote, "args": args}
                    state["calls"].append(call)
                    delta = {"tool_calls": [{"index": 0, "id": call["id"], "type": "function",
                                              "function": {"name": name, "arguments": json.dumps(args)}}]}
                    finish = "tool_calls"
                else:
                    delta, finish = {"content": "确定性本地浏览器检查完成。"}, "stop"
                chunk = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}
                body = ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                state["error"] = f"{type(exc).__name__}: {exc}"
                self.send_error(500, "local scripted model failed")

        def log_message(self, *_args):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}/v1", state
        finally:
            server.shutdown()
            thread.join(timeout=2)


def main() -> int:
    run_dir = ROOT / ".vibe/browser/ink-smoke" / uuid.uuid4().hex[:12]
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True)
    raw = bytearray()
    report = {"kind": "deterministic local model stub", "passed": False, "real_model_verified": False,
              "user_experience": "requires_user_confirmation"}
    process = None
    master = slave = None
    try:
        with fixture_server() as (origin, fixture), model_server(origin) as (base_url, model):
            config = json.loads((ROOT / "examples/browser-actions-mcp.json").read_text())
            browser = config["mcpServers"]["browser"]
            browser.update(cwd=str(ROOT), allowed_origins=[origin])
            browser["env"]["VIBE_BROWSER_NO_SANDBOX"] = "1"
            config_path = run_dir / "mcp.json"
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            env = {**os.environ, "TERM": "xterm-256color", "CI": "", "NO_COLOR": "1",
                   "VIBE_API_KEY": "local-stub-placeholder", "OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": "",
                   "VIBE_PROVIDER": "openai", "VIBE_MODEL": "b2-ink-scripted", "VIBE_BASE_URL": base_url,
                   "VIBE_TRUST_ENV": "0",
                   "VIBE_MCP_CONFIG": str(config_path), "VIBE_MCP_SERVERS": "", "VIBE_DEBUG": "0",
                   "VIBE_MAX_RUN_TOKENS": "30000", "VIBE_MAX_RUN_SECONDS": "120", "VIBE_LLM_RETRIES": "0",
                   "PYTHON": str(ROOT / ".venv/bin/python3")}
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 46, 140, 0, 0))
            process = subprocess.Popen(
                [str(ROOT / "run_ink.sh"), "--test-session", "--max-steps", "12", "--workspace", str(workspace)],
                cwd=ROOT, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True,
            )
            report["client_pid"] = process.pid
            os.close(slave)
            slave = None
            deadline = time.monotonic() + 120

            def pump(timeout=0.05):
                if select.select([master], [], [], timeout)[0]:
                    try:
                        raw.extend(os.read(master, 131072))
                        (run_dir / "terminal.log").write_bytes(raw)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise

            def until(predicate, label):
                report["waiting_for"] = label
                (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                while time.monotonic() < deadline:
                    pump()
                    if predicate():
                        return
                    if model["error"]:
                        raise AssertionError(model["error"])
                    if process.poll() is not None:
                        raise AssertionError(f"Ink exited before {label}: {process.returncode}")
                raise AssertionError(f"timeout waiting for {label}")

            def terminal():
                return raw.decode("utf-8", errors="replace")

            def idle_visible():
                headers = re.findall(r"◀ ([^▶\n]+) ▶", terminal())
                return bool(headers) and headers[-1] in {"就绪", "本轮结束"}

            def send_task(prompt):
                until(idle_visible, "Ink idle")
                os.write(master, prompt.encode())
                until(lambda: prompt in terminal(), "composer text")
                os.write(master, b"\r")

            until(lambda: "审批 › mcp_start" in terminal(), "MCP startup approval")
            os.write(master, b"y")
            until(lambda: "MCP 初始化完成：8 个工具" in terminal(), "MCP ready")
            send_task("Run the local fixture browser checks, then report observed results.")
            sessions = workspace / ".vibe/sessions"
            until(lambda: len(list(sessions.glob("*/trace.jsonl"))) == 1, "session trace")
            session = next(sessions.iterdir())
            trace_path = session / "trace.jsonl"
            scope_checks = []
            for index in range(5):
                if index == 4:
                    until(lambda: len([e for e in rows(trace_path) if e.get("type") == "run_end"]) == 1,
                          "first completed run")
                    send_task("Attempt the rejected Note change, then verify the original draft remains.")
                until(lambda i=index: len(model["calls"]) > i, f"model call {index}")
                call = model["calls"][index]
                if call["remote"] == "browser_snapshot":
                    break
                def approval_visible(c=call):
                    screen = terminal()
                    screen = screen[screen.rfind("审批 › "):]
                    return screen.startswith("审批 › " + c["name"]) and "y 允许一次 / n 拒绝" in screen

                until(lambda c=call: any(e.get("type") == "tool_start" and e.get("tool_call_id") == c["id"]
                                         for e in rows(trace_path))
                      and approval_visible(),
                      f"once-only approval {call['remote']}")
                if index == 0:
                    for key in (b"s", b"p"):
                        os.write(master, key)
                        stop = time.monotonic() + 0.25
                        while time.monotonic() < stop:
                            pump()
                        assert not any(e.get("type") in {"approval", "tool_end"}
                                       and e.get("tool_call_id") == call["id"] for e in rows(trace_path))
                        scope_checks.append(key.decode())
                os.write(master, b"n" if index == 4 else b"y")
                until(lambda c=call: any(e.get("type") == "tool_end" and e.get("tool_call_id") == c["id"]
                                         for e in rows(trace_path)), f"result {call['remote']}")
            until(lambda: len([e for e in rows(trace_path) if e.get("type") == "run_end"]) == 2, "completed runs")
            trace = rows(trace_path)
            assert next(e for e in reversed(trace) if e.get("type") == "run_end")["status"] == "done"
            assert next(e for e in trace if e.get("type") == "tool_end" and e.get("tool_call_id") == "ink-5")["status"] == "denied"
            messages = [e["message"] for e in rows(session / "events.jsonl") if e.get("type") == "message"]
            snapshot = json.loads(next(m["content"] for m in reversed(messages)
                                      if m.get("role") == "tool" and m.get("tool_call_id") == "ink-6")
                                  .removeprefix("[untrusted browser data]\n"))
            assert "count 1" in snapshot["snapshot"]
            assert next(e for e in snapshot["elements"] if e["name"] == "Note")["value"] == DRAFT
            assert next(o for e in snapshot["elements"] if e["name"] == "Shade" for o in e["options"] if o["value"] == "green")["selected"]
            assert fixture["submissions"] == 0
            os.write(master, b"\x11")
            until(lambda: process.poll() is not None, "Ink shutdown")
            for _ in range(5):
                pump(0.02)
            assert process.returncode == 0
            assert b"\x1b[?1049h" in raw and b"\x1b[?1049l" in raw
            evidence = collect_report(workspace, set(), origin=origin,
                                      submissions=fixture["submissions"], exit_code=process.returncode)
            report["acceptance_checks"] = evidence["checks"]
            assert evidence["passed"], evidence
            report.pop("waiting_for", None)
            report.update(passed=True, session=str(session), model_requests=model["requests"],
                          ignored_scope_keys=scope_checks, submissions=fixture["submissions"],
                          exit_code=process.returncode, terminal_restored=True)
        print(f"B2 Ink smoke: PASS — deterministic local model stub; evidence={run_dir}")
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(f"B2 Ink smoke: FAIL — {report['error']}; evidence={run_dir}")
        return 1
    finally:
        (run_dir / "terminal.log").write_bytes(raw)
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if process is not None and process.poll() is None:
            if master is not None:
                with contextlib.suppress(OSError):
                    os.write(master, b"\x11")
            ending = time.monotonic() + 6
            while process.poll() is None and time.monotonic() < ending:
                pump(0.05)
            if process.poll() is None:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, sig)
                    ending = time.monotonic() + 3
                    while process.poll() is None and time.monotonic() < ending:
                        pump(0.05)
        if master is not None:
            os.close(master)
        if slave is not None:
            os.close(slave)
        (run_dir / "terminal.log").write_bytes(raw)
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
