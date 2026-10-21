# Retro · mini-harness M1–M8

> 保留旧 M1–M8 的历史验收数据；架构描述已随单循环清理更新。下文的测试数、能力差距和优先级保留当时结论；当前 MCP 能力见 [README](../README.md)，当前产品验收使用 [W0–W7](MILESTONES.md)，旧编号见 [历史计划](history/MILESTONES-M1-M8.md)。

## 结论

这个项目达成了原定目标：**通过从 0 搭一个本地 TUI coding agent，把 harness 的主要层真正跑通**。

- 约 **4,667 行** Python（core + app + tests）
- **115 个测试**
- `deepseek-flash` eval **10/10**；每任务 3.8k–10.3k tokens，3–7 步，2.9–7.1s
- 8 个里程碑全部有代码 + 验收路径

但它仍然是**学习用 harness**，不是 opencode/Codex 的替代品。最大差距不在 agent loop，而在**隔离、生态、产品化**。

---

## 1. 交付了什么

| 里程碑 | 交付 | 验收 |
|---|---|---|
| M1 | 真模型 + TUI 流式 | 真 key 跑通 |
| M2 | 只读工具 + 文本工具协议 fallback | 读文件后回答 |
| M2.5 | loop 硬化：重复/空响应/截断/超时/取消 | 单测覆盖失败路径 |
| M3 | 1M 分区预算 + recall + LLM 摘要 + TaskState | 长对话代码路径 + 单测 |
| M4 | write/edit + diff + 审批 + 备份 + 审计 | 批准/拒绝/审批崩溃都测 |
| M5 | bash + 黑名单 + 超时 + 输出截断 + run 预算 | 真实命令 |
| M6 | session JSONL + `--continue` + 多轮 history + 崩溃对账 | kill -9 场景 |
| M7 | trace + `/cost` + 10 任务 eval + 回归 delta | 10/10 |
| M8 | AGENTS.md + demo + 8 维对照 | 3 分钟 demo |

---

## 2. 架构现状

```
TUI (Textual)
  └─ Agent.run
       ├─ ContextBuilder  ── BudgetPolicy / offload / recall / summary
       ├─ Provider        ── OpenAI-compatible SSE
       ├─ ToolRegistry    ── read/write/bash/recall/update_task
       ├─ ApprovalGate    ── diff + session allowlist + audit
       ├─ SessionStore    ── events.jsonl / state / trace
       └─ TraceRecorder   ── usage / cost / span
```

- `core/`：`models`、`budget`、`context`、`state`、`summarize`。
- `app/`：provider、tools、policy、session、trace、eval、TUI。
- 依赖规则仍然成立：`core/` 不 import `app/`、`textual`、`httpx`。

**但 `core/` 还不是完全通用的 harness**：

- 唯一运行 loop 在 `app/agent.py`；同步参考实现已删除。
- `Message` / `ToolCall` 形状偏 OpenAI。
- `ToolRegistry`、审批、session、trace 都在 `app/`。

这是有意的取舍：先让第一个 consumer 跑通，再决定什么值得下沉。

---

## 3. 做对了什么（Keep）

1. **里程碑优先，真模型验收**
   每个阶段都有用户可见行为，不是先堆抽象。

2. **上下文按“比例上限 + 绝对上限”分区**
   1M 下没有机械照搬 10% system = 100k；S/M/T/H/R 都有绝对 cap。

3. **offload → recall → summary 三段式**
   不再有“超预算就静默 drop”；摘要之前已经 offload 的 ref 仍可取回。

4. **TaskState 是 harness 状态，不是模型记忆**
   goal/plan/done/open/files/tests 由 harness 维护，pinned 注入，不参与压缩。

5. **审批 fail closed**
   没有 gate 就 `approval_required`；拒绝回灌 `denied_by_user`；审批崩了也不写。

6. **session 是 append-only，未知不假装成功**
   崩溃后缺 tool 结果补 `ERROR interrupted`，而不是编一个成功。

7. **trace / cost / eval 是一等公民**
   不是“感觉能跑”；有 usage、span、check、regression delta。

8. **测试跟着行为走**
   115 个测试覆盖成功路径和失败路径，TUI 审批也有 headless 测试。

9. **AGENTS.md 自举**
   项目根规则会被 vibe 自己读到，学习项目开始有自己的“操作手册”。

---

## 4. 踩过的坑（Stop / 教训）

| 坑 | 根因 | 修复 |
|---|---|---|
| 1M 模型只用了 32k 预算 | `max_tokens=32000` 默认值漂移 | 改成 1M + 分区预算 |
| 工具 schema 没计入预算 | 只数 `messages` | `tool_tokens` 单独扣 |
| TUI 每轮 stateless | 每轮 `Agent.run` 都新建 messages | `Agent.history` + session |
| `approval_required` 但用户以为已批准 | `__main__` 漏装 `ApprovalGate` | 入口装配 + 启动警告 |
| 输入 `y` 没批准 | 没有弹窗，`y` 变成聊天消息 | `y/a/s/n/d` + 弹窗提示 |
| `max_steps=1000` | 用步数兜底代替预算 | `max_tool_calls` + `max_run_seconds` |
| eval cost 全是 0 | 没配价格 | 文档写清 `VIBE_*_PRICE_PER_1M` |
| eval 10/10 但任务偏简单 | 任务只覆盖基础文件操作 | 下一步加硬任务/注入回归 |
| 一次批量替换把 `tui.py` 覆盖成 `__main__` | 多文件字符串补丁缺 import smoke | 恢复后补 TUI 测试；流程上先跑 import/测试 |

