"""P9：权限来源、有效期与进程内授权撤销的结构化视图。"""

from __future__ import annotations

import re
from pathlib import Path

from app.safety import permissions
from app.safety.permission_policy import PolicyError, RuleNotFound, remove_user_rule


class PermissionGrantNotFound(ValueError):
    """请求撤销的进程内授权不存在、已撤销或不属于当前工作区。"""


class PermissionRuleNotFound(ValueError):
    """请求撤销的用户策略规则不存在或已被外部修改。"""


class PermissionRuleNotRevocable(ValueError):
    """该持久规则不能在进程内撤销（项目策略/用户策略未启用/文件不可用）。"""


def _autonomy_view(agent) -> dict:
    policy = getattr(agent, "permission_policy", None)
    effective = getattr(agent, "autonomy", "ask")
    cli = getattr(agent, "_cli_autonomy", None)
    user_default = getattr(policy, "autonomy", "ask") if policy is not None else "ask"
    cap = getattr(policy, "autonomy_cap", None) if policy is not None else None
    if cli:
        source, requested = "cli", cli
    elif user_default and user_default != "ask":
        source, requested = "user-policy", user_default
    else:
        source, requested = "default", "ask"
    return {
        "level": effective,
        "label": permissions.AUTONOMY_LABELS.get(effective, effective),
        "source": source,
        "requested": requested,
        "cap": cap,
    }


def _policy_paths(agent) -> tuple[Path | None, Path | None]:
    getter = getattr(agent, "_permissions_paths", None)
    if not callable(getter):
        return (None, None)
    try:
        project, user = getter()
    except (OSError, TypeError, ValueError):
        return (None, None)
    return (Path(project) if project else None, Path(user) if user else None)


def permission_overview(agent, workspace) -> dict:
    """当前进程的有效权限快照；只读，不改变任何授权。"""
    gate = getattr(agent, "approval", None)
    policy = getattr(agent, "permission_policy", None)
    project_path, user_path = _policy_paths(agent)
    persistent: list[dict] = []
    if policy is not None:
        for entry in policy.describe_entries():
            source = entry["source"]
            path = user_path if source == "user" else project_path
            persistent.append({
                "id": entry["id"],
                "source": source,
                "path": str(path) if path else "",
                "rule": entry["rule"],
                "validity": "file",
                "revocable": bool(entry["revocable"]),
                "reason": "用户策略可在此撤销（会写入用户策略文件）" if source == "user"
                else "项目策略只能收紧；撤销会放宽权限，请修改仓库文件",
            })
    return {
        "autonomy": _autonomy_view(agent),
        "grants": gate.grants(workspace) if gate is not None else [],
        "persistent": persistent,
        "error": getattr(agent, "permission_error", None),
        "revoked": None,
        "revoked_rule": None,
    }


def revoke_grant(agent, workspace, grant_id: str) -> dict:
    """撤销单条进程内授权；返回撤销后的完整概览（含 revoked 回执）。"""
    gate = getattr(agent, "approval", None)
    revoked = gate.revoke_grant(workspace, grant_id) if gate is not None else None
    if revoked is None:
        raise PermissionGrantNotFound(f"没有可撤销的进程内授权 {grant_id!r}；刷新后重试")
    overview = permission_overview(agent, workspace)
    overview["revoked"] = {key: revoked[key] for key in ("id", "kind", "scope", "tool", "target")}
    return overview


def revoke_rule(agent, workspace, rule_id: str) -> dict:
    """撤销用户级持久策略规则（写回用户策略文件并重载）；项目规则拒绝。"""
    if not isinstance(rule_id, str) or not re.fullmatch(r"(?:ur|pr)-[0-9a-f]{12}", rule_id):
        raise PermissionRuleNotFound(f"没有可撤销的用户策略规则 {rule_id!r}；刷新后重试")
    project_path, user_path = _policy_paths(agent)
    if user_path is None:
        raise PermissionRuleNotRevocable("用户策略未启用（VIBE_PERMISSIONS 为空）；持久规则不可在进程内撤销")
    policy = getattr(agent, "permission_policy", None)
    entries = policy.describe_entries() if policy is not None else ()
    entry = next((item for item in entries if item["id"] == rule_id), None)
    if entry is None:
        raise PermissionRuleNotFound(f"没有找到可撤销的用户策略规则 {rule_id!r}；刷新后重试")
    if entry["source"] != "user":
        raise PermissionRuleNotRevocable("项目策略只能收紧；撤销会放宽权限，请修改仓库文件")
    if not user_path.is_file():
        raise PermissionRuleNotFound("用户策略文件不存在；刷新后重试")
    try:
        remove_user_rule(user_path, rule_id)
    except RuleNotFound as exc:
        raise PermissionRuleNotFound(f"没有找到可撤销的用户策略规则 {rule_id!r}；刷新后重试") from exc
    except PolicyError as exc:
        raise PermissionRuleNotRevocable(f"用户策略文件不可用，未撤销：{exc}") from exc
    agent._reload_permission_policy()
    overview = permission_overview(agent, workspace)
    overview["revoked_rule"] = {"id": rule_id, "source": "user", "path": str(user_path), "rule": entry["rule"]}
    return overview
