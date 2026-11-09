"""P9：权限来源、有效期与进程内授权撤销的结构化视图。"""

from __future__ import annotations

from pathlib import Path

from app.safety import permissions


class PermissionGrantNotFound(ValueError):
    """请求撤销的进程内授权不存在、已撤销或不属于当前工作区。"""


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
        for rule in policy.describe_rules():
            source = "user" if rule.startswith("- [user]") else "project"
            path = user_path if source == "user" else project_path
            persistent.append({
                "source": source,
                "path": str(path) if path else "",
                "rule": rule,
                "validity": "file",
                "revocable": False,
                "reason": "持久策略在文件中，进程内不撤销；请修改文件后重开或下一次 run 重载",
            })
    return {
        "autonomy": _autonomy_view(agent),
        "grants": gate.grants(workspace) if gate is not None else [],
        "persistent": persistent,
        "error": getattr(agent, "permission_error", None),
        "revoked": None,
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