最值得记住的一条：**“装配错误”比“算法错误”更常见**。`approval_required` 不是审批逻辑错，是入口没把 gate 接上。以后应把 `main()` 的装配抽成可测试的 `build_runtime()`。

---

## 5. 通用 harness  vs  TUI 产品

| 层 | 现在在哪 | 是否通用 |
|---|---|---|
| Message / ToolCall | core | 较通用，但偏 OpenAI 形状 |
| Loop | app 的 `Agent.run` | 唯一异步实现，尚未下沉到 core |
| Context budget / offload / recall | core | 通用 |
| Summary | core 确定性摘要 + app LLM 摘要 | HTTP 调用在 app，经 summarizer 注入 core |
| TaskState | core | 通用 |
| Provider | app | 产品 adapter |
| ToolRegistry / 审批 / sandbox | app | 通用候选，但目前耦合 app |
| Session / trace / eval | app | 通用候选 |
| TUI / diff modal / `/cost` | app | 产品层 |

如果要做第二个 consumer（CLI、Web、IDE），优先下沉：**Provider 协议、Tool 接口、Approval 接口、Session/Trace**；TUI 只保留呈现。

---

## 6. 和 opencode / Codex 的真实差距

| 差距 | 影响 |
|---|---|
| 没有 OS 级 sandbox | 命令仍以用户权限运行；审批/黑名单不是隔离 |
| 没有 MCP / LSP | 工具生态和代码智能弱 |
| 没有多 Agent / 任务编排 | 只能单会话串行 |
| 没有 provider 生态/路由 | 只有 OpenAI-compatible |
| 没有产品级权限 UX | 只有 y/a/s/n/d + session allow |
| eval 深度不够 | 10 个基础任务，缺硬任务和注入回归 |
| 没有 prompt cache 指标 | 长上下文成本和延迟未优化 |

强项：**结构透明、1M 分区预算、recall、TaskState、fail-closed 审批、崩溃对账、trace/eval 一体**。这些是很好的学习骨架，不是产品护城河。

---

## 7. 如果继续：优先级

### P0（下一步最值）

1. **Runtime 装配 + 接线测试**
   把 `main()` 里 provider/context/tools/approval/session/agent 的组装抽成 `build_runtime()`，用测试锁住“gate 一定在”。

2. **更硬的 eval**
   加多文件重构、失败恢复、注入回归（故意改坏一个行为，确认 eval 抓到）。目标不是 10/10，而是**能区分好坏**。

3. **真 sandbox**
   macOS Seatbelt / Linux Landlock 或容器；先做命令级，不是全文件系统。没有这个，`bash` 只能算“有审批的 exec”。

### P1

4. **上下文缓存/前缀稳定性**
   现在 TaskState 排除了 stable prefix hash，但真实 provider cache 未必；加 cache hit / prefix 变化指标。

5. **Session schema 版本化 + seq/checkpoint**
   当前 JSONL 没有 schema version；恢复靠事件重放。加 `v1`、`seq`、checkpoint。

6. **结构化工具错误 / 幂等键**
   `ERROR ...` 字符串太自由；下一步应区分 `retryable / denied / unknown_side_effect`。

### P2（明确不做或最后做）

- MCP / LSP
- 多 Agent
- Web UI / 插件市场
- 多 provider 管理

---

## 8. 学习映射

这个项目真正练到的，不是“会调 API”，而是：

- **loop**：状态机、终止条件、重复检测、预算。
- **context**：预算、分组、offload、recall、摘要、pinned state。
- **memory/state**：模型无状态，harness 有状态。
- **tools**：schema、sandbox、超时、输出截断、审批。
- **HITL**：diff、allow once/session、fail closed。
- **session**：append-only、恢复、未知副作用。
- **observability**：span、usage、cost、eval、regression。

对应书2的 context / tools / HITL / observability / eval 章节；书1的 agent 架构部分可以二刷。

---

## 9. Keep / Stop / Start

### Keep

- 里程碑 + 真模型验收
- 先写失败路径，再写成功路径
- 小步提交、每步可运行
- 文档里写清“不做什么”

### Stop

- 用 `max_steps` 调大代替预算
- 批量字符串替换后不跑 import/测试
- 只跑简单 eval 就宣布质量
- 把“审批”当成“沙箱”

### Start

- 装配层可测试（`build_runtime`）
- 启动自检：budget / tools / approval / AGENTS.md 大小
- 硬 eval + 注入回归
- sandbox spike


---

## 附：Loop v2（retro 之后追加）

在 retro 之后，又按“先把 loop 做健壮 + vibe coding 定制”补了一轮：

- LLM 流超时 + 瞬时错误重试（429/5xx/网络，无输出才重试）
- `finish_reason=length` 自动续写（最多 `max_continuations` 次）
- 结果哈希无进展检测（`stuck: no_progress`）
- 单次 run 的 token / cost 预算
- 只读工具 timeout 重试；写/执行不重试
- 写前新鲜度保护（读过之后被外部改动就拒绝）
- vibe prompt：先读后改、优先 `edit_file`、跑测试、不谎报
- TUI `/diff`

测试从 115 增加到 **126**。
