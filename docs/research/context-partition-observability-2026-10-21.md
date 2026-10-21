# 上下文分区占用统计与可视化调研

日期：2026-10-21。范围：当前上下文占用、分区快照、请求用量与成熟可观测性工具。依据官方文档与官方源码，不含产品实测或接入验收。本文建议均为后续设计，不表示项目已经实现 `context_snapshot`、分区历史图或第三方导出；已有能力与工程缺口以仓库实现为准。

## 结论

本项目优先在本地生成结构化的分区快照，以 TUI 展示当前占用和变化历史，再按需要外接可观测性后端。Claude Code 的 `/context` 是合适的交互参考；Langfuse、Phoenix 和 LangSmith 更擅长模型请求、工具执行、token 消耗、费用和质量的关联分析。它们不会从现有请求的总 token 数自动还原 mini-harness 自定义的语义分区，分区边界与数值必须由 harness 提供。这一选择结合项目当前“单 Agent 可靠且可理解”的方向作出，参见 [roadmap](../roadmap.md) 与 [W1–W3 里程碑](../MILESTONES.md)。

必须分开保存三种含义：当前发送内容的分区占用快照、每次模型响应返回的实际 usage、整项任务累计消耗。压缩后当前占用可以下降，累计消耗继续增长；对快照重复求和得到的是重复读取量，不能称为当前占用。缓存命中也不等于腾出窗口空间。Anthropic 官方明确说明，缓存前缀仍占上下文窗口，缓存改变计费方式。[上下文窗口](https://platform.claude.com/docs/en/build-with-claude/context-windows)

## 当前代码核查

- 分区是预算分类，底层主要仍是 `list[Message]` 和单独的工具定义。默认窗口 1,000,000，名义额度为 system 8,000、memory 64,000、tools 16,000、history 872,000，另有 output 32,000 与 scratch 8,000 预留。history 按其他输入区实际占用借用剩余额度；配置可覆盖默认值。[预算实现](../../core/budget.py)
- system 包含基础指令、项目 AGENTS、skills 描述及动态 task_state；tools 统计工具定义，工具调用参数与返回内容属于 history。Agent 调用 builder 时 memory 用量为 0，长期记忆尚未接入。[请求构建](../../app/agent.py)
- 普通 `ContextResult.sections` 已有四区计数，`caps` 有名义及有效额度。Ink 后端把 context 的总量与 sections 写入会话事件，但前端 metrics 只有总量；Textual 主要保留当前结果和总量/history 显示。[ContextResult](../../core/context.py)、[stdio](../../app/stdio.py)、[Textual](../../app/tui.py)、[Ink 协议](../../tui/src/protocol.ts)
- `compact_history` 的 sections 与普通 build 不同：包含 history_before/history_after 等重叠值，以及 recent_groups/summary_layers 等非 token 指标；其 tokens 也未包含工具定义。不能直接把全部 sections 值绘为堆叠图。[压缩结果](../../core/context.py)
- 现有 on_context 回调之后仍可能追加纠偏指令、切换到无工具的收尾请求。应在最终调用 provider 前记录统一快照，并保留请求、重试和阶段关联，避免把中间构建结果称为实际发送内容。[Agent loop](../../app/agent.py)
- 当前分区计数是中文字符数加其他字符数约四分之一的估算，不包含完整协议开销。第三方返回的实际总 usage 不提供这些业务分区的精确归因。[估算函数](../../core/context.py)

## 成熟方案比较

| 方案 | 官方现有能力 | 对本项目的用途与边界 |
|---|---|---|
| Claude Code `/context [all]` | 彩色网格展示当前上下文，提供逐项展开与容量提示 | 借鉴分区颜色、总量、剩余量和详情；它是 Claude Code 自身的交互功能，不是可直接复用的 mini-harness 统计服务 |
| Langfuse | 每次 generation/embedding 的 usage 与费用、trace/session、元数据筛选、Metrics API 聚合 | 适合定位哪个步骤让上下文膨胀和分析累计成本；自定义分区快照需自行采集，元数据不等于任意数值时间序列 |
| Phoenix / OpenInference | trace/span、prompt/completion/total token 属性、成本计算、自定义属性和元数据 | 适合保留一次输入与工具链的可解释记录，关联分区快照；需要自行定义分区与绘图逻辑 |
| LangSmith | 请求与 trace 监控、token 时间图、按 tag/metadata 分组的自定义 dashboard | 已采用相关生态时方便统一观测；自定义分区数值仍需专门建模，分组支持不表示能直接对任意元数据数值作图 |
| OpenTelemetry + Prometheus + Grafana | 标准化遥测、可升降的 gauge、时序图与告警 | 自定义分区曲线最灵活；需要部署与维护，当前单机阶段可以先保留导出边界 |

对应来源：[Claude Code commands](https://code.claude.com/docs/en/commands)、[Langfuse usage/费用](https://langfuse.com/docs/observability/features/token-and-cost-tracking)、[Metrics API](https://langfuse.com/docs/metrics/features/metrics-api)、[Phoenix 成本与 token 属性](https://arize.com/docs/phoenix/tracing/how-to-tracing/cost-tracking)、[自定义元数据](https://arize.com/docs/phoenix/tracing/how-to-tracing/add-metadata/customize-spans)、[LangSmith dashboard](https://docs.langchain.com/langsmith/dashboards)、[Prometheus metric types](https://prometheus.io/docs/concepts/metric_types/)、[Grafana time series](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/time-series/)。

Langfuse 当前元数据文档明确说明值按字符串存储，暂不支持数值或布尔比较。因此，携带 `partition_tokens` 元数据可以用于关联与检查，但不能据此宣称内建 dashboard 能直接绘制全部任意分区数值。需要核对具体版本的查询、score 或导出能力。[Langfuse metadata](https://langfuse.com/docs/observability/features/metadata)

Phoenix 的成本文档把项目成本趋势和最贵模型标作 coming-soon；本次调研不把它们计入已交付能力。LangSmith 新旧自定义 dashboard 的可用性也取决于 Cloud 区域和自托管数据后端。选型时应核对实际安装版本。[Phoenix 成本视图](https://arize.com/docs/phoenix/tracing/how-to-tracing/cost-tracking)、[LangSmith 平台差异](https://docs.langchain.com/langsmith/dashboards)

## 建议的数据模型与呈现

以下是本项目的设计建议，不是第三方工具的预定义协议。

每次组装完成且即将调用模型时，生成一个 `context_snapshot`，记录 `session_id`、`turn_id`、`request_id`、时间、模型、触发原因、`input_tokens`、计数方法，以及各分区的 `id`、token 数、字符数、条目数、内容摘要或稳定引用。应在压缩前后各记录一次，保留同一关联标识，以便解释释放了哪些内容；工具结果实际进入下一次输入时，再出下一张快照。流式输出阶段如显示估算，应明确与已发送输入区分。

先做两个视图：当前快照用横向堆叠条和分区明细表；历史用按请求或事件顺序排列的堆叠面积/柱图，标出压缩事件。点击分区可下钻到条目，查看新增、删除或压缩的来源。没有实际会话数据时，只展示明确标注的演示数据。

总量应覆盖实际请求结构，包括 system、messages 和工具定义；分区标识必须在组装时保留，不能等内容拼成一段字符串后再猜来源。语义上的 summary、task state 与对话消息在最终请求里可能都表现为 system 或 user 内容，因此 API role 不足以替代语义分区。

## token 计数口径

建议实现可替换的计数器：本地估算用于即时刷新；模型匹配的 tokenizer 或供应商 count API 用于发送前校验；响应 usage 保存为实际调用证据。每个数值标明 `estimated`、`tokenizer`、`provider_count` 或 `provider_usage`，并保存模型与计数器版本。字符数/固定比例只能显示为估算。

Anthropic 的 count API 接受结构化 system、messages、工具定义、图片与 PDF，返回请求输入总数；支持输入存在例外，文档将其称为估计，某些服务器工具/MCP 输入需要依赖实际响应 usage。它不是“返回每个业务分区精确 token”的接口。[官方 token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)

分区独立计数之和与完整请求计数可能受消息封装、工具 schema 和分界影响。建议把未分配差额作为 `overhead/unattributed`，保留对账信息；或使用固定顺序的增量差分归因，并说明这种归因依赖顺序。这是实现建议，不把某种归因结果包装成供应商提供的精确分区账单。

## 未来接 OpenTelemetry 的边界

当前分区占用会随压缩下降，适合自定义 gauge，例如 `mini_harness_context_partition_tokens{partition="recent"}`；请求实际消耗按累计或请求分布指标另存。该名字是本项目建议，不是 GenAI 标准。Prometheus 官方将 gauge 定义为可任意升降的值。[metric types](https://prometheus.io/docs/concepts/metric_types/)

固定 `partition`、模型等低基数维度即可；逐条消息 ID、文件路径、session/request ID 和完整内容放事件/trace。不要无限扩张指标 label。Prometheus 官方明确不建议把无界 ID 作为标签。[标签命名与基数](https://prometheus.io/docs/practices/naming/)

截至本次读取，OpenTelemetry GenAI 约定已移到单独仓库，且仍标为 Development。当前源码使用 `gen_ai.client.inference.usage.*` counter 表示累计消耗，`gen_ai.client.inference.operation.*` histogram 表示单次分布；不能沿用旧文章中的名称作为永久接口。接入时锁定 SDK 与语义约定版本，再做兼容映射。[官方迁移页](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/)、[当前 token metrics 源码](https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-token-metrics.md)

本次仅新增调研记录；没有安装服务、创建云端账号、上传会话内容、修改执行逻辑或提升里程碑验收状态。后续实现需要先定义可验证行为，再按项目规则增加测试，并让运行时文件与命令操作经过 `ApprovalGate`。
