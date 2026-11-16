"""目标总预算：默认值、任务快照与续段门。"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

FIELDS = {"max_segments": "VIBE_GOAL_MAX_SEGMENTS", "max_seconds": "VIBE_GOAL_SECONDS",
          "max_tokens": "VIBE_GOAL_MAX_TOKENS", "max_cost_usd": "VIBE_GOAL_MAX_COST_USD"}
DEFAULTS = {"max_segments": 8, "max_seconds": 14400, "max_tokens": 0, "max_cost_usd": 0}


def validate_goal_limits(values):
    if not isinstance(values, dict) or set(values) != set(FIELDS):
        raise ValueError("请提供目标总段数、秒数、token 和估算费用。")
    for key, value in values.items():
        if type(value) not in (int, float) or not 0 <= value <= 1e12 or not math.isfinite(value):
            raise ValueError("目标预算须为 0 至 10¹² 的有限数字。")
        if key in {"max_segments", "max_tokens"} and type(value) is not int:
            raise ValueError("目标总段数和 token 必须为整数。")
    if values["max_segments"] < 1 or values["max_seconds"] < 60:
        raise ValueError("目标总段数至少 1，总时长至少 60 秒。")
    return dict(values)


def goal_limits_from_env(environ=None):
    env = os.environ if environ is None else environ
    values = dict(DEFAULTS)
    for key, name in FIELDS.items():
        try:
            value = float(env.get(name, DEFAULTS[key]))
            if not math.isfinite(value) or not 0 <= value <= 1e12:
                continue
            if key in {"max_segments", "max_tokens"}:
                value = int(value)
            values[key] = max(1, value) if key == "max_segments" else max(60, value) if key == "max_seconds" else value
        except (ValueError, TypeError, OverflowError):
            pass
    return validate_goal_limits(values)


def legacy_goal_limits(root, task, defaults):
    """仅迁移身份匹配的旧目标；损坏的匹配记录不静默放宽。"""
    legacy = root / "goal.json"
    if legacy.exists():
        value = json.loads(legacy.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("taskId") == task.task_id and value.get("objective") == task.objective:
            return validate_goal_limits(dict(zip(FIELDS, (
                value.get("maxSegments"), value.get("maxSeconds"),
                value.get("maxTotalTokens"), value.get("maxTotalCostUsd")))))
    path = root / "goal-record.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("task_id") == task.task_id:
            budget = value.get("budget", {})
            result = dict(defaults)
            for key, old in (("max_segments","max_goal_segments"),("max_seconds","max_goal_seconds"),
                             ("max_tokens","max_goal_tokens"),("max_cost_usd","max_goal_cost_usd")):
                if old not in budget:
                    continue
                raw = budget[old]
                if type(raw) not in (int, float) or not math.isfinite(raw) or raw < 0:
                    raise ValueError("旧目标预算无效。")
                # 旧 GoalRecord 的零值也用于未记录，不能据此取消启动保护。
                if key == "max_segments" or raw != 0:
                    result[key] = raw
            return validate_goal_limits(result)
    return validate_goal_limits(defaults)


def goal_limit_reason(task, totals, *, now=None):
    limits = validate_goal_limits(task.extra["goal_limits"])
    if len(task.segments) >= limits["max_segments"]:
        return "goal_max_segments"
    started = datetime.fromisoformat(task.created_at)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if ((now or datetime.now(timezone.utc)) - started).total_seconds() >= limits["max_seconds"]:
        return "goal_max_seconds"
    for field, total, reason in (("max_tokens","tokens_total","goal_max_tokens"),
                                 ("max_cost_usd","cost_usd","goal_max_cost_usd")):
        used = totals.get(total, 0)
        if not isinstance(used, (int, float)) or not math.isfinite(used) or used < 0:
            raise ValueError("目标累计账本无效，无法核对预算。")
        if limits[field] > 0 and used >= limits[field]:
            return reason
    return None
