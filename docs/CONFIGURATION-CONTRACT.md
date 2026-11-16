# 配置契约：原环境模板盘点

核对日期：2026-11-13。范围为本次精简前 .env.example 的 **47 个具名变量**（包含两个 Key 别名）；这不是全部内部环境开关的清单。用户入口见[配置指南](CONFIGURATION.md)，故障注入和实验参数见[开发配置](CONFIGURATION-DEV.md)。

表内“启动”指新进程初始化；已有后台 worker 保留创建时继承的环境。每次调用读取的是**进程环境**，不会自动重新加载磁盘 .env。更改文件后重启相关进程；现有会话/goal 可能还保留已创建时的预算快照。

默认值适用于标准交互入口。eval/bench 会为可比性覆盖部分预算/审计参数，不能用评测默认值推断日常运行。布尔项除特别说明外使用 1/true/yes（大小写转换后）表示开启；未列出的值不建议使用，部分数值解析对非法文本会直接报错。

## 应用内模型入口补充

2026-11-16：公共入口统一由 user_config.py 解析用户默认、项目覆盖与来源，旧安装 .env 为最低优先级兼容项；凭据与 endpoint 绑定，详见[配置指南](CONFIGURATION.md)。

阶段 4 增加 /model：模型、地址、Key 和代理可在普通 OpenAI 兼容连接空闲时显式应用到后续新任务；环境变量本身仍按下表在启动时解析。保存使用用户级 config.toml 与独立 credentials.json（显式 VIBE_CONFIG_FILE 可兼容旧格式），不改已有后台进程，也不让高级入口隐式加载文件。

