> 历史记录：这是调整产品路线前的 M1–M8 模块建设计划，保留原始完成标记与验收待办。它不代表当前版本已通过产品验收；当前计划见 [工作台里程碑](../MILESTONES.md)。

# 里程碑（学习驱动）

> 每个里程碑 = **一个用户可见行为 + 一个 harness 概念 + 一个验收测试 + 一篇 5 行笔记**。
> 必须用真模型端到端跑通；没有 key 就用 `dev/mock_openai_server.py`。
> **fake provider 只用于测试，不能作为里程碑验收。**
> 没通过当前验收，不进下一个里程碑。

## 当前状态

- **M1 ✅**：真实 provider + TUI 流式；真 key 跑通。
- **M2 ✅**：只读工具 + 文本工具协议 fallback；能读文件后回答。
- **M2.5 ✅**：loop 硬化 v2（重复/空响应/截断/超时/取消 + LLM 重试/自动续写/无进展/预算/只读工具重试）。
- **M3 ✅（代码）**：1M 分区预算 + recall + LLM 摘要 + TaskState。
- **M4 ✅（代码）**：write/edit + diff + a/s/d 审批 + backup + audit。
- **M5 ✅（代码）**：bash + 审批 + 黑名单 + 超时 + 输出截断 + run 预算。
- **M6 ✅（代码）**：session JSONL + `--continue` + 多轮 history + 崩溃对账。
- **M7 ✅**：trace/usage/成本 + `/cost` + 10 任务 eval + 回归 delta；deepseek-flash 10/10。
- **M8 ✅（代码）**：AGENTS.md 注入、8 维对照、3 分钟 demo；待彩排。

## 总表

| # | 里程碑 | 用户看到什么 | 学什么 | 书 | 预计 |
|---|---|---|---|---|---|
| M1 | 真实模型 + TUI 流式 | 输入“你好”，token 流式出现 | provider 适配、SSE、错误分类 | 书2 p290–292；书1 p225–234 | 1 天 |
| M2 | 只读工具循环 | 问“这个仓库做什么”，它会读文件再答 | function calling、tool_call_id、多步循环 | 书1 p261–276；书2 p347–350 | 2 天 |
| M3 | 上下文预算与压缩 | 长对话后状态栏显示 ctx；原任务仍记得 | 预算、pinned、offload、summary、KV cache | 书2 p341–356 | 2 天 |
| M4 | 写 + 审批 + diff | “修个 typo” → diff → 批准 → 文件变了 | 工具契约、风险分级、HITL、审计 | 书1 p270–271；书2 p349 | 3 天 |
| M5 | Bash + 安全 | “跑测试” → 命令 → 批准 → 输出；危险命令拒绝 | 执行隔离、超时、取消、失败路径 | 书2 p349、p405–407 | 3 天 |
| M6 | 会话与恢复 | `kill -9` 后 `--continue` 接着跑 | 事件日志、checkpoint、恢复分类 | 书2 p352–355 | 3 天 |
| M7 | Trace / 成本 / 评估 | `/cost`；10 个任务跑分；回归报告 | span、成本归因、eval、judge 校准 | 书2 p274–288 | 3 天 |
| M8 | 收口：AGENTS.md / 对照 / demo | 项目规则生效；和 opencode/Codex 对比 | harness 取舍、产品化 | 书2 p463–494；OpenClaw/Pi | 2 天 |

---

## M1 · 真实模型 + TUI 流式回复

**用户可见**：`./run_vibe.sh --chat-only`，输入“你好”，token 一个个出现；状态栏显示模型；Ctrl-C 取消。

**学什么**
- OpenAI-compatible `/chat/completions` 的消息形状。
- SSE：`data: {...}` / `[DONE]`；`delta.content`、`finish_reason`。
- 为什么 provider 是适配器：app 不认识 HTTP，只认识事件流。
- 错误分类：401 / 404 / timeout / 网络错误，都要在 TUI 里可读。

