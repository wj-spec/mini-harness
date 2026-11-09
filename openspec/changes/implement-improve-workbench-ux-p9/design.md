# P9 设计

## 事实与取舍

当前 `/permissions` 由 stdio 拼文本：workspace 是否开启、session 精确规则条数、档位、策略规则明细。`ApprovalGate` 运行时授权是两组集合：`workspace_allowed`（工作区根）与 `session_allowed`（(workspace, tool, target) 键），`clear()` 只能整体清空。`PermissionPolicy` 有 user/project 两个持久文件（`~/.vibe/permissions.json` 与 `<workspace>/.vibe/permissions.json`），规则不可在进程内撤销。档位 effective = `strictest_autonomy(cli_override_or_user_default, project_cap)`。

取舍：不改审批判定与集合结构；在只读视图侧推导稳定 id 与撤销入口，集合仍是唯一事实来源。持久策略写回需要处理文件并发、用户级路径与确认语义，本批不碰。

## 授权来源与有效期

| 来源 | 呈现 | 有效期 | 可撤销 |
|---|---|---|---|
| 审批档位 autonomy | effective 档位 + 来源（CLI 本进程 / user 策略默认 / 默认 ask）+ project 上限 | 本进程（CLI）或每次 run 重载（user 策略） | 否（用 `/autonomy` 调整或改策略文件） |
| 进程 workspace 授权 | 每个工作区一条，scope=workspace | 本进程，重启失效 | 是 |
| 进程 session 精确授权 | 每条 tool+目标，scope=session | 本进程，重启失效 | 是 |
| 持久策略规则（user） | [user] 规则 + ur- 稳定 id | 文件存在即生效，每次 run 重载 | 是（面板二次确认后原子写回用户文件） |
| 持久策略规则（project） | [project] 规则 + pr- 稳定 id | 文件存在即生效，每次 run 重载 | 否（只能收紧，撤销会放宽；修改仓库文件） |

## 协议

- ready 声明新 capability `permission-panel`。
- 请求：`{"type":"command","command":"permissions","args":{"action":"list"}}` 或 `{"action":"revoke","grant_id":"<id>"}`；其余字段/取值拒绝。
- 响应事件 `permission_overview`（绑定请求 id）：
  - `autonomy: {level,label,source,requested,cap}`；`requested` 为请求档位，`source ∈ cli|user-policy|default`。
  - `grants: [{id, kind:"workspace"|"session", scope, tool, target, workspace, validity:"process", revocable:true}]`；target 为路径或命令摘要。
  - `persistent: [{source:"user"|"project", path, rule, validity:"file", revocable:false, reason}]`。
  - `error: string|null`（策略加载错误，保留上一份策略）。
  - `revoked: {id, kind, scope, tool, target} | null`（revoke 回执）。
- grant id 确定性推导：`ws-` + sha256(root)[:12] / `ss-` + sha256(workspace|tool|target)[:12]；id 只用于同一进程内定位，不跨进程持久化。
- 未知/已撤销/跨工作区 id → `permission_grant_not_found`（error 事件），不改变现有授权；回执不收窄其他来源。

## 撤销语义

- workspace：移除该工作区根的 workspace 授权；session 精确授权保持（分别撤销）。
- session：仅移除该条键；workspace 授权与其他 session 条保持。
- user 持久规则：按 ur- id 从用户策略文件原子删除并重载；保留 version/autonomy/其它规则；未知 id、文件不可用、项目规则分别拒绝且不改文件。
- 撤销后立即影响后续 `ApprovalGate.request`；审计仍记录实际决策（不改 audit.jsonl 结构）。
- 响应返回撤销后的完整概览，使界面能显示"剩余权限"；不返回其他工作区的授权。

## TUI

- `/permissions`：有 `permission-panel` 能力时打开面板；否则回退原文本命令结果。
- 面板分区：档位与来源 → 进程临时授权（可点选、`撤销所选`、显示有效期）→ 持久策略（只读，说明文件路径与不可撤销原因）→ 策略错误。
- 撤销流程：选择 grant → 撤销 → 等待回执 → 用回执概览刷新面板 → 提示"已撤销 X；剩余 Y 条进程授权"；失败/迟到回执保留原列表并说明。
- `/permissions reset` 保持旧路径与 P1 文案（不做成面板按钮，避免误把"全部撤销"与单条撤销混在一起）。
- 返回/Esc 保留主区草稿与光标；面板只读查询不改变当前运行/停止目标。

## 验证

先加/改测试再实现：Python 单元（grant 推导、撤销边界、跨工作区拒绝）+ stdio 结构化协议测试；TUI 面板旅程（打开、撤销、剩余、失败、迟到回执、能力回退）+ `BackendClient` 实际 Python 测试（list → revoke → 后续读取反映剩余权限）。完整 Python/Ink 回归与 fake PTY；真人/真实终端与 Owner Review 单列。

## 风险

- 面板显示的授权/规则集合是调用时刻快照；运行中新增授权需要刷新（面板提供刷新，审批后提示可重新打开）。
- 持久 user 规则撤销会写入用户文件并立即重载：TUI 要求二次确认，后端以解析校验 + 原子替换保证失败不改文件；project 规则保持只读并在界面说明原因。
- 撤销动作目前没有独立审计文件，只通过返回的概览与策略文件内容可核对。
