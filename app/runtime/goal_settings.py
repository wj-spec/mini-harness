"""新目标默认预算的来源、审批保存与应用。"""
from app.runtime.goal_limits import FIELDS, goal_limits_from_env, validate_goal_limits
from app.runtime.limit_settings import LimitSettings, LimitSettingsError
from app.runtime.model_config import save_goal_config


class GoalSettings(LimitSettings):
    fields = FIELDS
    writer = staticmethod(save_goal_config)
    label = "目标总预算"

    def __init__(self, config, agent, **kwargs):
        super().__init__(config, agent, **kwargs)
        self.initial = goal_limits_from_env(self.env)
        self.current = dict(self.initial)

    def values(self):
        return dict(self.current)

    def sources(self):
        return {key: "进程环境" if name in self.env_keys else "本次选择" if self.applied else
                self.loaded_sources.get(name, "用户配置" if self.path.suffix == ".toml" else ".env") if self.env.get("VIBE_CONFIG_FILE") and name in self.env else "默认值"
                for key, name in FIELDS.items()}

    def candidate(self, args):
        if not isinstance(args, dict) or set(args) - {"action", "scope", "limits", "revision"} or args.get("scope") != "goal":
            raise LimitSettingsError("目标预算设置参数无效。")
        try:
            return validate_goal_limits(args.get("limits"))
        except ValueError as exc:
            raise LimitSettingsError(str(exc)) from None

    def apply(self, args):
        values = self.candidate(args)
        blocked = []
        for key, value in values.items():
            if FIELDS[key] in self.env_keys:
                blocked.append(key)
            else:
                self.current[key] = value
        self.applied = True
        return "已应用为新目标的总预算；现有目标和后台续做保持原快照。" + (
            " 仍受进程环境覆盖：" + "、".join(blocked) if blocked else "")
