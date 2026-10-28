"""审批层级：副作用等级、可授予作用域与标签。

层级只描述副作用强度，不替代审批本身；作用域决定授权能覆盖多少调用。
矩阵是唯一事实来源：新增工具只声明 risk，作用域与展示由这里推导。
"""

from __future__ import annotations

TIERS = ("read", "write", "exec", "network", "remote_action")

TIER_LABELS = {
    "read": "T0 只读",
    "write": "T1 工作区写入",
    "exec": "T2 本地执行",
    "network": "T3 外部网络",
    "remote_action": "T4 远端页面动作",
}

RISK_TO_TIER = {
    "read": "read",
    "write": "write",
    "exec": "exec",
    "network": "network",
    "remote_action": "remote_action",
}

# 每个等级允许的作用域；workspace 仍需工具本身具备工作区语义。
TIER_SCOPES = {
    "read": (),
    "write": ("once", "session", "workspace"),
    "exec": ("once", "session", "workspace"),
    "network": ("once", "session"),
    "remote_action": ("once",),
}


def tier_for(risk: str) -> str:
    """未知风险按最保守的执行层处理（fail closed）。"""
    return RISK_TO_TIER.get(str(risk or "").strip().lower(), "exec")


def label(tier: str) -> str:
    return TIER_LABELS.get(tier, tier)


def scopes_for(tier: str, *, workspace_eligible: bool = False) -> list[str]:
    """返回给定等级可提供的作用域（末尾总是 deny）。"""
    scopes = [scope for scope in TIER_SCOPES.get(tier, ()) if scope != "workspace" or workspace_eligible]
    return [*scopes, "deny"]


def scope_allowed(tier: str, scope: str, *, workspace_eligible: bool = False) -> bool:
    if scope == "deny" or scope == "once":
        return True
    return scope in TIER_SCOPES.get(tier, ()) and (scope != "workspace" or workspace_eligible)
