# P10 验证记录

状态：实施中，未整体验收。会话内搜索、按轮/错误定位、只读工具折叠、跨会话检索与索引、rounds 账本深链与搜索高亮已通过工程核验；真实终端与 Owner Review 仍在范围内。

## 检索索引、增量缓存与高亮（2026-11-09）

跨会话检索原先每次分页都重新扫描全部会话事件。本批加**进程内索引**：按 (workspace, purpose) 缓存各会话的消息（session_id、identity、title、created、`messages[{index, role, content, ts}]`）；每个会话以 events/meta 文件的 inode、大小与 mtime 组成签名，未变化直接复用、变化只重建该会话、删除的会话自动剔除；总消息超过 200k 时按创建时间保留最新会话并标记 `truncated`，损坏行与不可读会话计入 `skipped`。索引只读，不写会话记录。

**搜索高亮**：`Span` 增加 `highlight` 属性（反色+加粗渲染），新增 `highlightSpans` 大小写不敏感切分；`/find` 结果、跨会话 `/history` 命中都在结果行内高亮关键词；从结果或轮次定位到正文时，目标行整行反色高亮，开始新任务时清除。

### 自动验证

| 边界 | 证据与结果 |
|---|---|
| 索引复用 | 未变化的两次检索 `session_reloads` 不增加；`index_builds` 保持 1，第二次起计 `cache_hits` |
| 增量刷新 | 追加消息后只重建该会话（+1 reload）并立即命中新内容；删除会话后结果不再返回 |
| 损坏与降级 | 追加坏行后 `skipped` 计数增加但索引仍可用；超 200k 消息按最新会话截断并标记 `truncated`（常量级防御） |
| 性能 | 20 会话 / **4000 消息**：冷启动首次检索 10.7–11.5ms，**warm p95 = 2.9–4.0ms**（15 次采样）；`perf.txt` 保留实测输出 |
| 高亮 | `highlightSpans` 单测：大小写不敏感、多处命中、原文拼接不变、空查询/无命中、中文关键词、`base` 样式保留 |
| 完整 Ink | `tui-full.log`：**726** 项（**724 passed**，2 项 sandbox PTY 环境失败，与基线相同） |
| 完整 Python | `python-full.log`：**1952** 项，**1939 passed / 13 skipped**，0 失败 |

### 范围与未做

产品源码：`app/runtime/history_search.py`（索引/增量）、`tui/src/markdown.tsx`（highlight 渲染与切分）、`tui/src/locator-panel.tsx`、`tui/src/history-search-panel.tsx`、`tui/src/app.tsx`（结果高亮与正文目标行）；测试 2 份。证据在 `.vibe/ux-p10-batch3-2026-11-09/`。

未做：磁盘级持久索引/启动预热、索引内存上界之外的淘汰策略（当前按最新会话截断）、跨会话检索与服务端全文索引、账本到 P6 检查记录的精确跳转、真实终端与 Ghostty 真人/Owner Review。本批不把自动验证当作阶段或真人验收。

## 跨会话检索与轮次账本深链（2026-11-09）

第二批补「旧结论」的跨会话路径。`search` 命令（能力 `history-search`）在当前工作区、当前用途（user/test）的已保存会话消息中做大小写不敏感子串检索：匹配带 session_id、identity（沿用会话目录身份哈希，打开前仍复核）、role、上下文 snippet 与 message_index；按会话创建时间倒序、会话内消息顺序返回，20 条一页并提供身份游标；损坏记录、不可读会话与超限扫描分别计数/标记。读取只扫 `.vibe/sessions` 下当前用途的事件文件，不调用模型、不写记录。

TUI `/history <关键词>`（别名 `/search`）展示跨会话匹配；「打开会话」复用 P8 显式打开流程（候选后端 + ready 回执核对 workspace/session/identity），成功后把查询交给新工作台，在历史回放后自动定位首个匹配行。打开仍受运行中/待办/草稿 guard 约束，不自动运行。

`rounds` 命令（能力 `rounds-ledger`）只读当前会话 rounds 账本：轮次序号、目标、状态、验证三态、步数/费用、保存时间，损坏记录计数，最多 50 轮并标记截断。TUI `/rounds` 有该能力时展示账本（关键词可拖选复制前先看验证状态），「定位」按目标文本映射正文轮次起点；不在当前正文的旧轮次提示用 `/sessions` 打开原会话核验；无能力时回退第一批的正文轮次列表。

### 自动验证

| 边界 | 证据与结果 |
|---|---|
| 跨会话检索 | 新增 2 项 Python：用途隔离/身份可打开/损坏跳过；20+10 身份游标跨页、跨用途 cursor 与坏参数拒绝 |
| 轮次账本 | 新增 1 项 Python：新旧顺序、验证/费用字段、损坏文件计数、limit 截断；字段级防御式校验 |
| stdio 协议 | `history-search`/`rounds-ledger` 能力声明；`search`/`rounds` 结构化事件、空 query 与多余 args 返回 `invalid_request` |
| TUI 传输 | fixture：search/rounds 结构化 args 传输、非法参数与缺能力拒绝；实际 Python：跨会话命中带 identity、当前会话账本 total=0 |
| App 旅程 | 新增 4 项：结果面板打开旧会话并携带 focus query、迟到/异范围回执保留、账本定位与「不在当前正文」提示、能力缺失回退正文轮次；另 1 项新会话 initialFocus 自动定位 |
| 完整 Ink | `tui-full.log`：**725** 项（**723 passed**，2 项 sandbox PTY 环境失败，与基线相同） |
| 完整 Python | `python-full.log`：**1950** 项，**1937 passed / 13 skipped**，0 失败 |
| 先红后绿 | `backend-red.log` 保存模块缺失的收集失败；`backend-green.log` 4/4 通过；`green.log` 8/8 通过 |

### 终端与范围

产品源码：`app/runtime/history_search.py`、`app/runtime/round_ledger.py`、`app/stdio.py`、`tui/src/{protocol,backend,app,cli,commands}.ts*`，新增 `history-search-panel.tsx` 与 3 份测试。证据在 `.vibe/ux-p10-batch2-2026-11-09/`。

未做：跨会话索引/增量缓存（当前每次分页重新扫描，量级大时需要）、搜索结果在正文内的高亮、账本到 P6 检查记录的精确跳转、真人终端与 Ghostty 真人/Owner Review。本批不把自动验证当作阶段或真人验收。

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