共享读写见 app/runtime/model_config.py（scripts/launcher_config.py 保留兼容导出）；来源、审批保存、最小检测和恢复核对见 app/runtime/model_settings.py，接线在 app/stdio.py。专项证据为 tests/test_model_settings.py 与 Ink 模型面板测试，完整边界见[使用说明](USAGE.md#应用内模型设置)。

## 应用内目标预算补充

阶段 5.B 增加 /settings 的目标分组（g 切换）。app/runtime/goal_settings.py 管理新目标默认、来源与审批保存；goal_limits.py 管理数值契约和续段门。TaskRecord 扩展字段 goal_limits 为不可变预算，created_at 为起点，BudgetLedger 为累计用量。task_state 增加 goal_budget 投影；前台运行、恢复和后台继续共用后端检查，客户端不能经 run 参数放宽预算。已有进程的默认值不跨进程热同步，但交接目标始终使用原任务快照。

## 应用内单段限制补充

/settings 为四项启动限制增加当前前台后端的“后续新任务默认”应用入口：VIBE_MAX_STEPS、VIBE_MAX_RUN_SECONDS、VIBE_MAX_RUN_TOKENS、VIBE_MAX_RUN_COST_USD。保存只更新这四项，不应用；显式应用仍遵守 CLI/进程环境覆盖。下表启动解析和其他进程的消费时点保持原义。

实现为 app/runtime/limit_settings.py；stdio 每段运行临时使用所选限制，在 trace 的 run_limits 中记录任务首次执行快照，续做优先使用原快照。启动器只传递环境来源的变量名，不传递额外值。测试见 tests/test_limit_settings.py、tui/tests/settings*.test.tsx；[完整边界](USAGE.md#应用内任务限制与设置中心)包含旧记录兼容、费用估算与独立预算。

## 逐项去向

“高级配置”与“开发配置”均表示变量仍有效，仅移出首次模板；唯一标记“不支持”的输出上限变量从未在标准环境接线中生效。保护机制与实验开关的默认值未改动。

| 变量 | 默认值 / 解析边界 | 解析 → 消费位置 | 作用域 / 生效时点 | 状态 / 去向 | 验证依据 |
| --- | --- | --- | --- | --- | --- |
| `VIBE_API_KEY` | 空；真实模型必需 | C.api_key → P Authorization | 进程 / 启动 | 最小模板 | Tstart |
| `OPENAI_API_KEY` | 空；第二顺位别名 | C.api_key → P Authorization | 进程 / 启动 | 兼容保留；用户指南 | Tstart |
| `DEEPSEEK_API_KEY` | 空；第三顺位别名 | C.api_key → P Authorization | 进程 / 启动 | 兼容保留；用户指南 | Tstart |
| `VIBE_BASE_URL` | https://api.deepseek.com/v1 | C.base_url → P 请求 URL | 进程 / 启动 | 最小模板可选 | Tstart |
| `VIBE_MODEL` | deepseek-chat | C.model → P 请求 model | 进程 / 启动 | 最小模板可选 | Tstart |
| `VIBE_PROVIDER` | openai；fake 为本地响应 | C.provider → E.build_provider | 进程 / 启动 | 开发配置；推荐 --demo | Tprovider |
| `VIBE_FAKE_DEMO` | 未设置；1/plan/revision/revision-resume | E.build_provider → FakeProvider；tui/src/app.tsx 预填 | fake 进程 / 启动 | 开发配置 | Tstdio |
| `VIBE_FAKE_STREAM_ERRORS` | 0；整数限制 0–5 | E._fake_stream_faults → FakeProvider | fake 进程 / 启动 | 开发配置 | Tprovider |
| `VIBE_AUTONOMY` | 空；用户策略指定，否则 ask | C.autonomy → E._run → Agent/ApprovalGate | 进程 / 启动；/autonomy 可改当前档位 | 高级配置 | Tpermission |
| `VIBE_PERMISSIONS` | ~/.vibe/permissions.json；空串禁用用户文件 | A._permissions_paths → _reload_permission_policy | 工作区与用户策略 / 每次 run；文件内容可重读 | 高级配置 | Tpermission |
| `VIBE_COMPLETION_GUARD` | true | C.completion_guard → E._run → Agent 完成判断 | Agent / 启动 | 高级配置；保持默认保护 | Tconfig |
| `VIBE_WORKSPACE_AUDIT` | true | C.workspace_audit → E._run → Agent 工作区审计 | Agent / 启动 | 高级配置；保持默认保护 | Tconfig |
| `VIBE_LLM_AUDITOR` | false | C.llm_auditor → E._run → 只读审计 Agent | Agent / 启动；额外模型调用 | 高级配置；默认关闭 | Tconfig |
| `VIBE_CONTRACT_BACKCHECK` | true；仅 LLM 审计启用时有作用 | C.contract_backcheck → E._run → auditor.require_contract_aligned | Agent / 启动 | 高级配置 | Tconfig |
| `VIBE_SANDBOX` | false | C.sandbox → E._run → ToolRegistry → Seatbelt | 工具进程 / 启动 | 高级配置；macOS | Tsandbox |
| `VIBE_SANDBOX_NETWORK` | false | C.sandbox_network → ToolRegistry → Seatbelt | 沙箱 / 启动；sandbox 开启才有意义 | 高级配置 | Tsandbox |
| `VIBE_SANDBOX_NETWORK_ALLOW` | 空；逗号分隔域名，支持通配 | C.sandbox_network_allow → ToolRegistry → SandboxProxy | 沙箱 / 启动；放行全部网络时不限制域名 | 高级配置 | Tproxy |
| `VIBE_CONTEXT_WINDOW` | 1000000；后备别名 VIBE_MAX_TOKENS | C.context_window/budget_policy → core/budget/policy.py → ContextBuilder | Agent / 启动 | 高级配置 | Tconfig |
| `VIBE_MAX_STEPS` | 0：不限轮数 | C.max_steps → E._run → Agent 循环 | Agent / 启动；--max-steps 可覆盖 | 高级配置 | Tconfig |
| `VIBE_MAX_RUN_SECONDS` | 1800 秒 | C.max_run_seconds → E._run → Agent 时限 | 单 run / 启动快照；排除审批等待 | 高级配置 | Tagent |
| `VIBE_LLM_MAX_OUTPUT_TOKENS` | 无有效环境默认；Config 字段为 0 | Config.from_env 未解析；E.build_provider 未转发该字段 | 不生效 | 不支持；移除示例并纠正文档 | Tstart + TproviderLow |
| `VIBE_LLM_DEBUG_PATH` | 空：不写诊断日志 | E.build_provider → P._debug | provider / 启动 | 开发配置 | Thttp |
| `VIBE_TRUST_ENV` | false | C.trust_env → E.build_provider → P 的 httpx client | provider / 启动；读取 shell 代理环境 | 最小模板可选 | Tstart |
| `VIBE_MCP_CONFIG` | 空：无配置文件 | C.mcp_config → E._run → McpManager.from_env | 外部工具 / 启动 | 高级配置 | Tmcp |
| `VIBE_MCP_SERVERS` | 空：无内联定义 | C.mcp_servers → E._run → McpManager.from_env | 外部工具 / 启动 | 高级配置 | Tmcp |
| `VIBE_ACCEPTANCE_REPAIR_ATTEMPTS` | 0；限制 0–3 | S._acceptance_repair_limit → 计划修复循环 | 计划执行 / 修复循环读取进程环境 | 高级配置 | Tplan |
| `VIBE_ACCEPTANCE_RETRIES` | 0；限制 0–3 | S._run_trusted_acceptance → run_verification_plan | 计划验收 / 每次验收 | 高级配置 | Tverification |
| `VIBE_ACCEPTANCE_TIMEOUT_SECONDS` | 300；限制 1–3600 秒 | S._run_trusted_acceptance → run_verification_plan | 计划验收命令 / 每次验收 | 高级配置 | Tverification |
| `VIBE_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS` | 0：按单次超时×(重试+1)+30 计算；最终限制 1–7200 秒 | S._run_trusted_acceptance → asyncio.wait_for | 整批计划验收 / 每次验收 | 高级配置 | Tplan |
| `VIBE_UNATTENDED_REQUIRE_APPROVAL` | false | app/safety/unattended.py profile_approval_required → worker 放宽策略检查 | worker / 启动前 | 高级配置 | Tworker |
| `VIBE_NOTIFY_WEBHOOK` | 空：不外发；http/https URL | N.webhook_url_for → post_webhook | 通知 / 每次派发读取进程环境 | 高级配置 | Tnotify |
| `VIBE_NOTIFY_WEBHOOK_KINDS` | 空或 *：全部；逗号分隔筛选 | N.webhook_url_for → _dispatch_webhook | 通知 / 每次派发 | 高级配置 | Tnotify |
| `VIBE_NOTIFY_WEBHOOK_TIMEOUT_SECONDS` | 3；限制 0.1–30 秒 | N._webhook_timeout → httpx.post | 通知 / 每次发送 | 高级配置 | Tnotify |
| `VIBE_EVENTS_MAX_BYTES` | 33554432；0 关闭告警 | R.events_soft_limit；W._check_session_events_size → 提示/通知 | 会话日志 / 检查时读取进程环境；不轮转 events | 高级配置 | Tretention + Tworker |
| `VIBE_GOAL_MAX_SEGMENTS` | 8；正整数，至少 1 | goal_limits/goal_settings → TaskRuntime 快照与续段门；worker 复用快照 | 新目标创建时固定；前后台每段开始前按累计账本核对 | /settings 目标分组；保存与应用分离 | Tgoal + Tworker + tests/test_goal_settings.py |
| `VIBE_GOAL_SECONDS` | 14400；至少 60 秒 | goal_limits/goal_settings → TaskRuntime 快照与续段门；worker 复用快照 | 新目标创建时固定；前后台每段开始前按累计账本核对 | /settings 目标分组；保存与应用分离 | Tgoal + Tworker + tests/test_goal_settings.py |
| `VIBE_GOAL_MAX_TOKENS` | 0：不限；非零正整数 | goal_limits/goal_settings → TaskRuntime 快照与续段门；worker 复用快照 | 新目标创建时固定；前后台每段开始前按累计账本核对 | /settings 目标分组；保存与应用分离 | Tgoal + Tworker + tests/test_goal_settings.py |
| `VIBE_GOAL_MAX_COST_USD` | 0：不限；非零正数 | goal_limits/goal_settings → TaskRuntime 快照与续段门；worker 复用快照 | 新目标创建时固定；前后台每段开始前按累计账本核对 | /settings 目标分组；保存与应用分离 | Tgoal + Tworker + tests/test_goal_settings.py |
| `VIBE_SUBAGENT_DELEGATE` | false | S._delegate_enabled → register_delegate_tool | stdio 工具注册 / 初始化；需启用工具 | 开发配置；实验 opt-in | Tdelegate |
| `VIBE_DELEGATE_MAX_STEPS` | 6；限制 1–15 | S.register_delegate_tool → run_subagent | 只读 delegate / 每次调用 | 开发配置 | Tdelegate |
| `VIBE_DELEGATE_MAX_TOKENS` | 20000；限制 500–200000，再取父任务配置上限 | S.register_delegate_tool → run_subagent | 只读 delegate / 每次调用 | 开发配置 | Tdelegate |
| `VIBE_DELEGATE_MAX_SECONDS` | 60；限制 5–300 秒 | S.register_delegate_tool → run_subagent | 只读 delegate / 每次调用 | 开发配置 | Tdelegate |
| `VIBE_EXPLORE_MAX_STEPS` | 6；限制 1–20 | S 与 E 的 explore 分支 _env_bounded → run_subagent | 只读 explore / 每次调用 | 高级配置 | Texplore |
| `VIBE_EXPLORE_MAX_TOKENS` | 20000；限制 500–200000 | S 与 E 的 explore 分支 _env_bounded → run_subagent | 只读 explore / 每次调用 | 高级配置 | Texplore |
| `VIBE_EXPLORE_MAX_SECONDS` | 60；限制 5–600 秒 | S 与 E 的 explore 分支 _env_bounded → run_subagent | 只读 explore / 每次调用 | 高级配置 | Texplore |
| `VIBE_BENCH_AGENT_VERSION` | 未设置：git short HEAD，失败 unknown | bench_long/real_p1_acceptance/oneshot 的 agent_version → 报告 | 评测进程 / 生成版本记录时 | 开发配置 | Tbench |
| `VIBE_FAKE_TIME_OFFSET_SECONDS` | 0；限制 0–31536000 秒 | W._clock_offset_seconds → goal should_continue 的 now | 后台 goal / 续段判断 | 开发配置 | Tworker |

## 源码与验证索引

位置缩写只为保持表格可读；可以从下面链接追到解析和消费代码。测试列是相关回归依据，**并非声称每个边界都有独立测试**；未由断言直接覆盖的默认/钳制/生效时点由本次源码核对确认。Tstart 新增从模板 → Config → provider 实际请求的本地 MockTransport 测试（默认/自定义服务），不访问真实 API。输出上限的环境缺口由该请求测试和 Config/入口代码共同核验；底层 provider 参数测试不能替代入口验证。

- [C](../app/config.py)
- [E](../app/__main__.py)
- [A](../app/runtime/agent.py)
- [P](../app/providers/openai_compat.py)
- [S](../app/stdio.py)
- [W](../app/runtime/worker.py)
- [G](../tui/src/goal.ts)
- [N](../app/runtime/notifications.py)
- [R](../app/session/retention.py)
- [Tstart](../tests/test_start.py)
- [Tprovider](../tests/test_cli_provider.py)
- [Tconfig](../tests/test_config.py)
- [Tstdio](../tests/test_stdio.py)
- [Tpermission](../tests/test_permission_policy.py)
- [Tsandbox](../tests/test_sandbox.py)
- [Tproxy](../tests/test_sandbox_proxy.py)
- [Tagent](../tests/test_agent.py)
- [TproviderLow](../tests/test_provider.py)
- [Thttp](../tests/test_provider_http.py)
- [Tmcp](../tests/test_mcp.py)
- [Tplan](../tests/test_stdio_tasks.py)
- [Tverification](../tests/test_verification.py)
- [Tworker](../tests/test_worker.py)
- [Tnotify](../tests/test_notifications.py)
- [Tretention](../tests/test_retention.py)
- [Tgoal](../tui/tests/goal.test.ts)
- [Tdelegate](../tests/test_delegate_tool.py)
- [Texplore](../tests/test_cli_explore.py)
- [Tbench](../tests/test_bench_long.py)

## 有意保留的差异与后续

- goal 新任务由后端统一固定预算：段数至少 1，时长至少 60 秒，token/费用的 0 表示不限；总时长按墙钟计入等待/暂停。每段开始前检查，已开始的段仍遵守单段限制。旧 GoalRecord 零值可能表示未知，不据此取消启动保护；匹配的旧 goal.json 明确值可迁移。
- VIBE_PERMISSIONS 空值只禁用用户文件；项目策略仍加载且只能收紧。默认档位可能受已有用户策略影响，不应宣称所有安装始终 ask。
- 模型代理开关仅控制模型 HTTP client；MCP、通知或其他工具各有自己的网络行为。HTTPS_PROXY 不由启动器从 .env 加载。
- 后续应用内设置应先解决进程、任务快照与持久化归属，再提供编辑界面；当前无热更新承诺。