**验收**
- [ ] 真 key 或 mock server：回复流式出现，不一次性蹦出来。
- [ ] `--chat-only` 时 provider 收到的 `tools=[]`。
- [ ] 401 / timeout：TUI 显示可读错误，不崩。
- [ ] Ctrl-C：能取消当前流。
- [ ] `./run_tests.sh -q` 全绿。

**书**：书2 p290–292；书1 p225–234。

**已知坑**：macOS 系统代理会让 `127.0.0.1` 走代理，返回 502。provider 默认 `trust_env=False`；远程 API 需要代理时设 `VIBE_TRUST_ENV=1`。

---

## M2 · 只读工具循环

**用户可见**：问“这个仓库是做什么的？”，它先 `list_files` / `read_file` / `grep`，再回答，并指出文件路径。

**学什么**
- tool schema（name / description / parameters）。
- `assistant(tool_calls)` + `tool` 消息的配对；`tool_call_id`。
- 多步循环：什么时候继续、什么时候停。
- 未知工具 / 工具异常如何回灌给模型。

**验收**
- [ ] 一次回答至少触发 1 次读工具。
- [ ] 第二次模型调用能看到工具结果（不是丢掉）。
- [ ] `max_steps` 能停；未知工具能恢复。
- [ ] 路径逃逸 `../` 被拒绝。

**书**：书1 p261–276；书2 p347–350。

**模型兼容**：Qwen/Ollama 可能返回 `<tool_call>` 文本，而不是 `tool_calls`。M2 已加 fallback 解析；如果 provider 拒绝历史里的 structured `tool_calls`，下一步加 text-protocol 模式（工具结果作为 user 消息）。

---

## M2.5 · Loop 硬化 ✅（v2）

**用户可见**：模型重复调用同一工具、返回空响应、输出被截断、工具卡住时，TUI 不会静默卡死，而是给出明确状态。

**学什么**
- 终止条件不只有 `max_steps`：重复调用、空响应、截断、超时都要有出口。
- 工具执行要带超时；同步工具放线程，不能阻塞事件循环。
- 取消要检查在 step 边界和工具执行后。
- 工具调用 ID 必须跨 step 唯一。

**已做**
- 重复调用：`(tool, args)` 哈希，超过 `max_repeats`（默认 2）返回 `stuck`。
- 空响应：重试一次，再空返回 `error`。
- 截断：`finish_reason=length` → `truncated`。
- 工具超时：`ToolSpec.timeout`（默认 10s），`asyncio.to_thread + wait_for`。
- 取消：流中 / step 开头 / 工具执行后都检查。
- ID：文本协议 `step{n}_{i}`；provider 兜底 `call_{seq}_{i}`。

**v2 追加**
- LLM 流超时：`asyncio.timeout(llm_timeout)`；无输出时重试 1 次。
- 瞬时错误重试：provider 的 429/5xx/网络错误标记 `retryable`，指数退避。
- 截断自动续写：`finish_reason=length` 且无 tool call 时，注入 continuation user 消息，最多 `max_continuations` 次。
- 结果哈希无进展：同一 `(tool, args) + result` 重复超过 `max_no_progress` → `stuck: no_progress`。
- 单次 run 预算：`max_run_tokens` / `max_run_cost_usd`，超了 `stopped`。
- 只读工具重试：`read_file/grep/list_files/recall` 的 timeout 可重试；写/执行不重试。
- 写前新鲜度：读过之后文件被外部改动，`edit_file/write_file` 拒绝，要求 re-read。
- vibe prompt：先读后改、优先 `edit_file`、改完跑测试、不要谎报测试通过。
- TUI `/diff`：直接看当前 git diff，不经过模型。

**验收**
- [x] 重复调用 3 次停，状态 `stuck`
- [x] 连续两次空响应 → `error`
- [x] `finish_reason=length` → `truncated`
- [x] 慢工具超时 → 错误回灌，模型能恢复
- [x] 工具执行后取消 → `cancelled`
- [x] 瞬时错误重试 / 自动续写 / 无进展 / token+成本预算
- [x] 只读工具 timeout 重试
- [x] stale-read 保护 + `/diff`

