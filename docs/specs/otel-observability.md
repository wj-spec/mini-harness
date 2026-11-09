# 集中观测与 OTel 导出（M0–M3 分阶段规格）

日期：2026-11-08（v2：按只读评审修正 canonical projection、子 Agent/重试/bench 边界与字段名；v1 同日）。状态：**规格初稿（未实现）**。M0–M3 是可按序独立验收的切片；M4+ 是后续方向，不承诺日期。
对应决策：[待用户拍板事项 #5 跨机器/集中观测](open-decisions.md)（本文是其选项 B 的可执行规格，目标系统与隐私级别仍待拍板，见 §14）。
现状依据：[HARNESS §10 可观测性与成本](../HARNESS.md)、[上下文观测](context-observability.md)、[预算账本](budget-ledger.md)、[控制面 attach](control-plane-attach.md)、[后台进程监督](process-supervision.md)、[只读子 Agent](subagent-runner.md)、[评测](evaluation.md)、[长任务评测](long-horizon-eval.md)。
参考调研：[上下文分区占用统计与可视化](../research/context-partition-observability-2026-10-21.md)（含 OTel 边界与 GenAI 语义约定现状）。

## 0 第一性原理与不变量

1. **本地证据是事实源，导出是派生视图**：`trace.jsonl`、`rounds/`、`audit.jsonl`、`budget.jsonl`、`events.jsonl` 继续作为可复核事实；OTel 只是另一条只读出口。删除或关闭导出不丢失任何本地证据。
2. **导出集合 = 本地实际写入集合**：M1 只处理 `SessionStore.append_trace` 真正落盘的事件（canonical projection）；M0 读同一批事件（含轮转文件）。不凭空补事件、不基于“未落盘的中间事件”导出。
3. **观测不能改变行为**：导出失败、SDK 缺失、collector 离线、网络超时都不得改变 run 结果、审批决定、预算、退出码、stdout 协议帧或本地文件写入。
4. **默认关闭、本地优先**：不显式开启时零 SDK import、零导出线程、零网络连接、零新文件；默认 endpoint 只允许 loopback。
5. **默认不导出内容**：模型文本、prompt/completion、工具参数与输出、命令文本、文件内容、任务原文默认不出本机；需要按 §3.4 的隐私分级显式 opt-in。
6. **stdout 是协议**：任何导出、诊断、SDK 日志只能走 OTLP / 显式文件 / stderr，绝不写 stdout。
7. **关联而非复制**：span 携带 `run_id/session_id/task_id` 等关联键，不把本地证据正文搬进 span 属性，也不要求后端能读本地文件。
8. **有界**：导出队列、属性长度、批量、重试、flush 时间都有上限；指标 label 低基数；导出线程不得阻塞 Agent loop。
9. **可离线验证**：单元测试不依赖网络或 collector；M0 的 debug 格式与 M1 的 debug 文件是可复现验收的确定性出口。

不变量：

| # | 不变量 |
|---|---|
| O1 | `VIBE_OTEL` 未开启（或 SDK 未安装）时，进程行为不变，`trace.jsonl` 的事件格式/数量/语义不变；`core/` 不新增第三方依赖 |
| O2 | 导出异常全部在 sink 内吞掉并计数，最多写 stderr 诊断；不触发 `error` 帧、不改变 `AgentResult`、不改变退出码 |
| O3 | 默认 endpoint 必须是 `127.0.0.1`/`localhost`；非 loopback 目标需要显式 `VIBE_OTEL_ALLOW_REMOTE=1` |
| O4 | 默认不导出任何正文/参数/路径（L0）；L1 只增加路径、枚举与错误类别等非正文信息，正文仍不导出；L2 正文导出不在 M0–M3 |
| O5 | 每个 `vibe.run` span 的 `vibe.run_id` 必须与该 run 在 `trace.jsonl` 中的 `run_id` 相等，且 M0/M1 对同一事件流生成相同 span 结构（确定性 ID，§3.5） |
| O6 | span 名稳定、低基数；不把 run/session/request/tool_call ID 放进 span 名或指标 label |
| O7 | flush 在进程退出/连接关闭前限时完成（默认 ≤5s）；flush 超时只丢导出数据，不阻塞退出 |
| O8 | 测试套件不访问外部网络；OTel 集成测试用内存 exporter 或 debug 文件，不依赖 collector |
| O9 | 不替换 `--trace-summary`、`/evidence`、`/trace`、webhook；它们是本地/轻量通道，与 OTLP 并行 |
| O10 | eval/bench 路径不得因环境变量继承而意外外发遥测；`--bench` 必须显式强制关闭（§6.4） |
| O11 | 一个 stdio 请求可能包含多次 `agent.run`（如 plan 验收失败后的 repair）：投影必须显式处理“多 `run_start`、单 `run_end`”，不得把中间 run 伪装成完整 run |

## 1 现状、目标与非目标

### 1.1 现状（2026-11-08 代码核对）

- **单一 trace 事件流**：`TraceRecorder` 生成 `{type, ts, run_id, model, ...}` 事件，经 `Agent.run(trace=...)` 的单一回调进入 [app/stdio.py](../../app/stdio.py) 的 `record_trace`，再分流到 `TaskRuntime` 预算账本、`trace.jsonl`、TUI 帧与内存 metrics；空回调时只聚合不落盘（[app/session/trace.py](../../app/session/trace.py)）。
- **事件字段**：`run_start/run_end`、`llm_start/llm_end/llm_error/llm_retry`、`tool_start/approval/approval_wait/tool_end/tool_not_executed/tool_retry`、`command_result/file_changes/verification_recorded`、`completion_audit/completion_rejected`、`budget_update/context_snapshot/history_compacted`、`no_progress/action_required/no_action/continuation/steering_applied`、`permission_policy_error/audit_baseline_failed/round_record_failed` 等；关联键为 `run_id`、`step`、`request_id`、`tool_call_id`。
- **重试粒度**：`request_id = f"{run_id}:{step}:{attempt}"`，每次 attempt 都有独立的 `llm_start/llm_end`；`llm_retry` 在失败 attempt 的 `llm_end` 之后发出（[app/runtime/agent.py](../../app/runtime/agent.py)）。
- **本地落盘不是“每个事件都写”**：`record_trace` 对 `run_end` 只记录 `terminal_trace` 并提前返回，不写本地；一个 stdio 请求可在 plan repair 循环里多次 `agent.run`，最终只把最后一个 `ending` 写入 `trace.jsonl`。因此本地 trace 可能出现多个 `run_start`、单个 `run_end`（O11）。
- **直接 append 的非 run 事件**：`loaded` context 快照（`run_id=loaded:<session>`，[app/stdio.py](../../app/stdio.py)）、`/explore` 的合成 `run_end(kind=subagent, run_id=subagent:<task_id>)`、`user_profile_selection`（`run_id=<stdio request_id>`）、finally 中的最终 `ending`。
- **子 Agent 在父进程内运行**：[app/runtime/subagents.py](../../app/runtime/subagents.py) 的 `run_subagent` 接受可选 `trace_path`，但当前 delegate、`/explore`、CLI 调用均未传；未传时 trace 事件被丢弃。`/explore` 只在父 trace 追加一条合成 `run_end(kind=subagent)`。`--explore` CLI 不写父 trace。
- **本地观测面**：`--trace-summary`（[app/session/observability.py](../../app/session/observability.py)）、`/evidence`（[app/session/evidence.py](../../app/session/evidence.py)）、`--bench-history`（[app/eval/history.py](../../app/eval/history.py)）、TUI `/trace`、[通知 webhook](../../app/runtime/notifications.py)、[retention](../../app/session/retention.py)（`trace.jsonl` 轮转 + `events.jsonl` 软上限）。
- **跨进程结构**：`--worker` 启动独立 stdio 子进程执行任务，attach 走 worker socket；协议是 JSONL v1，TUI 与 Python 双方做严格字段校验（[tui/src/protocol.ts](../../tui/src/protocol.ts)、[app/stdio.py](../../app/stdio.py)）。
- **bench/eval 边界**：`--bench` 不是子进程，是 `--task --bench` 在 `run_oneshot(options.bench=True)` 内本进程执行；`--eval` 走 `app/eval/internal.py` 本进程 `agent.run`；SWE-bench 有独立 trace 回调。三者都不应因 `VIBE_OTEL` 环境变量继承而自动导出。
- **没有 OTel**：全仓无 `opentelemetry` import；当前 `.venv`/`.pylibs` 只因 `mcp`/`fastmcp` 传递依赖存在 `opentelemetry-api 1.45.1`，**没有 SDK 与 exporter**。

### 1.2 目标

- 在不改变本地证据链与默认行为的前提下，按阶段提供：离线 trace 导出（M0）→ 进程内 span 导出（M1）→ 指标导出（M2）→ worker/子 Agent 关联（M3）→ 生产化（M4+）。
- 所有阶段默认关闭、fail-open、隐私分级明确、离线可测；开启后能用标准 OTLP/HTTP 接到本地 collector 与常见后端。
- 输出与本地 `trace.jsonl` 可按 `run_id` 对账；不要求后端能读本地文件。

### 1.3 非目标

- 不替换本地 trace/rounds/evidence/`--trace-summary`/webhook。
- 不做 OTel Logs（M0–M4 不做；日志仍走 stderr 诊断与 `worker.log`）。
- 不做多用户、远程 attach、公网暴露、云端账号托管。
- 不改变审批、预算、完成守卫、沙箱或协议语义。
- 不在 `core/` 引入依赖；不把 OTel 设为默认安装依赖。
- 不做会话内容导出（L2）的默认能力；M0–M3 只定义到 L1。

## 2 阶段与范围

| 阶段 | 内容 | 默认 | 依赖 | 产出 | 状态 |
|---|---|---|---|---|---|
| **M0** | 离线读取 `trace.jsonl`（含轮转）→ debug JSONL / OTLP JSON / 摘要；可选 POST | 关闭（命令显式调用） | 无新依赖 | `scripts/export_otel.py` + 纯映射模块 + 测试 | 未实现 |
| **M1** | `serve` / `--task` 进程内 span 导出；debug 文件；fail-open | 关闭 | 可选 SDK extra | `app/session/otel.py` + 接线 + 测试 | 未实现 |
| **M2** | 指标导出（token/cost/工具时延/审批等待/context 分区/预算/丢弃） | 关闭 | M1 | `app/session/otel_metrics.py` + 测试 | 未实现 |
| **M3a** | worker 执行进程与子 Agent 的 span 关联（同进程传父上下文、task_id、去重） | 关闭 | M1（M2 可并行） | 接线 + 测试 | 未实现 |
| **M3b** | TUI 关联字段（B）/ 全链路 traceparent（A，M4 评估）（可选，可延后） | 关闭 | M3a + 用户拍板 | 协议校验 + TUI 接线 + 测试 | 未实现/可裁剪 |
| **M4+** | collector/后端部署样例、采样/保留/告警、语义约定锁定、Prometheus、eval/bench 接入 | — | M0–M3 | 部署文档与 smoke | 方向 |

**顺序约束**：M0 先验证映射与隐私；M1 只做 trace；M2 在 M1 稳定后做指标；M3 才碰跨进程与协议。每个阶段单独提交、单独更新 spec 状态与验收证据；不允许一次性实现全部阶段。每阶段完成后更新 §2 状态与 §13 勾选，并记录证据路径（测试文件或 `.vibe/otel/<stamp>/`）；未通过不得进入下一阶段。

## 3 总体设计

### 3.1 数据流

```text
Agent.run(trace=cb)
  └─ TraceRecorder ──emit──> record_trace（进程内 trace 分流点）
        ├─ 本地写入：SessionStore.append_trace → trace.jsonl（含轮转）
        └─ 同一事件对象 → OtelSink（M1 起；只处理本地实际写入的事件）
                              └─ 事件 → span/span event/metric → Batch → OTLP/HTTP exporter
                              └─（测试/离线）debug 文件 / 内存 exporter

M0（离线）：trace.jsonl[.1..N] + meta.json → 同一 canonical projection → debug JSONL / OTLP JSON
```

关键点：**M0 与 M1 共用一个 canonical projection 和同一映射函数**；差异只在“事件来源”（读文件 vs 实时回调）。M1 的 sink 不接收未落盘的中间事件，避免在线/离线结果不一致。

### 3.2 canonical trace projection（M1 前置小重构）

M1 实施前需要把 stdio 的 trace 出口收敛，保证 OTel 看到的集合与本地文件一致：

1. 在 `_Connection` 内新增 `emit_trace(event, *, persist=True)`：`persist=True` 时先 `session.append_trace(event)`，再调用 `otel.emit(event)`；OTel 调用包 try/except，永不抛出。
2. `record_trace(event)` 保持现有分流（预算账本、TUI 帧、metrics）；其中 **`run_end` 仍只记录 `terminal_trace`、不写本地**；其他事件改为调用 `emit_trace(event)`。
3. 现有直接 `append_trace` 的位置改为 `emit_trace`：
   - `loaded` context 快照：落盘，但 sink 按“非 run 事件”跳过（§3.2.6）；
   - `/explore` 合成 `run_end(kind=subagent)`：落盘，sink 映射为 `vibe.subagent` span（§4.1），不是 run 根；
   - `user_profile_selection`：落盘，sink 只计数并跳过（`run_id` 是请求 id，不是 `TraceRecorder.run_id`）；
   - finally 的最终 `ending`：落盘，sink 用它关闭 root（可能也是唯一的 `run_end`）。
4. **多 run 投影**（plan repair）：本地 trace 有 N 个 `run_start`、1 个 `run_end` 时，投影规则为“新 `run_start` 到来时把仍打开的 run 以 `vibe.incomplete=true`、`vibe.close.reason=superseded` 收口；最终 `ending` 收口最后打开的 run”。若 `ending.run_id` 与最后打开 run 不一致，优先按 run_id 匹配，找不到则收口最后打开 run。不得为中间 run 补一条假的成功 `run_end`。**代价**：中间 run 的最终 status/stop_reason 不在本地 trace 中，投影只能标 incomplete，不得臆造完成状态；这是与本地事实源保持一致的选择。
5. `ending` 的 `run_id` 可能来自最后一个 run（`terminal_trace` 或 `result.run_id`）；若它在 trace 中从未出现 `run_start`（极端取消路径），允许创建一个零宽 root，标 `vibe.incomplete=true`，不计为成功。
6. **skip 清单**（M0/M1 共用）：
   - `context_snapshot` 且 `phase=loaded` 或 `run_id` 以 `loaded:` 开头 → 跳过（可选以后做 session 级 span，M2 再定）；
   - `user_profile_selection` → 跳过（内容敏感，仅计数）；
   - 任何没有 `run_id` 的事件 → 计数并跳过；
   - 未知 `type` → 计数并跳过；
   - `run_end` 且 `kind=subagent` → 不跳过，映射为 `vibe.subagent`（§4.1）；
   - 合成 `run_end` 与真实 `run_end` 重复出现时按 `(run_id, type=run_end)` 去重（正常 canonical stream 不应出现，防御性保留）。
7. **eval/bench 的边界**：M1 只接 `serve`（stdio）与 `--task` 的 `run_oneshot`（`options.bench=False`）。`--eval`/SWE-bench 不构造 sink；`--worker` 的执行子进程自动走 `serve`；attach 进程不构造 sink。
8. **sink 生命周期位置**：`serve()` 内创建 `session`、`_Connection` 并在 `finally` 发 `closed`；因此 sink 在 `serve()` 内构造并传给 `_Connection`，flush 放在 `serve()` 的 `finally` 中、`send("closed", …)` 之前（`run_stdio` 只做 `redirect_stdout` + `asyncio.run`，没有 finally）。

### 3.3 进程与 Resource 模型

- 当前一个后端进程对应一个会话（stdio；worker 子进程通过 `--session` 继承），因此 `service.name`、`vibe.session.id`、`vibe.session.purpose` 可放 **Resource**；run/task 身份放 **span 属性**。
- Resource 建议（全部低基数、默认不含路径/正文）：

| 属性 | 来源 | 默认 |
|---|---|---|
| `service.name` | 固定 `vibe`（可被 `OTEL_SERVICE_NAME` 覆盖） | `vibe` |
| `service.version` | `VIBE_AGENT_VERSION` 环境变量优先；否则读 `pyproject.toml` 的 `project.version`（启动时缓存；读不到回退 `unknown`），不跑 git、不 import `app/eval` | 当前版本 |
| `service.instance.id` | 进程内随机 UUID | 生成 |
| `vibe.session.id` | `SessionStore.id` | 开启时 |
| `vibe.session.purpose` | `user` / `test` | 开启时 |
| `vibe.worker` | `1` 表示 worker 子进程 | 开启时 |
| `vibe.otel.mapping_version` | 固定 `"1"` | 开启时 |

- **不放进 Resource**：workspace 绝对路径、任务原文、文件路径、审批预览、API key/环境变量。workspace 最多在 L1 以工作区相对路径出现在 span 属性（见 §3.4）。
- 一个进程可能跑多个 run（TUI 连续任务、worker 续段）：root span 每个 run 一个，不共享 trace_id；跨度关联用 `vibe.run_id`/`vibe.task_id` 与 §8 的 link 规则。
- **版本工具落位**：不新增 `app/` 根模块（布局测试只允许边界文件）；版本读取放在 `app/config.py` 的纯函数或 `app/session/otel.py` 内部并缓存。eval 现有的 git 版 `agent_version` 保持独立，不被 runtime 引用。

### 3.4 隐私分级

| 级别 | 开启方式 | 允许导出 | 不允许导出 |
|---|---|---|---|
| **L0（默认）** | 无需配置 | run/tool/approval 计数与耗时、模型名、工具名、风险级别、审批 scope/allowed、status/stop_reason 词汇、token/cost 数值、`usage_estimated`、`run_id/session_id/task_id/request_id/tool_call_id`、错误类别（如 `TimeoutError`）、hash/计数 | 任务原文、prompt/completion、消息、工具参数与输出、命令文本、文件内容、工作区绝对路径、文件路径、审批预览、错误正文 |
| **L1（opt-in）** | `VIBE_OTEL_CONTENT=paths` | L0 + 工作区相对路径、文件变更 kind、验证命令的 `verification` 标志与 exit_code、错误首行截断 | 文件内容、命令完整输出、模型文本、任务原文、审批预览正文 |
| **L2（不在 M0–M3）** | `VIBE_OTEL_CONTENT=full`（暂不实现） | 需另行评审：prompt/completion、工具参数/输出、命令文本；必须先定义脱敏/凭据过滤 | — |

补充规则：

- 即使 L2，也必须剥离凭据：`authorization`、`api_key`、`password`、`token`、`sk-*`、私钥、环境变量值。
- `tool_start.args`、`run_start.task`、`user_profile_selection.selected` 始终不导出；L1 也只导出路径类字段。
- 哈希可用作关联（如 `vibe.tool.args_sha256`），默认不导出；L1 可选。
- 默认 endpoint 只允许 loopback；非 loopback 必须 `VIBE_OTEL_ALLOW_REMOTE=1`，且文档明确“等于把遥测数据发给该地址”。

### 3.5 命名、时间、ID 与状态

- **属性命名**：本项目自定义属性统一 `vibe.*`；模型相关优先使用稳定的 OTel GenAI 属性（`gen_ai.request.model`、`gen_ai.usage.input_tokens`/`output_tokens`、`gen_ai.response.finish_reasons`），但**以 `vibe.*` 为规范源**，`gen_ai.*` 只做兼容别名；语义约定仍标注 Development，必须锁版本（见 §14）。
- **span 名**（稳定、低基数）：`vibe.run`、`vibe.llm`、`vibe.tool`、`vibe.approval`、`vibe.subagent`。不把工具名/模型名拼进 span 名，放属性；压缩等无耗时信号用 span event 表达（§4.1）。
- **时间**：用事件 `ts`（epoch 秒）与 `duration_ms` 推 span 起止；`llm_end/tool_end` 的 end 取事件 `ts`，start = end - duration；`approval` 的 start = `approval_wait` 事件 `ts` - `seconds`；缺失 duration 时 start=end（零宽 span）并标 `vibe.timing.inferred=true`。
- **确定性 ID**（M0/M1 必须一致）：
  - `trace_id = sha256("vibe-trace\0" + session_id + "\0" + run_id).hexdigest()[:32]`；
  - `span_id = sha256("vibe-span\0" + trace_id + "\0" + span_key).hexdigest()[:16]`，`span_key` 固定为 `run`、`llm:<request_id>`、`tool:<tool_call_id>`、`approval:<tool_call_id>`、`subagent:<task_id 或 run_id>`；零值或冲突时追加 `:rehash` 重算；
  - 这是**可复现映射**，不是随机 ID；不用于安全用途。`vibe.run_id` 仍作为关联属性保留（原始 8 位 hex）。
- **状态映射**：
  - `run_end.status` 只有 `done/stopped/cancelled/error`（[AgentResult](../../app/runtime/agent.py) 注释口径）；`error` → span status ERROR；其余 → UNSET + `vibe.run.outcome`；
  - tool `ok=false` 且 `status` 为 `failed/timed_out/unknown` → ERROR；`cancelled` → UNSET + `vibe.tool.outcome=cancelled`；成功 → UNSET + `vibe.tool.ok=true`；
  - approval 拒绝/取消不标 ERROR（审批结果是正常业务结果）。
- **OTLP 传输**：M1 默认 OTLP/HTTP（4318）而非 gRPC，避免 grpc 依赖；JSON 与 protobuf 的 exporter 选择见 §14。
- **映射版本**：每个 span/指标带 `vibe.otel.mapping_version=1`；属性增删不改变既有名字含义，破坏性变更升版本并在 spec 记录。

## 4 事件 → 信号映射

### 4.1 span 树

```text
vibe.run                    (INTERNAL, 每 run 一个根；多 run 时按 §3.2.4 收口)
├─ vibe.llm                 (CLIENT, 每次模型请求/每次 attempt)
├─ vibe.tool                (INTERNAL, 每次工具执行)
│  └─ vibe.approval         (INTERNAL, 审批等待；仅需审批的工具)
│     └─（tool span 的 events：command_result / file_changes / verification_recorded / tool_retry）
├─ vibe.subagent            (INTERNAL, 子 Agent；/explore 的合成 run_end 或 M3a 实时事件)
└─（run span 的 events：context_snapshot / history_compacted / completion_audit /
    completion_rejected / no_progress / action_required / continuation / steering_applied /
    permission_policy_error / audit_baseline_failed / round_record_failed / llm_retry）
```

- `vibe.llm` **按 attempt 建 span**：`request_id = run_id:step:attempt` 每次不同；`llm_retry` 在上一次 attempt 的 `llm_end` 之后到达，作为**root span event**（`vibe.retry.attempt`、`vibe.retry.request_id`、`vibe.error.type`），不重新打开已关闭的 llm span。
- `vibe.tool`：`tool_start` 打开；`tool_end` 或 `tool_not_executed` 关闭。`tool_not_executed` 的 `reason` 可选（暂停预算路径没有 reason）；若 `tool_start` 已存在，按 `tool_call_id` 关闭为未执行（`vibe.tool.executed=false`），reason 缺省标 `unknown`；若不存在，创建零宽 span。重复终态（同一 `tool_call_id` 再收到终态）只计数，不改 span。
- `vibe.approval`：`approval_wait`（有 `seconds`、`tool`，无 `tool_call_id`）负责起止，关联到最近一个仍打开的同名 tool span；`approval`（有 `tool_call_id`）补 `allowed/scope`。两者顺序是 `approval_wait` 先、`approval` 后；缺 `tool_call_id` 时用同名最近打开 tool span 兜底，找不到则挂在 root 下。只有 `approval_wait`、没有 `approval`（取消/中断路径）时，span 以 wait 结束、标 `vibe.approval.decision=unknown`，不标 ERROR。
- `completion_audit`：只导出 `verified`、`completion.allowed`、`completion.codes`、`audit.status/integrity/coverage` 等枚举/布尔；不导出 findings 正文、messages、llm 报告。
- `context_snapshot`：字段来自 `build_context_snapshot`：`total_tokens`、`sections.{system,memory,tools,history}`、`context_window`、`input_capacity`、`remaining_tokens`、`over_budget`、`estimated`、`count_method`、`phase`、`reason`、`delta_tokens`、`snapshot_id`、`request_id`、`step`、`attempt`；**不导出 blocks**；`snapshot_id` 保留字符串，序号从末尾 `:N` 解析为 `vibe.context.sequence`（解析失败则省略）。
- `history_compacted`：`reason`、`before_tokens`、`after_tokens`、`target_tokens`、`recent_groups`、`summary_layers`、`offloaded`、`over_budget`；不导出 `notes`（可能含正文）。
- `budget_update` 不进入 span（频率过高）；其数值由 M2 指标使用。
- `kind=subagent` 的合成 `run_end`（`/explore`）→ `vibe.subagent` 零宽 span：`vibe.subagent.task_id`（从 `run_id` 前缀解析）、`vibe.subagent.status/stop_reason`、token/cost、`vibe.subagent.synthetic=true`；父级为当时打开的 run span，若没有则作为独立 root（不伪造 run 关系）。
- M3a 起，子 Agent 的实时事件在父 sink 内建 `vibe.subagent` span（父 = 触发它的 tool span 或 run 根）；与合成 `run_end` 同时存在时，合成事件只用于补终态/成本，不重复建 span。

### 4.2 字段映射（L0 默认）

| 事件 | 目标 | 属性/事件 | 说明 |
|---|---|---|---|
| `run_start` | root start | `vibe.run_id`、`vibe.model`、`vibe.step(0)` | `task` 正文不导出 |
| `run_end` | root end | `vibe.status`、`vibe.stop_reason`、`vibe.steps`、`vibe.tool_calls`、`vibe.tokens.prompt`/`completion`/`total`、`vibe.cost_usd`、`vibe.limit_tokens`、`vibe.remaining_tokens`、`vibe.usage_estimated` | status/outcome 按 §3.5；`remaining_tokens` 可能为 0 或省略 |
| `llm_start` | llm start | `vibe.step`、`vibe.request_id`、`vibe.attempt`（从 request_id 解析） | 每次 attempt 独立 span |
| `llm_end` | llm end | `gen_ai.request.model`、`gen_ai.usage.input_tokens`/`output_tokens`、`gen_ai.response.finish_reasons`、`vibe.cost_usd`、`vibe.usage_estimated`、`vibe.duration_ms` | 时长由事件 ts - duration_ms 推导 |
| `llm_error` | llm event | `vibe.error.type`（L0）/`vibe.error.message`（L1 截断） | 失败 attempt 的终态；span status 见 §3.5 |
| `llm_retry` | root event | `vibe.retry.attempt`、`vibe.retry.request_id`、`vibe.error.type` | 挂在 root，不重开 llm span |
| `tool_start` | tool start | `vibe.tool.name`、`vibe.tool_call_id`、`vibe.step` | **无 risk**（risk 在 tool_end 才有） |
| `approval_wait` | approval start | `vibe.approval.wait_seconds`、`vibe.tool.name` | 无 tool_call_id，按最近同名打开 tool span 关联 |
| `approval` | approval finish | `vibe.approval.allowed`、`vibe.approval.scope`、`vibe.tool_call_id` | 与 approval_wait 合并为一个 span |
| `tool_end` | tool end | `vibe.tool.ok`、`vibe.tool.status`、`vibe.tool.duration_ms`、`vibe.tool.risk` | `error` 正文不导出（L0） |
| `tool_not_executed` | tool end | `vibe.tool.reason`（可选）、`vibe.tool_call_id`、`vibe.tool.executed=false` | 与 tool_start 配对；无 start 时零宽 |
| `command_result` | tool event | `vibe.command.status`、`vibe.command.exit_code` | 不导出 command/cwd；execution 只有 status/exit_code |
| `file_changes` | tool event | `vibe.files.count`、`vibe.file.kind` 列表（L1 才含 `vibe.file.path`） | hash 默认不导出；不导出文件内容 |
| `verification_recorded` | tool event | `vibe.verification.status`、`vibe.verification.exit_code`、`vibe.verification.ok` | 命令文本不导出 |
| `tool_retry` | tool event | `vibe.retry.attempt`、`vibe.error.type` | 挂在 tool span |
| `context_snapshot` | root event | `vibe.context.total_tokens`、`vibe.context.partition.{system,memory,tools,history}`、`vibe.context.window`、`vibe.context.input_capacity`、`vibe.context.remaining_tokens`、`vibe.context.over_budget`、`vibe.context.estimated`、`vibe.context.count_method`、`vibe.context.phase`、`vibe.context.reason`、`vibe.context.delta_tokens`、`vibe.context.snapshot_id`、`vibe.context.sequence`（可解析时） | 不含 blocks；`loaded` 快照跳过 |
| `history_compacted` | root event | `vibe.compact.reason`、`vibe.compact.before_tokens`、`vibe.compact.after_tokens`、`vibe.compact.target_tokens`、`vibe.compact.recent_groups`、`vibe.compact.summary_layers`、`vibe.compact.offloaded`、`vibe.compact.over_budget` | 不含 notes 正文 |
| `completion_audit` | root event | `vibe.completion.verified`、`vibe.completion.allowed`、`vibe.completion.codes`、`vibe.audit.status/integrity/coverage` | 不含 findings/messages |
| `completion_rejected` | root event | `vibe.completion.codes`、`vibe.step` | 不含 guidance 正文 |
| `no_progress` | root event | `vibe.progress.action`、`vibe.progress.reason`、`vibe.progress.streak`、`vibe.tool.name` | — |
| `action_required` / `no_action` / `continuation` | root event | `vibe.step`、`vibe.action.reason`（若有） | 只枚举/计数 |
| `steering_applied` | root event | `vibe.steering.chars`、`vibe.step` | 不含 steering 正文 |
| `permission_policy_error` / `audit_baseline_failed` / `round_record_failed` | root event | `vibe.error.type` | 不含堆栈/正文 |
| `run_end(kind=subagent)` | `vibe.subagent` span | `vibe.subagent.task_id`、`status`、`stop_reason`、tokens、cost、`synthetic=true` | 父级=当前 run span 或独立根 |

> 完整字段以 M1 的映射表测试为准；上表是验收基线，不能静默增删。

## 5 M0：离线导出

### 5.1 行为

- 输入：`<workspace>/.vibe/sessions/<session>/trace.jsonl`，以及轮转文件 `trace.jsonl.1 … trace.jsonl.N`（[app/session/store.py](../../app/session/store.py) 的 `_maybe_rotate_trace`）；可选 `meta.json`（用于 session id/purpose）。
- **读取顺序**：从最旧备份 `trace.jsonl.N` → … → `trace.jsonl.1` → `trace.jsonl`；缺失跳过；损坏行计数。按 §3.2 的 canonical projection 处理 skip/多 run/合成子 Agent 事件。
- 输出格式（`--format`）：
  1. `debug-jsonl`（默认）：每行一个 span，字段固定、确定性：
     `{"schema_version":1,"trace_id":"32hex","span_id":"16hex","parent_span_id":"16hex|null","name":"vibe.run","start_unix_nano":…,"end_unix_nano":…,"status":{"code":"UNSET|OK|ERROR"},"attributes":{…},"events":[…]}`。
  2. `otlp-json`：标准 OTLP/HTTP JSON（`resourceSpans`），可直接交给 collector 的 file/log exporter 或后端导入；至少包含 `resource`、`scopeSpans`、`spans`。
  3. `summary`：复用现有 `--trace-summary` 口径的人类可读摘要 + 将要导出的 span 计数。
- CLI 草案（脚本必须 `sys.path.insert(0, ROOT)`，与现有 scripts 一致）：

```bash
python scripts/export_otel.py --workspace PATH --session SID --out FILE \
    [--format debug-jsonl|otlp-json|summary] [--content off|paths] \
    [--all-sessions] [--last-hours H] [--endpoint URL] \
    [--allow-remote] [--strict]
```

- 默认只写 `--out`（`-` 表示 stdout），不写 `.vibe/`、不联网；只有显式 `--endpoint` 才 POST OTLP/HTTP JSON。endpoint 默认只允许 loopback；非 loopback 需要 `--allow-remote` 或 `VIBE_OTEL_ALLOW_REMOTE=1`，否则拒绝执行。
- `--last-hours H`：按 run 级别过滤——run 的 ending/最后事件 `ts` 落在窗口内才整段输出，保留该 run 的完整事件（含窗口外的 `run_start`）；没有 run 上下文的孤立事件（loaded/子 Agent 合成）按自身 `ts` 过滤。
- 退出码：`0` 成功（损坏行单独计数）；`2` 会话/参数不可用；`1` 致命 IO/写失败。`--strict` 时以下任一情况返回 `1`：损坏行；任一 run 没有 `run_start`；最后一个 run 没有 `run_end`/ending（中间 run 由下一个 `run_start` 以 superseded 收口属正常，不算违规）。`--strict` 的“关键事件”即 `run_start` 与最终 `run_end`/ending。
- `trace_id`/`span_id` 使用 §3.5 的确定性映射；这是 M0/M1 输出可对账的前提。

### 5.2 与 M1 的共用

- 抽出纯函数 `events_to_spans(events, *, session_id, content) -> list[SpanRecord]` 与 `spans_to_otlp_json(...)`；M0 与 M1 共用，测试覆盖同一 fixture。
- M0 不需要 SDK、不需要 `opentelemetry` 包；OTLP JSON 序列化由本项目实现（只依赖标准库），避免离线环境安装依赖。
- M0 可独立于 M1 交付：即使最终不接运行时导出，也能先用它验证“这棵 span 树是否值得长期看”。
- M1 的 sink 复用同一映射模块（属性/状态/ID 规则与 §4.2 的映射表）；可用 `events_to_spans` 做整段转换，或在事件到达时调用同一组 helper 增量构建，禁止复制一份映射逻辑。M0 与 M1 的同一 fixture 必须产出相同 span 结构。

### 5.3 测试与验收

- `tests/test_otel_export.py`：每类事件的 span 树、parent 推导、缺失/乱序/重复、多 run（plan repair）、`loaded`/`user_profile_selection` skip、`kind=subagent` 合成事件、轮转文件顺序、`--last-hours`、L0/L1 字段差异、`--strict`、损坏行计数。
- `tests/test_otel_cli.py`（或复用 CLI 测试）：会话不存在、`--format` 互斥、`--endpoint` 非 loopback 被拒、`--strict` 退出码、无新依赖。
- 验收：
  - 对真实 `.vibe` 会话（含轮转文件）导出 debug JSONL：每 run 根 span 的 `vibe.run_id` 与 trace.jsonl 一致；span 计数与关键事件可对账；多 run 请求按 `superseded` 收口；
  - 默认输出不含任何任务原文/工具参数/路径；`--content paths` 只多出相对路径；
  - `otlp-json` 输出能被 `jq` 解析且 `resourceSpans[*].scopeSpans[*].spans` 计数与 debug 输出一致；
  - 同一 trace 多次导出得到相同 `trace_id/span_id`（确定性）。

## 6 M1：进程内 span 导出

### 6.1 模块与接口

新增 `app/session/otel.py`（或同等的单一模块，落位遵循 [app/README](../../app/README.md)）：

```python
@dataclass(frozen=True)
class OtelConfig:
    enabled: bool = False            # VIBE_OTEL
    signals: tuple[str, ...] = ("traces",)  # traces / metrics（M2）
    content: str = "off"             # off / paths
    allow_remote: bool = False
    debug_file: str | None = None    # VIBE_OTEL_DEBUG_FILE
    service_name: str = "vibe"
    traces_exporter: str = "otlp"    # otlp | none；debug_file 非空时走 debug-file backend

class OtelSink:
    def __init__(self, config: OtelConfig, *, backend=None): ...
    def begin_session(self, *, session_id, purpose): ...
    def begin_run(self, *, request_id, task_id=None, goal_id=None, plan_id=None,
                  plan_version=None, segment=None, mode=None): ...
    def emit(self, event: dict) -> None: ...   # 永不抛异常
    def flush(self, timeout_ms: int = 5000) -> None: ...
    def close(self) -> None: ...
    # M3a
    def begin_subagent(self, *, task_id, parent_span=None, synthetic=False) -> object: ...
    def end_subagent(self, handle, *, status, stop_reason="", usage=None, cost_usd=0.0): ...
```

> `begin_run` 只预登记 stdio 请求与任务身份；`TraceRecorder.run_id` 直到第一个 `run_start` 事件才产生，因此 root span 在 `run_start` 到达时创建（缺失 `run_start` 时在首个带 `run_id` 的事件上懒创建），不能在 `agent.run` 前假定 run_id。`segment` 来自 `RunTask.segment`，`goal_id` 来自 `run_task.record.goal_id`，`plan_id/plan_version/mode` 来自 `run.task` meta/record（不是 meta 里全都有）。

- **backend seam**：`OtelSink` 只依赖抽象 backend（`start/end span`、`add event`、`record metric`、`flush`），生产 backend 懒加载官方 SDK；测试用 `RecordingBackend`/`InMemorySpanExporter`。这样没有安装 SDK 也能跑大部分单测，且 `VIBE_OTEL=0` 时完全不 import SDK。
- 工厂：`build_otel_sink(config: Config, session: SessionStore) -> OtelSink | None`；关闭时返回 `None`，调用方零分支。
- 接线：
  - `serve(..., otel=None)` 增加可选参数；`serve()` 内解析出 `session` 后构造 sink（测试可注入 fake），把 sink 传给 `_Connection`；`_Connection.run` 在**每次** `agent.run` 前调用 `begin_run`（plan repair 循环会多次调用，`request_id` 可重复，`run_id` 由随后的 `run_start` 确定；`begin_run` 必须幂等）。
  - `_Connection` 内 `emit_trace` 按 §3.2 组合“本地写入 + otel.emit”；`record_trace` 保留现有分流。
  - `--task` oneshot（[app/eval/oneshot.py](../../app/eval/oneshot.py)）在 `options.bench=False` 时构造 sink，`finally` 中 flush；`options.bench=True` 时**不构造 sink，并清掉子进程环境里的 `VIBE_OTEL`/`OTEL_*`**，保证 bench 不导出。
  - `serve()` 的 `finally` 在发送 `closed` 前限时 flush；`closed` 之后不再导出。
  - `--eval` / SWE-bench / bench 不接 M1（M4 再评估）；它们不构造 sink，因此 `VIBE_OTEL` 环境变量在这些路径上不产生导出。
- 调试出口：`VIBE_OTEL_DEBUG_FILE=/path/spans.jsonl` 时使用 debug-file backend，写与 M0 相同的 `debug-jsonl` 格式；这是 M1 无 collector 的确定性验收方式，不得写 stdout。

### 6.2 生命周期与顺序

1. 进程启动：`serve()` 在拿到 `session` 后 `build_otel_sink`；失败（缺 SDK、配置非法）→ 打印一次 stderr 警告并返回 `None`（不做 fail-closed 启动，见 §11）。
2. run 开始：`_Connection.run` 在每次 `agent.run` 前调用 `begin_run(request_id, task_id=…)` 预登记上下文（可重复、幂等）；root span 在收到 `run_start` 时创建并挂 resource/run 属性；缺 `run_start` 时在首个带 `run_id` 的事件上懒创建。
3. 实时事件：sink 维护 `run_id -> {root, llm spans, tool spans, subagents}`；乱序事件按 §4 规则补齐；skip 清单按 §3.2.6。
4. run 结束：**只有落盘的最终 `ending`** 关闭 root（`record_trace` 里的中间 `run_end` 不直接喂 sink）；多 run 按 §3.2.4 收口。
5. 连接/进程关闭：`serve` 的 `finally` 先停任务、关 MCP，再 flush（≤5s）→ shutdown → `send("closed")`；导出失败只记 stderr 一次，不阻塞退出。
6. 每个进程一个 sink；一个 sink 可顺序处理多个 run；不跨进程共享 span 状态。

### 6.3 配置（M1）

| 变量 | 默认 | 说明 |
|---|---|---|
| `VIBE_OTEL` | `0` | 总开关；`1/true/yes` 开启 |
| `VIBE_OTEL_SIGNALS` | `traces` | `traces`/`metrics`/`traces,metrics`（M2 起支持 metrics） |
| `VIBE_OTEL_CONTENT` | `off` | `off`/`paths`（L0/L1）；`full` 未实现 |
| `VIBE_OTEL_ALLOW_REMOTE` | `0` | 允许非 loopback endpoint |
| `VIBE_OTEL_DEBUG_FILE` | 空 | 非空时写 debug-jsonl，不发网络（测试/离线验收） |
| `VIBE_OTEL_TRACES_EXPORTER` | `otlp` | `otlp` / `none`（用于只算指标） |
| `VIBE_OTEL_UI_CONTEXT` | `1`（M3b-B） | 是否宣告 `otel_ui_context` capability；M3b 才生效 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://127.0.0.1:4318` | 标准变量；traces/metrics 可各自覆盖 |
| `OTEL_EXPORTER_OTLP_HEADERS` | 空 | 认证/租户头；不写入日志 |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | `10000` | 毫秒；导出超时不影响 run |
| `OTEL_SERVICE_NAME` | `vibe` | 覆盖 resource |
| `OTEL_TRACES_SAMPLER` | `parentbased_always_on` | 本地单用户默认全采；M4 可调 |
| `OTEL_RESOURCE_ATTRIBUTES` | 空 | 运维附加属性；不得含凭据 |
| `VIBE_OTEL_QUEUE_MAX` | `2048` | 批量队列上限（防内存无界） |
| `VIBE_OTEL_ATTR_MAX_CHARS` | `512` | 字符串属性截断上限（按 UTF-8 字节校验） |

### 6.4 失败与边界

| 场景 | 行为 |
|---|---|
| SDK/exporter 未安装且 `VIBE_OTEL=0` | 不 import、不告警、零变化 |
| SDK 未安装且 `VIBE_OTEL=1` | stderr 警告一次；本进程不再尝试；任务照常 |
| endpoint 不可达/超时 | 后台批量导出失败；最多重试 SDK 默认；run 正常结束；stderr 限频诊断 |
| 队列满 | 丢弃新 span 并在进程内计数（M2 起导出 `vibe.otel.dropped` counter），不阻塞 loop |
| 属性超长/类型非法 | 截断或跳过该属性；span 仍导出 |
| 未知事件类型 | 忽略并计数；不影响已知事件映射 |
| 重复 `run_end` | 按 `(run_id, run_end)` 去重；正常 canonical stream 只有最终 `ending` |
| 缺 `tool_end`/`run_end` | 退出时收口并标 `vibe.incomplete=true` |
| 多 run（plan repair） | 中间 run 由下一个 `run_start` 收口（superseded），最终 `ending` 收口最后一个 |
| `--bench`（`run_oneshot` 进程内） | **不构造 sink**，并清掉 `VIBE_OTEL`/`OTEL_*` 以免 bash 子进程继承 |
| `--eval` / SWE-bench | 不构造 sink（M1 范围外） |
| `--worker` 执行子进程 | 走 `serve`，正常导出；worker 父进程不导出 |
| `--attach` | 不构造 sink；只中继帧 |
| TUI 协议 | M1 不改帧；不新增 `capabilities`；导出与 TUI 无关 |
| stdout | 任何导出/SDK 日志不得写 stdout；违反视为缺陷 |

### 6.5 依赖

- 可选 extra（名称建议 `otel`）：

```toml
[project.optional-dependencies]
otel = [
  "opentelemetry-sdk==1.45.1",
  "opentelemetry-exporter-otlp-proto-http==1.45.1",
]
```

- 版本必须与已装 `opentelemetry-api`（当前 1.45.1）一致并精确锁定；安装前确认包可用。`uv.lock` 一并更新；`.pylibs` 回退环境不预装，缺依赖时测试跳过 SDK 集成用例。
- 不允许在 `core/`、`providers`、`tools` 等路径直接 import OTel；只允许 `app/session/otel*.py` 与入口接线层。
- 若用户拒绝新增依赖，改用 §14 的“零依赖 OTLP/HTTP JSON exporter（httpx）”方案，M1 的 backend seam 不变，只换 backend 实现。

### 6.6 测试与验收

- `tests/test_otel_sink.py`：映射（每类事件）、父子、时间推导、**每 attempt 一个 llm span + llm_retry 挂 root**、approval 配对、tool_not_executed（有/无 reason、有/无 tool_start）、L0 无内容、L1 只加路径、fail-open、队列丢弃、重复 run_end、多 run 收口、缺事件收口、subagent 合成事件。
- `tests/test_stdio_otel.py`：`serve(otel=…)` 注入 fake backend；fake provider 跑一个 run；断言 root+llm+tool+approval 数量与 `trace.jsonl` 对账；`VIBE_OTEL=0` 时 sink 为 None 且不 import SDK；flush 在 `closed` 之前（fake emit 顺序断言）；plan repair 多 run fixture。
- `tests/test_oneshot_otel.py`：`--task --fake` + `VIBE_OTEL_DEBUG_FILE` 产出文件；`--bench` 不产生导出、子进程 env 已清理；退出码与不开启时一致。
- `tests/test_otel_config.py`：非法组合、非 loopback 拒绝、`full` 未实现时明确报错/降级。
- 验收：
  - 开启 debug 文件后，一次 `--fake` run 的 span 树与 trace 事件数一致；`vibe.run_id` 对齐；重试有多个 `vibe.llm`；
  - 关闭时 `diff` 现有 trace/测试基线无变化；
  - 删除 SDK 模拟缺失时任务仍成功；
  - `--bench` 不导出；
  - 全量 `./run_tests.sh -q` 与 TUI 测试不回归。

## 7 M2：指标导出

### 7.1 信号清单

| 指标 | 类型 | 单位 | 属性（低基数） | 来源 |
|---|---|---|---|---|
| `vibe.llm.calls` | Counter | `1` | `model`、`status`、`usage_source=provider\|estimated`、`subagent=true\|false` | `llm_end/llm_error` |
| `vibe.llm.tokens` | Counter | `{token}` | `direction=input\|output`、`model`、`usage_source`、`subagent` | `llm_end` |
| `vibe.llm.duration` | Histogram | `ms` | `model`、`status` | `llm_end`（duration_ms；缺失时跳过） |
| `vibe.tool.calls` | Counter | `1` | `tool`、`status` | `tool_end/tool_not_executed` |
| `vibe.tool.duration` | Histogram | `ms` | `tool`、`status` | `tool_end` |
| `vibe.approval.wait` | Histogram | `s` | `tool`、`allowed` | `approval_wait` |
| `vibe.run.duration` | Histogram | `ms` | `status` | `run_end`（最终 ending） |
| `vibe.run.steps` | Histogram | `1` | `status` | `run_end` |
| `vibe.cost.usd` | Counter | `{USD}` | `model`（有则）、`source=llm\|subagent_run`、`pricing_configured=true\|false` | `llm_end`；仅有子 Agent 合成 `run_end` 时按 `source=subagent_run` 计入 |
| `vibe.subagent.calls` | Counter | `1` | `status`、`source=live\|synthetic` | `run_end(kind=subagent)` / M3a 实时结果 |
| `vibe.subagent.tokens` | Counter | `{token}` | `direction` | 同上（未接入实时事件时的兜底） |
| `vibe.context.tokens` | ObservableGauge | `{token}` | `partition`（system/memory/tools/history）、`estimated` | 最近一次 `context_snapshot` |
| `vibe.context.window` | ObservableGauge | `{token}` | `model` | `context_snapshot` |
| `vibe.budget.used` | ObservableGauge | `{token}` | `scope=run` | 最近一次 `budget_update`/`run_end` |
| `vibe.budget.limit` | ObservableGauge | `{token}` | `scope=run` | 同上 |
| `vibe.otel.dropped` | Counter | `1` | `signal=traces\|metrics`、`reason=queue_full\|export_error` | sink 内部计数 |

### 7.2 口径与约束

- `usage_source` 必须区分 provider 真实 usage 与本地估算；不可把 `estimated=true` 的 token 当账单价。
- `pricing_configured` 由 `input_price_per_1m/output_price_per_1m` 是否为 0 推导；**价格默认 0 不代表免费**，指标必须可区分。
- **子 Agent 成本口径**：M2 阶段子 Agent 的 `llm_end` 事件通常未落盘（`trace_path` 未传）；`/explore` 的合成 `run_end` 带 tokens/cost，delegate 连合成事件也没有。因此 M2 从 `/explore` 合成事件按 `source=subagent_run` 计入，并标 `source` 以免与 `llm` 重复；delegate 子 Agent 成本在 M2 **未计**，必须在文档/验收里如实标注，M3a 接入实时事件后补齐。M3a 接入实时子 Agent 事件后，改用实时事件（`subagent=true`）并跳过合成事件的成本/token 计数，避免双计；`vibe.subagent.calls` 的 `source` 用来对账。
- 指标属性禁止出现 `run_id/session_id/task_id/request_id/tool_call_id`、文件路径、模型原文；`tool` 与 `model` 为低基数（工具名来自注册表 + 已配置 MCP；同进程出现超过 64 个不同工具名时折叠为 `other`）。
- `vibe.budget.*` M2 只做 run 级；task/goal 级聚合留 M3（任务身份只作为 span 属性，不进指标 label）。
- 导出间隔默认 60s（`OTEL_METRIC_EXPORT_INTERVAL`），退出前 flush；关闭时零指标开销。

### 7.3 失败与边界

| 场景 | 行为 |
|---|---|
| `VIBE_OTEL_SIGNALS` 不含 metrics | 不创建 MeterProvider，零指标开销 |
| SDK 未安装 / 初始化失败 | 与 M1 相同的 stderr 一次警告；任务照常 |
| 采集数值非法（负数/NaN/非数字） | 跳过该样本并计数，不伪造 0 |
| 指标导出失败/collector 离线 | 丢弃本周期数据并计数；run 不受影响 |
| 关闭/退出 | flush 后 shutdown；超时只丢数据 |
| 子 Agent 成本重复风险 | M3a 起按 §7.2 去重；测试覆盖“实时 + 合成同时存在” |

### 7.4 测试与验收

- 用 `InMemoryMetricReader`：一次 fake run 后断言 counter 累计、histogram 数量、gauge 最新值；`usage_estimated=true` 时 label 正确；无动态 ID label。
- 关闭/未启用 metrics 时无 MeterProvider 创建；缺 SDK 时与 M1 相同的 fail-open。
- 子 Agent 合成成本只计一次；M3a 实时事件接入后不双计。
- 验收：debug/metrics 输出条目与 trace 事件计数可对账；`--trace-summary` 数字不变（导出不修改本地证据）。

## 8 M3：跨进程与子 Agent 关联

### 8.1 M3a：worker 执行进程 / 子 Agent / attach（必做）

- **worker 执行进程**：worker 父进程 spawn `--stdio --session` 子进程；子进程在 `serve` 内构造 sink。`run.task` 帧的 meta 字段实际为 `kind/task_id/objective`，`kind=run` 时另有 `mode/plan_id/plan_version`；**没有 `goal_id`/`segment`**。因此 root span 属性取值规则：
  - `vibe.task_id`：meta 或 `run_task.record.task_id`；
  - `vibe.goal_id`：`run_task.record.goal_id`；
  - `vibe.plan_id`/`vibe.plan_version`：`run_task.record` 或 meta 中存在时；
  - `vibe.segment`：`RunTask.segment`（不是 record 字段）；
  - `vibe.mode`：meta 或 record。
- worker Resource 增加 `vibe.worker=true`（来自 `VIBE_WORKER` 环境或子进程参数）。
- **父进程不重复导出**：`--worker` 监督进程不构造 sink、不导出 run span；只由执行子进程导出。
- **子 Agent**：`run_subagent` 当前在**父进程内**执行；`trace_path` 是可选参数，现有 delegate/`/explore`/CLI 调用都未传，事件默认丢弃；`/explore` 只在父 trace 追加合成 `run_end(kind=subagent)`，`--explore` CLI 不写父 trace。M3a 改为：
  - `run_subagent(..., otel: OtelSink | None = None, parent_span=None)`；接线点：`register_delegate_tool(..., otel=...)`、stdio 的 `/explore` 命令、`app/__main__.py` 的 `--explore` 路径（若该进程构造了 sink）；
  - 父 sink 在子 Agent 开始时 `begin_subagent(task_id=…, parent_span=当前 tool/run span)`，结束时 `end_subagent(...)`；子 Agent 内部的 `llm/tool` 事件通过它自己的 `Agent.run(trace=…)` 传给 sink（标 `vibe.subagent=true`），从而进入同一 trace；
  - `/explore` 命令在 run 外执行：没有父 run span，建独立 root `vibe.subagent`（同 resource，不伪造 parent）；如果同时收到合成 `run_end`，只补终态/成本，不重复建 span；
  - delegate 在工具执行内：`vibe.subagent` 的父为当前 `vibe.tool` span（找不到则 root）；
  - 没有 sink（默认关闭）时行为与今天完全一致：不写 trace 文件、不产生额外开销。
- **attach/observer**：attach 只中继帧，不重新导出后端事件；TUI observer 只渲染。只由执行进程导出，避免重复 span。
- **关联键**：root span 至少含 `vibe.run_id`、`vibe.session_id`（resource 亦可）、`vibe.task_id`、`vibe.segment`；子 Agent 含 `vibe.parent_run_id`/`vibe.parent_task_id`；有父上下文时用真实 parent span，否则用属性 + span link。
- **测试**：fake worker 两个段；子 Agent 一次 delegate 与一次 `/explore`；断言 `vibe.subagent` 父子/链接正确、`segment/goal_id` 来自正确字段、attach 不产生重复 span、合成事件与实时事件不重复；无 collector 时用 debug 文件。
- **失败边界**：无父上下文 → 独立 root + link 属性；sink 未传 → 零变化；合成 `run_end` 与实时事件并存 → 实时优先、合成只补终态；worker env 未继承 → 不导出（不报错）。

### 8.2 M3b：TUI 关联与全链路（可选，可延后）

- **前置**：TUI 是 Node/Ink 进程，自身 trace 需要 `@opentelemetry/*` JS 依赖；没有 TUI span 就传播 `traceparent` 会产生“父 span 不存在”的悬空引用。因此 M3b 分两档：
  - **B（M3b 最小切片，推荐）**：TUI 不建 span、不引入 OTel JS；通过 capability `otel_ui_context` 在 `run` 请求带可选 `ui` 对象（`interaction_id`、`client`），后端只把它作为 root span 属性 `vibe.ui.interaction_id` / `vibe.ui.client`，不建立父子关系；
  - **A（M4 评估）**：TUI 引入 OTel JS 并建 UI span，与后端 trace 做父子传播；依赖与工作量更大。
- 协议规则（B 现在就做，A 预留）：
  - 后端在 `ready.capabilities` 增加 `otel_ui_context`（仅 `VIBE_OTEL=1`、traces 开启且 `VIBE_OTEL_UI_CONTEXT` 不为 0 时）；A 再增加 `otel_trace_context`；
  - 客户端仅在 capability 存在时发送新字段；`run` 请求的字段集校验按现有“精确字段集”扩展；未知字段仍按旧规则拒绝；
  - B 的 `ui` 对象只允许 `interaction_id`（≤128）与 `client`（枚举 `ink`）；非法返回 `invalid_request`；
  - A 的 `traceparent` 必须匹配 W3C v00 格式：`^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$`，且 trace-id/span-id 不能全零、version 不能为 `ff`；否则返回 `invalid_request`；
  - capability 未声明但请求里出现新字段 → `invalid_request`（fail closed，便于发现客户端 bug）；
  - 旧客户端不发送、旧后端不声明，双方保持 JSONL v1 兼容；不升级协议版本号。
- 不做：TUI 导出后端回放事件、TUI 作为 collector、跨机器多客户端。
- 测试：Python 协议校验（capability 开关、合法/非法 `ui`/`traceparent`、旧字段集兼容）；TUI 测试覆盖 capability 消费与参数构造。
- **失败边界**：capability 误发/漏发、字段非法、旧新组合、TUI 只读 observer 不导出，各有测试。

## 9 M4+：生产化与后续方向

- **部署样例**：`dev/observability/`（或等价目录）提供 OTLP collector + 一个后端（Jaeger/Tempo）最小 compose，默认只绑定 loopback；Grafana/Prometheus 可选。文档写清“本地验证不进入日常默认启动”。
- **采样与保留**：本地默认全采（单用户量小）；需要长期保留时在 collector 做 tail sampling 与 retention，不把策略写死在 harness。`trace.jsonl` 轮转策略（`VIBE_TRACE_MAX_BYTES`/`VIBE_TRACE_BACKUPS`）保持独立，导出不替代本地保留。
- **安全**：TLS/认证头只从 env 读取且不写日志；远程 endpoint 必须显式 allow；L2 内容导出必须单独评审并先做脱敏测试。
- **语义约定**：锁定 SDK 与 semconv 版本；`gen_ai.*` 仅做兼容别名，规范源仍是 `vibe.*`；映射版本升级写迁移说明。
- **Prometheus**：优先经 collector 暴露，不默认在 Python 进程开 `/metrics` 端口；若需要再加 opt-in 本地端点。
- **eval/bench**：单独 opt-in（容器内默认无网络；`bench/requirements-bench.txt` 不默认加 OTel）；`--telemetry-out` 与 OTel 并存，不互相替代。
- **OTel Logs**：暂不做；`stderr`/`worker.log` 保持现状，后续需要再评估。
- **M5 探索（不承诺）**：TUI OTel JS 全链路、成本/预算告警联动、多机集中观测、远程 attach 遥测。

## 10 接口与配置汇总

- **新增模块（实施时）**：`app/session/otel.py`（M1 sink/backend/config）、`app/session/otel_export.py`（M0 纯 canonical projection/映射/序列化，M1 复用）、`app/session/otel_metrics.py`（M2）。M1 前先更新 [docs/STRUCTURE.md](../STRUCTURE.md) 与 [app/README](../../app/README.md)。
- **入口接线**：`app/stdio.py`（`serve`/`_Connection`/`record_trace`/`emit_trace`）、`app/eval/oneshot.py`、`app/config.py`；M3a 增加 `app/runtime/subagents.py` 与 worker 相关；`app/__main__.py` 的 `--explore` 若要导出也需传 sink。
- **CLI**：M0 `scripts/export_otel.py`（脚本开头 `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`）；M1 不加交互入口，只用 env + `VIBE_OTEL_DEBUG_FILE`；M4 可加 `--otel-smoke` 或 `scripts/otel_smoke.sh`。
- **配置**：§6.3 为 M1 全集；M2 追加标准 `OTEL_EXPORTER_OTLP_METRICS_*` 与 `OTEL_METRIC_EXPORT_INTERVAL`；M3b-B 使用 `VIBE_OTEL_UI_CONTEXT`；M3b-A 另议。
- **文档更新**：`.env.example`、[USAGE](../USAGE.md)（开启/关闭/隐私/离线导出/排障）、[HARNESS §10](../HARNESS.md)（M1 后新增“OTel 导出”小节，区分已实现/未实现）、[open-decisions](open-decisions.md) #5 状态。

## 11 失败与兼容

| 场景 | 行为 |
|---|---|
| 默认未开启 | 零变化；不 import SDK、不建线程、不写文件（O1） |
| 只有 `opentelemetry-api`、没有 SDK | 视为未安装；`VIBE_OTEL=1` 时 stderr 警告一次；任务正常 |
| collector/网络失败 | run 不受影响；批量重试/丢弃；stderr 限频 |
| `VIBE_OTEL=1` + 非 loopback endpoint 但未 `ALLOW_REMOTE` | 拒绝启用并 stderr 提示；不回退成“悄悄发本地” |
| 多 run（plan repair） | 中间 run 收口为 superseded，不伪造成功；M0/M1 一致 |
| `loaded` / `user_profile_selection` / 合成子 Agent | 按 §3.2.6 skip 或映射 `vibe.subagent`，不产生错误 run 根 |
| 旧/新 TUI 协议 | M1–M3a 不改协议；M3b 必须 capability 门控 + 双向兼容测试 |
| worker 子进程 env 继承 | 复用现有 `{**os.environ, **child_env}`；新增 `VIBE_OTEL_*` 原样继承；bench/eval 强制关闭 |
| bench 在本进程 | `run_oneshot(options.bench=True)` 不构造 sink，并清理子进程 env |
| 本地文件损坏/缺字段/轮转 | 跳过/计数；M0 读轮转顺序；不伪造 span |
| 进程被 kill | 已批量导出的 span 保留；未 flush 的丢失；本地 trace 不受影响 |
| 指标 label 爆炸 | 固定枚举 + 工具名折叠 + 禁止 ID label |
| 版本漂移 | 锁 SDK/semconv；映射版本属性；升级在 spec 记录 |

## 12 测试计划（先写测试）

| 阶段 | 测试文件 | 关键用例 |
|---|---|---|
| M0 | `tests/test_otel_export.py`、CLI 测试 | canonical projection、多 run、skip、合成子 Agent、轮转文件、缺失/乱序、L0/L1、`--strict`、确定性 ID、exit code |
| M1 | `tests/test_otel_sink.py`、`tests/test_stdio_otel.py`、`tests/test_oneshot_otel.py`、`tests/test_otel_config.py` | 每 attempt llm span/retry、approval/tool 配对、debug 文件、flush 顺序、关闭零副作用、SDK 缺失、bench 不导出、plan repair 多 run、非 loopback 拒绝 |
| M2 | `tests/test_otel_metrics.py` | counter/histogram/gauge、label 基数、estimated/价格 label、子 Agent 成本去重、dropped counter、flush |
| M3a | `tests/test_otel_links.py`、worker/subagent 集成测试 | task/goal/plan/segment 字段来源、子 Agent 父/链接、合成与实时去重、attach 不重复 |
| M3b | `tests/test_stdio_protocol_ui_context.py`、`tui/tests/ui-context.test.ts`（A 另加 traceparent 用例） | capability 协商、`ui` 字段校验、旧客户端兼容、非法 traceparent |
| 文档 | `tests/test_docs_links.py` | 新 spec 链接/锚点可解析 |
| 全量 | `./run_tests.sh -q`、`npm --prefix tui test` | 无回归；`./scripts/verify_all.sh` 可选 |

所有测试默认离线；真实 collector smoke 单独记录，不作为单元测试前置条件。

## 13 验收标准

**M0**

- [ ] 真实会话（含轮转文件）导出 debug JSONL：每 run 根 span 的 `vibe.run_id` 与 trace.jsonl 一致；span 计数与关键事件可对账；
- [ ] plan repair 多 run 的中间 run 被标 `vibe.incomplete=true` 且 `vibe.close.reason=superseded`；`loaded`/`user_profile_selection` 不生成 run 根；`/explore` 合成事件生成 `vibe.subagent`；
- [ ] 默认 L0 不含任务原文/工具参数/路径/命令文本；`--content paths` 只多相对路径；
- [ ] 同一 trace 多次导出得到相同 `trace_id/span_id`；`otlp-json` 可解析且 span 数与 debug 一致；
- [ ] `--strict` 在损坏行、任一 run 缺 `run_start`、最后一个 run 缺 ending 时返回 1；无新依赖、无网络默认、不写 `.vibe/`。

**M1**

- [ ] `VIBE_OTEL=0` 时 `trace.jsonl`、测试基线、退出码零变化；
- [ ] 开启后 fake run 的 span 树与本地事件一致，`vibe.run_id` 对齐，每次 attempt 一个 llm span，`llm_retry` 在 root；
- [ ] tool/approval 父子与终态正确；`tool_not_executed` 有/无 reason、有/无 tool_start 都能收口；
- [ ] SDK 缺失/endpoint 不可达时任务成功、stderr 最多一次警告；
- [ ] `serve` 的 flush 在 `closed` 之前；`--bench` 不导出；eval 不接；
- [ ] `VIBE_OTEL_DEBUG_FILE` 可在无 collector 环境验收；
- [ ] stdout 无任何 OTel 输出；全量 Python 测试通过。

**M2**

- [ ] 指标与 trace 事件计数对账；`usage_source`/`pricing_configured` 正确；
- [ ] 子 Agent 成本只计一次（合成或实时，不双计）；`vibe.otel.dropped` 可见；
- [ ] 指标无动态 ID label；关闭/未启用 metrics 时零开销；
- [ ] 全量测试通过。

**M3a**

- [ ] worker 执行进程的 root span 带 `task_id/segment`（来自正确字段）；子 Agent 的 `vibe.subagent` 有父/链接；
- [ ] 合成 `run_end` 与实时子 Agent 事件不重复；attach/observer 不产生重复 span；
- [ ] 无 collector 的 debug 文件验收通过。

**M3b（若做）**

- [ ] capability 未声明时旧客户端行为不变；非法 `ui`/`traceparent` 返回 `invalid_request`；
- [ ] 新 TUI + 旧后端、旧 TUI + 新后端均不破坏；
- [ ] TUI 不导出后端回放事件；B 档不建立错误的父子关系。

**真实后端（可选，用户配合）**

- 本地 collector + Jaeger/Tempo 启动一次真实任务，能在 UI 看到 `vibe.run → vibe.llm/tool/approval` 树（子 Agent 任务另见 `vibe.subagent`）；记录 `trace_id` 与本地 `run_id` 对账；证据放 `.vibe/otel/<stamp>/`（截图/导出 JSON），不进入提交。

## 14 待用户拍板

| # | 事项 | 选项 | 推荐 | 不拍板也能做 |
|---|---|---|---|---|
| D1 | 目标系统/endpoint | 本地 Jaeger/Tempo；本地 collector → 远端；Langfuse/Phoenix 兼容端点 | 本地 OTLP collector → Jaeger/Tempo | M0/M1（debug 文件） |
| D2 | 隐私级别 | L0 默认；L1 路径；L2 正文 | 先 L0，L1 作为 opt-in；M0–M3 不做 L2 | M0/M1/M2 |
| D3 | 依赖方式 | 官方 SDK optional extra；零依赖 httpx 手写 OTLP/HTTP JSON | 官方 SDK extra（版本锁死） | M0；M1 的 backend seam 不变 |
| D4 | M3a 子 Agent 采集范围 | 只 delegate/`/explore`；含 CLI `--explore`；都含 | delegate + `/explore`；CLI 可后补 | M3a 主体 |
| D5 | M3b TUI 范围 | B 最小（关联字段）；A 引入 OTel JS；不做 | B；A 留 M4 | M3a |
| D6 | 采样/量级 | 本地全采；collector tail sampling | 本地全采，M4 再定 | M0–M3 |
| D7 | eval/bench 是否导出 | 不导出；opt-in | M0–M3 不导出 | 全部 |
| D8 | 保留/告警 | 复用现有 webhook；后端保留 | 遥测保留交后端，告警用后端 | M0–M3 |
| D9 | 指标口径扩展 | M2 最小集；是否加 task/goal 预算 | M2 最小集，M3a 再扩 | M2 |

拍板前默认按“推荐 + 默认关闭”推进；任何涉及“把会话内容发往非本机地址”的决定必须单独确认。

## 15 实施顺序、风险与回滚

**顺序**：M0 → 更新本文状态并验收 → M1 → M2 → M3a →（用户决定 M3b）→ M4。每阶段先写测试，再实现，最后更新 HARNESS/USAGE/`.env.example`/open-decisions。若某阶段验收失败，停在该阶段，不回滚已完成的本地观测。

**风险**

| 风险 | 影响 | 缓解 |
|---|---|---|
| canonical projection 与本地落盘不一致 | 高 | M1 只喂已落盘事件；M0/M1 共用 `events_to_spans`；多 run/skip/轮转有测试 |
| 隐私泄露（路径/正文/错误信息） | 高 | L0 白名单 + 默认关闭 + 测试断言无内容 + 远程显式 allow |
| exporter 阻塞/拖慢 run | 中 | 批量异步 + 队列上限 + 超时 + fail-open |
| SDK 依赖升级/语义约定漂移 | 中 | 精确锁版本 + `vibe.*` 为规范源 + 映射版本 |
| 子 Agent 成本双计/丢计 | 中 | `source` label + 合成/实时去重测试 |
| 跨进程重复 span/悬空父引用 | 中 | 只由执行进程导出；无父上下文用 link/属性；attach 不导出 |
| 协议改动破坏旧客户端 | 中 | capability 门控 + 双端测试 + 保留 v1 |
| bench/eval 容器意外外发 | 中 | `run_oneshot` 硬关 + 清理 env；eval 不构造 sink |
| 指标 label 爆炸 | 中 | 固定枚举 + 工具名折叠 + 禁止 ID label |
| 日志/诊断污染 stdout | 高 | 只用 stderr；TUI 已有 diagnostic 通道；测试断言 stdout 帧合法 |

**回滚**：不设 `VIBE_OTEL` 即回到当前行为；卸载 extra 不影响本地证据；M0 脚本删除即无副作用；协议改动仅在 M3b 且必须可回退（capability 不声明即旧路径）。

## 附录 A：与现有观测面的关系

| 面 | 现状 | OTel 阶段后的关系 |
|---|---|---|
| `trace.jsonl` | 本地事实源（含轮转） | 不变；M0 读它，M1 从同一事件流派生 span |
| `--trace-summary` | [observability.py](../../app/session/observability.py) 的聚合 CLI | 不变；M0 复用其口径做 summary |
| `/evidence`、`rounds/` | 完成证据（[evidence.py](../../app/session/evidence.py)） | 不变；span 只带摘要/布尔，不复制 |
| `/cost`、`budget.jsonl` | 成本账本 | 不变；M2 只做指标视图 |
| `--bench-history` | [eval/history.py](../../app/eval/history.py) | 不变；M4 才评估 bench 导出 |
| 通知 webhook | 事件外发 | 不变；OTel 不做通知 |
| `worker.log`/stderr | 诊断 | 不变；M1–M4 不做 OTel Logs |
| eval/bench telemetry | 评测结果 | 不变；M4 才评估可选 OTel |

## 附录 B：参考

- [HARNESS 可观测性与成本](../HARNESS.md)
- [上下文分区观测调研](../research/context-partition-observability-2026-10-21.md)（OTel 边界、GenAI 语义约定 Development 状态、指标基数建议）
- [控制面 attach](control-plane-attach.md)、[后台进程监督](process-supervision.md)、[只读子 Agent](subagent-runner.md)（跨进程与关联面）
- [评测](evaluation.md)、[长任务评测](long-horizon-eval.md)（eval/bench 边界）
- 现行实现：[app/session/trace.py](../../app/session/trace.py)、[app/session/observability.py](../../app/session/observability.py)、[app/session/evidence.py](../../app/session/evidence.py)、[app/stdio.py](../../app/stdio.py)、[app/runtime/agent.py](../../app/runtime/agent.py)、[app/runtime/worker.py](../../app/runtime/worker.py)、[app/runtime/subagents.py](../../app/runtime/subagents.py)、[app/session/retention.py](../../app/session/retention.py)
