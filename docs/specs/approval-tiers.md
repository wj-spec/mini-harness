# 审批层级与作用域

日期：2026-10-28。状态：T0–T4 层级与作用域矩阵已实现（`app/permissions.py`），现有工具行为保持兼容；T4 远端动作仅接浏览器 once 通道，destructive 层、浏览器会话级授权与规则持久化待后续。

## 1. 原则

1. **层级描述副作用强度**，不代替审批；审批决定"是否放行"，层级决定"能放行到多大范围"。
2. **矩阵是单一事实来源**：工具只声明 `risk`，可用作用域、UI 选项和校验都由矩阵推导，避免散落的 `if name in {...}`。
3. **fail closed**：未知 risk 按执行层处理；无审批处理器一律拒绝；非法作用域降级为拒绝并写审计。
4. 审批、沙箱、护栏是三层独立防线：审批管"谁同意"，沙箱管"最坏能伤到哪"，护栏管"已知危险模式"。沙箱不减少审批。

## 2. 层级

| 层级 | 名称 | 典型工具 | 副作用 |
|---|---|---|---|
| T0 | 只读 | read_file、list_files、grep、git_status、git_diff、recall | 无副作用，免审批 |
| T1 | 工作区写入 | write_file、edit_file、apply_patch | 修改工作区文件 |
| T2 | 本地执行 | bash、MCP server 启动、risk=exec 的 MCP 工具 | 运行进程/任意命令 |
| T3 | 外部网络 | web_search、risk=network 的 MCP 工具 | 数据外发/外部读取 |
| T4 | 远端页面动作 | 浏览器 click/type/select（当前绑定 once + 快照） | 改变远端页面状态 |

## 3. 作用域矩阵

| 层级 | once | session（精确调用） | workspace（整个工具） | 说明 |
|---|---|---|---|---|
| T0 | — | — | — | 免审批 |
| T1 | ✅ | ✅ | ✅（仅写文件类） | apply_patch 暂不提供 workspace |
| T2 | ✅ | ✅ | ✅（仅 bash） | MCP 启动当前可 session 记住精确命令 |
| T3 | ✅ | ✅ | ❌ | 不允许"整个网络工具工作区放行" |
| T4 | ✅ | ❌ | ❌ | 快照绑定，逐次确认 |

判定优先级（高 → 低）：

1. 黑名单 / 写路径护栏 / 沙箱（硬拒绝，错误前缀 `blocked` / `sandbox_denied`）；
2. 本 run 已拒绝的相同调用（`denied_by_user`，不再询问）；
3. 已有授权：workspace（仅合格工具）→ session（精确键）；
4. `once_only` 覆盖（浏览器动作、受控 MCP 工具）；
5. 审批处理器决策，并按矩阵校验作用域；
6. 审计落盘（`audit.jsonl`）。

## 4. 拒绝原因（可区分）

| 原因 | 含义 |
|---|---|
| `denied_by_user` | 用户拒绝，或本 run 已拒绝过同参数调用 |
| `denied_no_handler` | 没有审批处理器，默认拒绝 |
| `denied_workspace_scope` | 工具不具备工作区语义，workspace 授权被拒 |
| `denied_scope_not_allowed` | 作用域与层级矩阵不符（如 T3 请求 workspace、T4 请求 session） |
| `sandbox_denied` | 沙箱策略拒绝（写外部/网络白名单外） |
| `origin_not_allowed` | MCP/browser 目标 origin 不在允许列表 |
| `snapshot_stale` | 浏览器动作快照过期，页面已变化 |
| `action_limit` | 浏览器单会话动作次数用尽 |

## 5. 与现有实现的关系

- `ApprovalGate` 新增 `tier_resolver`（由 `Agent` 注入：`mcp_start` → exec，其余按 `ToolRegistry.risk`）；未注入时退回旧的按名字集合行为，保持兼容。
- `decisions_for` 与 `request` 都经矩阵校验：作用域不在矩阵内直接拒绝并审计 `denied_scope_not_allowed`。
- 审批预览追加 `[审批层] Tn 名称`，MCP 启动预览同样标注。
- 现有工具映射后行为不变：write_file/edit_file/bash 仍可 workspace，web_search 仍只有 once/session，浏览器动作仍 once/deny。

## 6. 后续（未实现）

- **destructive 层（T5）**：`rm -rf`、`git reset --hard`、`git clean -fd`、强制 push 等不可逆命令即使 workspace 已授权也逐次确认（需要命令级分类与二次确认 UI）。
- **浏览器会话级授权**：同 origin 的 click/type/select 一次批准到本页会话（仍禁提交），降低 B2 的 17 次审批负担。
- **规则持久化**：session/workspace 规则跨进程持久化（opt-in、带来源与撤销）。
- **审批可视化与统计**：`/permissions` 列出具体规则并可逐条撤销；交付回执统计 once/session/workspace 次数，用数据评估审批负担。
- **沙箱下工作区写自动批准**：可作为 opt-in（沙箱兜底），默认关闭。

## 7. 验收

- `tests/test_permissions.py`：层级映射、fail closed、矩阵、非法作用域拒绝、workspace 不被 T3 记住。
- `tests/test_policy.py` / `tests/test_browser_actions.py`：既有 once/workspace/浏览器 once-only 行为不回退。
- `tests/test_agent.py::test_approval_tiers_are_visible_and_scoped`：Agent 注入 resolver、预览带层级、决策列表符合矩阵。
- 全量 `./run_tests.sh -q` 与 `npm --prefix tui test`。