**下一步 P1**：并行只读工具、prefix cache 命中率。

---

## M3 · 上下文预算与压缩 ✅（代码完成）

**用户可见**：连续聊 20 轮，状态栏 `ctx` 增长；触发压缩后，它仍记得最初的任务。

**学什么**
- 预算 = 上下文窗口 − 输出预留。
- 稳定前缀（system + tools）与易变层。
- 压缩顺序：先工具结果 → 再旧对话 → 再摘要 → 最后 drop。
- offload 引用、pinned、summary 的取舍。

**验收**
- [ ] 20 轮后触发压缩；状态栏能看到。
- [ ] 第一条 user 任务仍在上下文里。
- [ ] 被压缩的组有 `[offloaded: ref]` 或 `[summary]`，不静默消失。
- [ ] 超预算时报告 `over_budget`，不偷偷截断。

**书**：书2 p341–356。

**分区预算（已做）**
- `core/budget.py`：`BudgetPolicy`，比例上限 + 绝对上限。
- 1M 下：system 8k / memory 64k / tools 16k / history 256k / output 32k。
- `ContextBuilder` 先扣 system / memory / tools / scratch，再给 history。
- TUI 状态栏：`ctx total/968k · hist h/256k`。

**M3 追加（已完成）**
- `recall(ref)`：offload 内容可被模型取回。
- `LLMSummarizer`：同一个 provider 的 LLM 摘要，模型由 `VIBE_SUMMARY_MODEL` 指定；失败退回确定性摘要。
- `TaskState`：goal/plan/done/open/files/tests；`task_state` system 消息注入，不计入 stable prefix hash。
- TUI 状态栏显示 `todo done/total`。

**待验收**
- [ ] 真实长对话触发压缩（不是构造的测试）
- [ ] 原任务在压缩后仍可答
- [ ] `VIBE_KEEP_RECENT_GROUPS` 调到 8 后，历史明显更连贯
- [ ] 模型在需要时真的会调用 `recall`


---

## M4 · 写 + 审批 + diff ✅（代码完成）

**用户可见**：“把 README 里的 typo 修一下” → 弹 diff → `a` 允许一次 / `s` 本 session / `d` 拒绝 → 文件变化或不变。

**学什么**
- 写工具的契约：路径、内容、唯一匹配、幂等。
- 风险分级：read 自动，write 必须审批。
- 审批参数绑定：批准后改参数要失效。
- 备份与审计：`.vibe/backups/`、`.vibe/sessions/*.jsonl`。

**已做**
- [x] `write_file` / `edit_file`，`risk=write`
- [x] unified diff 预览
- [x] `a/s/d` 审批；session 按 `(tool, path)`
- [x] 写前备份 `.vibe/backups/`
- [x] 审计 `.vibe/sessions/*.jsonl`（参数/预览哈希）
- [x] 无 gate fail closed；拒绝回灌 `denied_by_user`

**待验收**
- [ ] 真实仓库里改一个文件，批准后变、拒绝后不变
- [ ] `s` 第二次同路径不再弹窗
- [ ] 备份文件存在且内容正确

**书**：书1 p270–271；书2 p349。

---

## M5 · Bash + 安全 ✅（代码完成）

**用户可见**：“跑一下测试” → 显示命令 → 批准 → 输出；`rm -rf /` 被拒绝；超时能停。

**学什么**
- 执行隔离与命令风险。
- 超时、输出截断、取消、部分结果。
- 为什么“模型说跑”不等于“可以跑”。

**已做**
- [x] `bash` + 审批（risk=exec）
- [x] 危险命令黑名单（`rm -rf /`、`sudo`、`git reset --hard` 等）
- [x] 超时 + 进程组 kill
- [x] 输出截断（最后 10k 字符）
- [x] `max_tool_calls` / `max_run_seconds` 预算

**待验收**
- [ ] 真实仓库里跑 `pytest` 或 `npm test`，批准后回显
- [ ] 危险命令被拒绝且模型能继续
- [ ] 超时后进程确实被杀掉
- [ ] **不是沙箱**：命令仍以用户权限运行，cwd 只是起点

