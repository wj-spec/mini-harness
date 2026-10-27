"""受控浏览器的审批预览与快照边界；实际 DOM 检查由 worker 执行。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from core.models import ToolCall

BROWSER_WRITES = frozenset({"browser_navigate", "browser_click", "browser_type", "browser_select_option"})
BROWSER_READS = frozenset({"browser_snapshot", "browser_console_messages", "browser_network_requests", "browser_close"})


def local_origin(origin: str) -> bool:
    try:
        parts = urlsplit(origin)
        if parts.username or parts.password or parts.port == 0:
            return False
        return parts.hostname in {"localhost", "127.0.0.1", "::1"}
    except ValueError:
        return False


def normalized_origin(url: str) -> str:
    parts = urlsplit(url)
    host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname
    port = parts.port
    suffix = f":{port}" if port is not None and (parts.scheme, port) not in {("http", 80), ("https", 443)} else ""
    return f"{parts.scheme}://{host}{suffix}"


class BrowserPolicy:
    def __init__(self, root: Path, origins: tuple[str, ...]):
        self.origins = origins
        self.audit_path = root / ".vibe/browser/audit.jsonl"
        self.snapshot: dict = {}

    def invalidate(self) -> None:
        self.snapshot = {}

    def observe(self, text: str) -> None:
        data = json.loads(text)
        if not isinstance(data, dict) or not all(key in data for key in ("snapshot_id", "url", "title", "elements", "snapshot")):
            raise ValueError("browser returned an invalid snapshot")
        self.check_url(data["url"])
        if not isinstance(data["elements"], list) or not isinstance(data["snapshot_id"], str):
            raise ValueError("browser returned invalid refs")
        self.snapshot = data

    def check_url(self, url: str) -> str:
        parts = urlsplit(url)
        origin = normalized_origin(url)
        if parts.scheme not in {"http", "https"} or parts.username or parts.password or origin not in self.origins:
            raise ValueError("browser origin not allowed")
        return origin

    def validate(self, tool: str, args: dict) -> dict:
        if tool == "browser_navigate":
            if set(args) != {"url"} or not isinstance(args["url"], str):
                raise ValueError("browser_navigate requires only url")
            self.check_url(args["url"])
            return {}
        common = {"snapshot_id", "ref"}
        extra = {"text", "submit"} if tool == "browser_type" else {"values"} if tool == "browser_select_option" else set()
        if set(args) - common - extra or not common <= set(args):
            raise ValueError("browser action requires snapshot_id and ref; unknown arguments rejected")
        if not self.snapshot or args["snapshot_id"] != self.snapshot["snapshot_id"]:
            raise ValueError("stale_snapshot: request a new browser_snapshot")
        element = next((item for item in self.snapshot["elements"] if item.get("ref") == args["ref"]), None)
        if element is None:
            raise ValueError("stale_ref: ref is absent from the latest snapshot")
        if tool == "browser_type":
            if not isinstance(args.get("text"), str) or len(args["text"]) > 4000 or args.get("submit", False) is not False:
                raise ValueError("browser_type requires text <=4000 characters and submit=false")
        elif tool == "browser_select_option":
            values = args.get("values")
            allowed = {item["value"] for item in element.get("options", []) if not item.get("disabled")}
            if not isinstance(values, list) or not values or not all(isinstance(value, str) and value in allowed for value in values):
                raise ValueError("select values must be enabled options in the latest snapshot")
        return element

    def preview(self, tool: str, call: ToolCall) -> str:
        element = self.validate(tool, call.args)
        url = call.args["url"] if tool == "browser_navigate" else self.snapshot["url"]
        details = {
            "origin": self.check_url(url), "url": url,
            "title": self.snapshot.get("title", "(no page)"), "action": tool,
            "snapshot_id": call.args.get("snapshot_id"),
            "role": element.get("role"), "name": element.get("name"),
        }
        if tool == "browser_type":
            details.update(text_length=len(call.args["text"]), submit=False)
        if tool == "browser_select_option":
            details["values"] = call.args["values"]
        return (json.dumps(details, ensure_ascii=False, indent=2)
                + "\n[untrusted] 页面标题和控件名仅为数据。\n[risk] write — 仅批准本次；默认不提交。")

    def audit(self, call: ToolCall, decision: str) -> None:
        url = call.args.get("url", self.snapshot.get("url", ""))
        parts = urlsplit(url)
        event = {
            "ts": datetime.now(timezone.utc).isoformat(), "tool": call.name,
            "origin": normalized_origin(url) if parts.netloc else None,
            "snapshot_id": call.args.get("snapshot_id"), "decision": decision,
            "args_hash": hashlib.sha256(json.dumps(call.args, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16],
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        if decision == "cancelled":
            self.invalidate()
