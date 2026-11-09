# P9 验证记录

状态：实施中，未整体验收。结构化权限概览、进程内逐条撤销、用户级持久策略规则撤销与 TUI 权限面板已通过工程核验；项目策略写回、撤销审计、跨进程同步、真实终端与 Owner Review 仍在范围内。

## 持久用户策略规则撤销（2026-11-09）

第二批补「撤销持久规则」。`permission_overview` 的 persistent 条目增加稳定 id（`ur-`/`pr-` + 规范化规则哈希 12 位）、`revocable` 与明确原因：用户级规则（`~/.vibe/permissions.json` 或 `VIBE_PERMISSIONS` 指定文件）可撤销；项目级规则保持只读——项目策略只能收紧，撤销会放宽权限，必须修改仓库文件。

`permissions` 新增 `{action:"revoke-rule", rule_id}`：按 id 从用户策略文件原子删除该条规则（保留 `version`、`autonomy` 与其它规则），写前用既有解析器完整校验；规则不存在/已变化返回 `permission_rule_not_found`，项目规则、用户策略未启用或文件不可用返回 `permission_rule_not_revocable`，两种情况都不改文件；成功后重载策略并返回撤销后的完整概览（含 `revoked_rule`），后续工具判定立即使用新策略。

TUI 权限面板将可撤销项扩展为「进程授权 + 用户级规则」（project 规则只读展示并标注不可撤销原因）；选中 user 规则按「撤销所选」进入二次确认（确认前不发送），再次确认才写入；回执提示「已撤销持久规则 …；剩余 N 条用户策略」，Esc 取消确认。

### 自动验证

| 边界 | 证据与结果 |
|---|---|
| 规则 id 与可撤销性 | user 规则 id 以 `ur-`、project 以 `pr-`；overview 显示 revocable 与原因且不修改文件；规则哈希对多字段稳定 |
| 原子写与保留 | 删除目标规则后文件保留 `version`/`autonomy`/其它规则，临时文件清理；重载后原规则不再命中（`evaluate(...).decision is None`） |
| 拒绝边界 | 项目规则 / 未知 id / `VIBE_PERMISSIONS` 为空 / 文件损坏均拒绝且文件字节不变；错误码区分 not_found 与 not_revocable |
| stdio 协议 | `permissions revoke-rule` 回执带 `revoked_rule` 与剩余 persistent；项目规则/未知 id/缺参分别返回 `permission_rule_not_revocable` / `permission_rule_not_found` / `invalid_request` |
| TUI | 新增 1 项旅程：二次确认前不发送、确认后发送准确 rule_id、回执显示剩余策略；transport 覆盖 revoke-rule args/非法 id，实际 Python 覆盖未知 id 错误 |
| 完整 Ink | `tui-full.log`：**727** 项（**725 passed**，2 项 sandbox PTY 环境失败，与基线相同） |
| 完整 Python | `python-full.log`：**1956** 项，**1943 passed / 13 skipped**，0 失败 |
| 先红后绿 | 后端新增测试在实现前红（ImportError/断言），实现后 4/4 通过；TUI 先红类型/断言后 9/9 通过 |

### 范围与未做

产品源码：`app/safety/permission_policy.py`（rule id/格式化/原子删除）、`app/runtime/permission_view.py`（revoke_rule 与异常）、`app/stdio.py`（action 分发）、`tui/src/protocol.ts`、`tui/src/use-permission-panel.ts`、`tui/src/permission-panel.tsx`，测试 2 份。证据在 `.vibe/ux-p9-rules-2026-11-09/`。

未做：项目策略写回（设计上只读）、撤销动作的独立审计文件、跨进程授权同步、真实终端 PTY 与 Ghostty 真人/Owner Review。本批不把自动验证当作阶段或真人验收。

## 权限来源与逐条撤销（2026-11-09）

开工前核对 `ApprovalGate` 的两组运行时授权（workspace 根集合、session `(workspace, tool, target)` 键集合）、`PermissionPolicy` 的 user/project 持久文件与 `/permissions` 文本输出。本批不改审批判定、硬边界与 audit.jsonl 结构，只增加只读结构化视图与进程内逐条撤销。

`permissions` 带 `args: {action:"list"}` 返回 `permission_overview`：档位 effective 与来源（本进程 CLI / 用户策略默认 / 默认 ask，含 project 上限与 requested）、进程内 workspace 授权、进程内 session 精确授权（稳定 id、工具、目标摘要、scope、`validity=process`）、持久 user/project 规则（来源文件、规则摘要、`validity=file`、不可进程内撤销的原因）、策略加载错误。grant id 由工作区根或 `(workspace, tool, target)` 哈希确定性推导（`ws-`/`ss-` + 12 位），只在本进程内定位，不跨进程持久化。

`args: {action:"revoke", grant_id}` 撤销单条进程内授权：workspace 与 session 分别撤销、互不连带；未知/已撤销/跨工作区 id 返回 `permission_grant_not_found` 且不改变现有授权；回执带 `revoked` 与撤销后的完整概览，界面据此显示剩余权限。不带 args 的 `permissions` 与 `permissions_reset` 保持原文本路径与 P1 文案。

### 自动验证

| 边界 | 证据与结果 |
|---|---|
| 结构化数据 | `PermissionGateway` 新增 `grants/revoke_grant`；`app/runtime/permission_view.py` 组装档位来源、有效期、持久规则与错误；未知 id 抛 `PermissionGrantNotFound` |
| Python 单元/stdio | 新增 3 项：稳定 id 与 workspace/session 分别撤销、跨工作区/重复撤销拒绝；档位来源 cli/user-policy/default 与 project 上限、策略错误保留上一份；stdio list→revoke→剩余→未知 id→坏参数→reset/文本兼容 |
| TUI 传输 | 新增 2 项 fixture/实际 Python：结构化 args 传输与非法 grant 拒绝、无能力拒绝；实际 Python list、未知 id 撤销、旧文本路径 |
| App 面板 | 新增 5 项旅程：档位来源/有效期/持久规则渲染、撤销后剩余权限、失败保留原列表、迟到/异工作区回执忽略、无能力回退旧文本 |
| 完整 Ink | `tui-full.log`：**707** 项（**705 passed**，2 项 sandbox PTY 环境失败，与基线相同） |
| 完整 Python | `python-full.log`：**1946** 项，**1933 passed / 13 skipped**，0 失败（沙箱环境 skip 比正常终端多 9 项） |
| 先红后绿 | `backend-red.log` 保存模块缺失的收集失败；`backend-green.log` 3/3 通过；TUI 先红构建失败与实现后绿分别在会话记录中保留 |

### 终端与范围

产品源码：`app/safety/policy.py`、`app/runtime/permission_view.py`、`app/stdio.py`（能力与命令分发）、`tui/src/protocol.ts`、`backend.ts`、`commands.ts`、`app.tsx`、`cli.tsx`，新增 `use-permission-panel.ts`、`permission-panel.tsx`；测试 2 份 Python/2 份 TUI。证据在 `.vibe/ux-p9-panel-2026-11-09/`。

未做：持久策略文件写回/撤销（需要文件编辑确认语义，单列）、跨进程授权同步、审计 UI、真实终端 fake PTY 与 Ghostty 真人/Owner Review。面板显示的是调用时刻快照；运行中新增授权需刷新或重新打开面板。本批不把自动验证当作 P9 整体或真人验收。