**书**：书2 p349、p405–407。

---

## M6 · 会话与恢复 ✅（代码完成）

**用户可见**：跑到一半 `kill -9`，重启 `--continue`，能接着上次；没有重复写。

**学什么**
- append-only 事件日志与状态投影。
- checkpoint / last_seq。
- 恢复分类：可恢复 / 需对账 / 必须失败。
- 副作用未知时不能假装成功。

**已做**
- [x] `SessionStore`：append-only `events.jsonl` + `state.json` 投影
- [x] Agent 跨轮 `history`（TUI 不再是 stateless）
- [x] `--continue` 加载最新 session，并自动续跑未完成轮次
- [x] 崩溃后缺失 tool 结果补 `ERROR interrupted`，记 `recovered_tool`
- [x] 启动显示最近消息

**待验收**
- [ ] 真实 `kill -9` 后 `--continue` 能接着跑
- [ ] 不重复执行已完成的副作用
- [ ] 外部动作结果未知时显示“需对账”，不假装成功

**书**：书2 p352–355。

---

## M7 · Trace / 成本 / 评估 ✅（代码完成）

**用户可见**：`/cost` 显示 tokens、耗时、工具调用；`eval` 跑 10 个任务出报告。

**学什么**
- span 树：run / step / model / tool / approval。
- 成本归因；延迟分解。
- 确定性断言 vs LLM judge；回归门禁。

**已做**
- [x] trace span：run / llm / tool / approval
- [x] provider usage + `Pricing` 成本
- [x] `/cost` 汇总
- [x] 10 个 eval 任务 + JSON/Markdown 报告
- [x] 与上一轮比 delta

**待验收**
- [ ] 一次失败能定位到第一个出错的 span
- [x] 真实模型 10 个任务至少过 7 个（deepseek-flash 10/10）
- [ ] 3 个故意注入的回归被抓到
- [ ] `/cost` 数字与 trace 对得上

**书**：书2 p274–288。

---

## M8 · 收口：AGENTS.md / 对照 / demo ✅（代码完成）

**用户可见**：项目里的 `AGENTS.md` 生效；3 分钟 demo；和 opencode/Codex 的对比表。

**学什么**
- harness 的四种形态：最小循环 / 可扩展内核 / 网关平台 / 产品化。
- 12-Factor Agents 与产品取舍。

**已做**
- [x] `AGENTS.md` 注入 system prompt（root / `.vibe/AGENTS.md`，12k 上限）
- [x] 8 维对比表（控制流/状态/持久化/HITL/多 Agent/工具/可观测/边界）
- [x] `examples/demo-repo` + `scripts/demo.sh` + `docs/DEMO.md`
- [x] roadmap 写清“不做什么”

**待验收**
- [ ] 3 分钟 demo 真实彩排
- [ ] 用 demo 仓库确认 `AGENTS.md` 的“只改 calc.py”规则生效

**书**：书2 p463–494；OpenClaw / Pi / Codex / opencode。

---

## 规则

1. **一个里程碑只加一个用户可见行为**；想加别的，进 roadmap。
2. **真模型验收**：没有 key 就用 `dev/mock_openai_server.py`；fake provider 不算。
3. **没有验收测试，不算完成**。
4. **讲不出一个失败路径，不算完成**。
5. **每个里程碑一篇 5 行笔记**：问题 / 我原来的答案 / 证据 / 现在的答案 / 还没懂。
6. **里程碑之间不并行**；当前不过，不开下一个。

## 现在做什么

M1 只差你真跑一次：

```bash
# 在项目根目录运行
./run_vibe.sh --chat-only
```

或先不联网，用 mock server：

```bash
python dev/mock_openai_server.py
# 另开一个终端
VIBE_API_KEY=x VIBE_BASE_URL=http://127.0.0.1:PORT/v1 VIBE_MODEL=mock \
./run_vibe.sh --chat-only
```

跑完把结果（成功/报错）发我；M1 过了就开 M2。
