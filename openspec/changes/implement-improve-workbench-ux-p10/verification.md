# P10 验证记录

状态：实施中，未整体验收。会话内搜索、按轮/错误定位与连续成功只读工具折叠已通过工程核验；跨会话全文检索/索引、rounds 账本深链、真人终端与 Owner Review 仍在范围内。

## 会话内定位与只读工具折叠（2026-11-09）

开工核对 `transcriptLines`/`ViewState.entries`、轮次与错误可得字段，确定本批以「用户任务轮次」为定位单位（`begin` 记录 runId，`done` 记录终态），rounds 账本深链单列。`tool_call` 事件新增可选 `tier`（T0–T4），Python 在工具调用时附带自身 `_tool_tier(call)`；旧后端缺省时宁可不折叠也不猜测副作用。

`/find <关键词>` 在当前已加载会话的渲染行中做大小写不敏感子串搜索（上限 200 条），结果面板显示命中上下文与行号；`/rounds` 列出用户任务轮次（序号、终态、目标摘要）；`/errors` 列出工具调用错误与 `runStatus=error` 的轮次，按正文顺序排列。↑↓ 选择、Enter 定位、Esc 返回；定位写入回看位置（目标行上方留 2 行），`Ctrl+End` 回最新；不调用模型、不写会话记录，返回保留草稿与光标。连续 ≥2 个成功只读（tier=read）工具调用折叠为一张摘要卡（`read_file×2 · grep×1`），`Tab` 选中其中一条即展开该条，`Ctrl+O` 仍可查看原始参数与输出；非成功、非只读、执行状态未知或旧后端的调用不折叠。

### 自动验证

| 边界 | 证据与结果 |
|---|---|
| 协议与状态 | `tool_call` 可选 `tier` 严格校验；ViewState 记录 tool tier、task runId 与 runStatus；`begin`/`done` 映射有单测 |
| 纯函数 | `history-locate.ts` 的 `searchTranscript`/`firstLineByEntry`/`roundItems`/`errorItems` 有独立回归；大小写、空查询、无命中、排序、行号稳定 |
| 渲染折叠 | 3 个连续成功只读工具折叠且不显示正文；选中其中一条展开原输出；write/失败条目不折叠；旧后端无 tier 不折叠（回到逐条渲染） |
| App 旅程 | 新增 5 项：`/find` 结果面板并跳到匹配行（视口回看）、`/rounds` 跳轮次起点、`/errors` 跳失败工具现场、无命中保留正文并给出用法、Esc 返回输入 |
| 性能 | 3000 条记录 / **8999 渲染行**，20 次 `/find` 搜索 p95 = **4.3–4.5 ms**（门槛 < 100ms）；`green.log` 保留测量输出 |
| 完整 Ink | `tui-full.log`：**717** 项（**715 passed**，2 项 sandbox PTY 环境失败，与基线相同） |
| 完整 Python | `python-full.log`：**1946** 项，**1933 passed / 13 skipped**，0 失败（`tool_call.tier` 不影响既有回归） |
| 先红后绿 | `red.log` 保存模块缺失的构建失败；实现后 `green.log` 10/10 通过 |

### 终端与范围

产品源码：`app/stdio.py`（tool_call tier）、`tui/src/protocol.ts`、`view-state.ts`、`workbench.tsx`、`commands.ts`、`app.tsx`、`cli.tsx`，新增 `history-locate.ts`、`locator-panel.tsx`；测试 2 份。证据在 `.vibe/ux-p10-history-2026-11-09/`。

未做：跨会话/旧会话全文搜索与索引、rounds 账本深链、搜索高亮、真人终端 fake PTY 与 Ghostty 真人/Owner Review。搜索范围是当前已加载正文；旧会话内容需先经 P8 `/sessions` 打开后再定位。本批不把自动验证当作 P10 整体或真人验收。
