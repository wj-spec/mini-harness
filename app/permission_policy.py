"""用户可覆盖的审批策略：在默认层级与作用域矩阵之上收紧或放宽。

规则匹配工具名（glob）与可选的 bash 命令前缀；来源分 user（可放宽）与
project（只收紧）。每次 run 重新加载，与项目背景/偏好一致。
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app import permissions

DECISIONS = {"deny", "allow", "once"}
SCOPES = {"once", "session", "workspace"}
TIER_RANK = {"read": 0, "write": 1, "exec": 2, "network": 3, "remote_action": 4}


class PolicyError(ValueError):
    """策略文件不合法；调用方应保留上一份策略或拒绝启动。"""


@dataclass(frozen=True)
class Rule:
    source: str
    tool: str
    decision: str | None = None
    scopes: tuple[str, ...] | None = None
    tier: str | None = None
    command_prefix: tuple[str, ...] = ()
    command_regex: str | None = None
    note: str = ""

    def matches(self, call, command: str) -> bool:
        if not fnmatch.fnmatchcase(call.name, self.tool):
            return False
        if self.command_prefix:
            return any(command.startswith(prefix) for prefix in self.command_prefix)
        if self.command_regex:
            try:
                return re.search(self.command_regex, command) is not None
            except re.error:
                return False
        return True


@dataclass(frozen=True)
class PolicyOutcome:
    decision: str | None = None
    scopes: tuple[str, ...] | None = None
    tier: str | None = None
    rule: Rule | None = None


def _as_strings(value, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item.strip() for item in value):
        raise PolicyError(f"rule {field} 必须是非空字符串或字符串列表")
    cleaned = tuple(item.strip() for item in value)
    if not allow_empty and any(not item for item in cleaned):
        raise PolicyError(f"rule {field} 不能为空")
    return cleaned


def _parse_rule(source: str, raw: dict, index: int) -> Rule:
    if not isinstance(raw, dict):
        raise PolicyError(f"{source} rules[{index}] 必须是对象")
    tool = raw.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise PolicyError(f"{source} rules[{index}].tool 必须是非空字符串")
    decision = raw.get("decision")
    if decision is not None and decision not in DECISIONS:
        raise PolicyError(f"{source} rules[{index}].decision 必须是 {sorted(DECISIONS)}")
    scopes = raw.get("scopes")
    if scopes is not None:
        scopes = _as_strings(scopes, "scopes")
        unknown = set(scopes) - SCOPES
        if unknown:
            raise PolicyError(f"{source} rules[{index}].scopes 含未知值 {sorted(unknown)}")
    tier = raw.get("tier")
    if tier is not None and tier not in permissions.TIERS:
        raise PolicyError(f"{source} rules[{index}].tier 含未知层级 {tier!r}")
    command_prefix = raw.get("command_prefix")
    if command_prefix is not None:
        command_prefix = _as_strings(command_prefix, "command_prefix")
    command_regex = raw.get("command_regex")
    if command_regex is not None:
        if not isinstance(command_regex, str) or not command_regex.strip():
            raise PolicyError(f"{source} rules[{index}].command_regex 必须是非空字符串")
        if len(command_regex) > 200:
            raise PolicyError(f"{source} rules[{index}].command_regex 过长（上限 200）")
        try:
            re.compile(command_regex)
        except re.error as exc:
            raise PolicyError(f"{source} rules[{index}].command_regex 无效：{exc}") from None
    if command_prefix and command_regex:
        raise PolicyError(f"{source} rules[{index}] 不能同时设置 command_prefix 与 command_regex")
    if source == "project":
        if decision == "allow":
            raise PolicyError("project 策略只能收紧，不能 decision=allow")
        if command_regex:
            raise PolicyError("project 策略不允许 command_regex（避免仓库携带复杂匹配）")
    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        raise PolicyError(f"{source} rules[{index}].note 必须是字符串")
    return Rule(source=source, tool=tool.strip(), decision=decision, scopes=scopes,
                tier=tier, command_prefix=command_prefix or (),
                command_regex=command_regex, note=(note or "").strip())


def _parse_document(source: str, raw) -> list[Rule]:
    if not isinstance(raw, dict):
        raise PolicyError(f"{source} 策略必须是 JSON 对象")
    version = raw.get("version", 1)
    if version != 1:
        raise PolicyError(f"{source} 策略 version 只支持 1")
    rules = raw.get("rules", [])
    if not isinstance(rules, list):
        raise PolicyError(f"{source} rules 必须是数组")
    unknown = set(raw) - {"version", "rules"}
    if unknown:
        raise PolicyError(f"{source} 含未知字段 {sorted(unknown)}")
    return [_parse_rule(source, item, index) for index, item in enumerate(rules)]


class PermissionPolicy:
    """有序规则集：project 规则优先，未命中才看 user 规则。"""

    def __init__(self, rules: tuple[Rule, ...] = ()):
        self.rules = tuple(rules)

    @classmethod
    def from_files(cls, project: Path | None, user: Path | None) -> "PermissionPolicy":
        rules: list[Rule] = []
        for source, path in (("project", project), ("user", user)):
            if path is None or not Path(path).is_file():
                continue
            try:
                raw = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PolicyError(f"{source} 策略读取失败 {path}: {exc}") from None
            rules.extend(_parse_document(source, raw))
        return cls(tuple(rules))

    def evaluate(self, call, *, tier: str) -> PolicyOutcome:
        command = str(call.args.get("command", "")) if call.name == "bash" else ""
        matches = [rule for rule in self.rules if rule.matches(call, command)]
        project = next((rule for rule in matches if rule.source == "project"), None)
        user = next((rule for rule in matches if rule.source == "user"), None)
        chosen = project or user
        if chosen is None:
            return PolicyOutcome()
        tier_override = chosen.tier
        if chosen.source == "project" and tier_override is not None:
            # project 只能把层级调得更严格；放宽交给 user 规则。
            if TIER_RANK.get(tier_override, 99) < TIER_RANK.get(tier, 99):
                tier_override = None
        return PolicyOutcome(decision=chosen.decision, scopes=chosen.scopes,
                             tier=tier_override, rule=chosen)

    def tier_for(self, call, default_tier: str) -> str:
        return self.evaluate(call, tier=default_tier).tier or default_tier

    def summary(self) -> dict:
        return {
            "user_rules": sum(1 for rule in self.rules if rule.source == "user"),
            "project_rules": sum(1 for rule in self.rules if rule.source == "project"),
        }
