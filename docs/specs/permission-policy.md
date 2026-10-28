# 用户可覆盖的审批策略

日期：2026-10-28。状态：已实现（`app/permission_policy.py` + `ApprovalGate.policy` + Agent 每 run 重载）；TUI 目前通过 `/permissions` 显示规则计数与错误，逐条可视化与撤销待做。

## 1. 目标

默认层级（T0–T4）与作用域矩阵给出通用边界；用户需要能按自己的习惯**覆盖**默认审批行为，而不改代码：

- 收紧：某些工具/命令永远拒绝或必须逐次确认；
- 放宽：明确信任的命令（如本地测试）自动放行，减少审批负担；
- 限定作用域：只允许 once，或只允许 session；
- 调整层级：把某工具归到更高/更低层级，影响可授予作用域与展示。

未来新增层级时只扩 `app/permissions.py` 的 `TIERS/TIER_SCOPES`，策略文件按名字引用，无需改匹配逻辑。

## 2. 文件与来源

| 来源 | 路径 | 权限 |
|---|---|---|
| 用户级 | `~/.vibe/permissions.json`（`VIBE_PERMISSIONS` 可覆盖；设为空串表示禁用） | 可收紧、可放宽（allow）、可限定作用域、可改层级 |
| 项目级 | `<workspace>/.vibe/permissions.json` | **只能收紧**（deny / once / 限定作用域 / 提高层级）；禁止 `allow` 与 `command_regex` |

原因：项目文件可能随仓库分发，若允许项目级放宽，等于让仓库自带"自我授权"。用户级文件由用户自己维护，才能放宽。

每次 run 重新加载（与项目背景/个人偏好一致）；文件非法时**保留上一份有效策略**并记录 `permission_policy_error` 事件，`/permissions` 显示错误原因。

## 3. 规则格式

```json
{
  "version": 1,
  "rules": [
    {"tool": "bash", "command_prefix": ["pytest", "npm test"], "decision": "allow", "note": "本地测试"},
    {"tool": "bash", "command_prefix": "rm -rf", "decision": "once", "note": "删除类逐次确认"},
    {"tool": "bash", "command_regex": "^curl\\s", "decision": "deny"},
    {"tool": "web_search", "scopes": ["once"]},
    {"tool": "mcp_*", "tier": "exec", "scopes": ["once"], "note": "MCP 一律逐次"}
  ]
}
```

字段：

| 字段 | 说明 |
|---|---|
| `tool` | 必填，工具名；支持 glob（`mcp_*`） |
| `decision` | `allow` / `deny` / `once`（`allow` 仅用户级） |
| `scopes` | 允许的作用域子集（`once`/`session`/`workspace`），与层级矩阵交集 |
| `tier` | 覆盖层级（`read`/`write`/`exec`/`network`/`remote_action`） |
| `command_prefix` | 仅 `bash`：命令前缀（字符串或列表），任一命中即匹配 |
| `command_regex` | 仅 `bash`、仅用户级：正则（≤200 字符，加载时编译校验） |
| `note` | 可选说明，便于 `/permissions` 与审计核对 |

未知字段、未知 decision/tier/scope、非法正则、版本不为 1 一律拒绝加载（fail closed）。

## 4. 决策优先级

1. **硬边界**：黑名单、写路径护栏、沙箱（不可被策略覆盖；策略不能把沙箱外写入变成允许）；
2. **项目级规则**（不可放宽，优先于用户级）；
3. **用户级规则**；
4. 默认层级 + 作用域矩阵；
5. 审批处理器；无处理器默认拒绝。

`decision=allow` 的额外限制：T4（远端页面动作）不允许 allow，即使写了也会被拒绝（`deny_policy_floor`），因为远端动作依赖快照绑定与逐次确认。

`once` 表示跳过 session/workspace 已有授权、强制逐次确认；`scopes` 与默认矩阵取交集。

审计新增：`policy_allow`、`deny_policy`、`deny_policy_once`、`deny_policy_scope`、`deny_policy_floor`，与 `denied_by_user`/`sandbox_denied` 等可区分。

## 5. 与评测/基准的隔离

评测与基准应显式设置 `VIBE_PERMISSIONS=""`，避免开发机的个人策略改变模型可执行范围，影响结果可比性。

## 6. 验收

- `tests/test_permission_policy.py`：解析校验、glob/前缀/正则匹配、项目与用户优先级、层级只可收紧、allow 跳过处理器、once/作用域限制、T4 下限、Agent 每 run 重载与非法策略保留上一份。
- 既有 `tests/test_policy.py`、`tests/test_permissions.py`、`tests/test_agent.py` 不回退。
- `/permissions` 显示 user/project 规则数量与策略错误。

## 7. 后续

- TUI 逐条展示规则、来源与撤销入口；
- 审批统计（once/session/workspace/policy）写入交付回执；
- destructive 层与"沙箱下自动批准"作为独立开关评估；
- 规则热更新的显式确认（当前是静默每 run 重载，改动需用户自己负责）。
