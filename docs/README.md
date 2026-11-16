# 文档导航

第一次使用从[项目首页](../README.md)和[使用指南](USAGE.md)开始，跟随[三分钟 Demo](DEMO.md)体验一次任务。参与开发见[贡献指南](../CONTRIBUTING.md)。

下列规格与验收记录保留各自编写时的阶段状态；当前进度以[里程碑](MILESTONES.md)为准，未来方向见[roadmap](roadmap.md)。历史测试数量与场景结果不是当前版本的整体能力承诺。

## 使用与配置

| 文档 | 内容 |
| --- | --- |
| [技能菜单](specs/skills-menu.md) | `/skills` 菜单、Tab 补全与按需加载/卸载的交互约定 |
| [使用指南](USAGE.md) | 安装、模型配置、功能区指引、命令、审批、预算、MCP、侧栏与评测 |
| [首次使用 UI / UX 方案](specs/first-run-onboarding-ui-ux.md) | 待审：真实启动、首次配置、首页、设置中心与 MCP 主动连接；尚未实施 |
| [体验验收](WORKBENCH-ACCEPTANCE.md) | 一条命令启动隔离 Demo，检查指引、回执、设置与 MCP 完整体验 |
| [配置指南](CONFIGURATION.md) | 首次必填项、/model 模型设置、高级选项与生效范围 |
| [开发配置](CONFIGURATION-DEV.md) | 演示、故障注入、调试与委派实验 |
| [配置契约](CONFIGURATION-CONTRACT.md) | 原模板 47 项的默认值、消费代码、支持状态与验证依据 |

## 原理与架构

| 文档 | 内容 |
| --- | --- |
| [长任务 / 后台 / 进阶模式讨论](design/long-horizon/README.md) | 长任务、后台任务与 `/plan` 等进阶模式的分块设计讨论（9 + 6 块）及子 Agent 结合方式 |
| [完成守卫与环境审计](specs/verified-completion.md) | 完成判定、文件变动账本、轮次记录与只读审计的行为/边界 |
| [任务运行时基础](specs/task-runtime.md) | TaskRecord/TaskStore 的数据模型、状态机、原子写、恢复与测试口径（P0 基础切片） |
| [任务运行时接入](specs/task-runtime-integration.md) | stdio `run.task`、`task_state` 事件、TUI `/goal` 接入、capability 门控与兼容回退 |
| [后台进程监督](specs/process-supervision.md) | worker 状态/心跳/判死、`--tasks`、`--worker/--attach`、TUI 退出转入后台、启动任务提示/`/task`（P0a/P0b/P0c 基础已实现） |
| [GoalRecord](specs/goal-record.md) | 跨段目标 schema v2、goal.json 迁移、段账本同步、worker 续段提示/决策（P0/P1a 已实现） |
| [预算账本](specs/budget-ledger.md) | task/goal/segment 的 append-only budget.jsonl、trace 接线、终态结算与 check_start 硬上限（P0/P1 部分已实现） |
| [审批收件箱](specs/approval-inbox.md) | approvals.jsonl、一次性审批/过期/归属/脱敏、worker 接线与 approval_timeout、`/approvals` 列表（P0/P1 部分已实现；TUI 决定 UI 后续） |
| [工作区隔离](specs/workspace-isolation.md) | workspace 单写者锁、git worktree、worker 接线、MergeGate（审批/漂移/脏工作区/合并后验证）、CLI/TUI stop/merge、非 git 默认拒绝 |
| [控制面 attach](specs/control-plane-attach.md) | TUI attach、controller/observer、心跳租约、capability、replay/live 锁、attach v2 帧、`/attach`/`/attach now`（P0 已实现；完整 v2 双栈后续） |
| [无人值守 profile P0](specs/unattended-profile.md) | 后台保守 profile：只读/worktree 写、exec/网络/MCP/remote 默认拒绝、auto-deny 审计 |
| [任务模式 P0](specs/modes.md) | chat/act 写入 TaskRecord.mode；chat 不发送 tools；TUI `/chat` `/act` |
| [结构化 Plan P0](specs/plan-artifact.md) | Plan schema 校验、原子版本存储、plan_hash、终端渲染 |
| [计划审批门 P0](specs/plan-approval.md) | plan propose/approve/reject、content hash、plan.review capability、审计事件 |
| [计划执行绑定 P0](specs/plan-execution.md) | step ledger、状态机、证据推进、原子 execution 文件 |
| [OTel 集中观测规格](specs/otel-observability.md) | M0–M3 分阶段（离线导出、进程内 span/metrics、worker/subagent 关联）与 M4+ 生产化；默认关闭、隐私分级、待拍板项（未实现） |
| [只读子 Agent P0](specs/subagent-runner.md) | harness 内部只读 runner、结构化结果、无审批/写/执行 |
| [单 Agent loop 清理](specs/single-loop-cleanup.md) | TUI/eval 统一走 `Agent.run`，删除未接入入口的同步教学循环 |
| [预算收敛与暂停（历史）](specs/token-budget-stop.md) | 初版 80% 收尾暂停策略与验证；现行策略见自动压缩继续 |
| [预算撞车排查](research/budget-pause-collision-2026-10-25.md) | 任务预算与上下文预算「撞车」的成因与默认值分析 |
| [Harness 技术方案](HARNESS.md) | harness 的分层、主循环、上下文、审批边界、状态与恢复、评测与取舍的汇总设计 |
| [真实模型 Plan P1 冒烟](specs/real-p1-plan-smoke-2026-11-07.md) | 2026-11-07 deepseek-flash/chat：计划生成、多步执行、审批帧、验收 acceptance=true；重复样本与剩余 TUI 人工验收记录 |
| [规格](specs/) | 每个功能切片的 spec、边界与验收口径 |

## 开发方向与规划

| 文档 | 内容 |
| --- | --- |
| [建设方向](roadmap.md) | 长期方向、阶段取舍与界面 / 全栈学习路径 |
| [体验简化方案](../openspec/roadmaps/simplify-workbench-experience/roadmap.md) | 回执分层、配置精简、功能指引和应用内模型设置已实现；任务限制与高级设置为后续阶段 |
| [文件规划](STRUCTURE.md) | 目录职责、依赖方向与新增文件约定 |
| [关于你研究笔记](research/about-you-context-control-2026-10-20.md) | 个人上下文可见性与用户控制的产品方向讨论 |
| [Agent 实验台规划](../openspec/roadmaps/build-agent-lab/roadmap.md) | 观测、运行版本、SWE-bench / Terminal-Bench 主基准及回归闭环；含设计、验收与开发规范（规划，未实施） |
| [待用户拍板事项](specs/open-decisions.md) | 模型自发委派、文件集并行写、真实 TUI 人工验收、跨机器观测：现状/选项/推荐 |
| [模型配置位置调查](research/coding-agent-config-locations.md) | OpenCode、Claude Code、Codex 的用户配置、项目覆盖与凭据存储对比 |
| [调研](research/) | TUI、评测、沙箱、上下文等选型记录 |

## 验证与历史记录

| 文档 | 内容 |
| --- | --- |
| [工作台里程碑](MILESTONES.md) | W0–W7 的验收条件、真实运行证据与当前状态 |
| [全链路验收收束](specs/acceptance-report.md) | 2026-11-05 运行时/worker/GoalRecord/预算/审批/隔离/attach 的验证结果、限制与复现命令 |
| [07 attach 人工验收](specs/manual-acceptance-07-attach.md) | 真实 pty 下的 TUI attach/v2 帧/审批/observer/接管/stop/merge 步骤与测试用例 |
| [07 attach 人工验收记录](specs/manual-acceptance-07-results.md) | 2026-11-06 人工验收：TC-01–09 全部通过、修复项与最终结论 |
| [长任务评测 P0](specs/long-horizon-eval.md) | `bench_long.py` fake worker 场景：detach/审批超时/kill/attach/崩溃/两槽/criteria/多段，报告与复跑 |
| [计划验收映射 P0](specs/plan-acceptance.md) | criteria→verification plan、三态结论与失败原因码 |
| [P0 收束报告](specs/p0-closure-report.md) | 16 块 P0 状态、当前测试基线、19 个长任务场景（5 个真实适配、双模型 5/5）、已知限制与 100 轮加固摘要 |
| [交接摘要（2026-11-07）](specs/final-handoff-2026-11-07.md) | 一页看清：完成项、复验命令、剩余用户决策与回来继续的步骤 |
| [委派 A/B 实验](specs/delegation-ab.md) | opt-in 只读 delegate 与单 Agent 的多文件调研对比：指标、结果与默认关闭结论 |
| [Plan/SubAgent 人工验收](specs/manual-acceptance-16-plan-subagent.md) | TC-16-01–08：计划审批/执行/验收、stale/修订、/explore、CLI 只读视图、侧栏摘要 |
| [Plan/SubAgent 验收结果](specs/manual-acceptance-16-results.md) | 2026-11-07：真实模型 Plan/SubAgent 自动化结果、TC-16 映射、剩余 TUI 键路 |
| [TUI 终端快速验收](specs/manual-acceptance-tui-quick.md) | 真实终端 10 分钟走完启动/审批/Plan 覆盖层/explore/后台/队列/attach/侧栏的可见效果与手感 |
| [真实 P1 验收脚本](../scripts/real_p1_acceptance.py) | 一键真实模型 Plan 生成→批准→执行（前台/`--background`）→审批→验收 + `/explore`，输出 JSON 报告 |
| [Benchmark 适配（Harbor）](../bench/README.md) | SWE-bench Verified / Terminal-Bench 2.1 的 Harbor 接入、单题 smoke 与限制 |
| [Coding Agent 评测集调研](research/coding-agent-benchmarks-2026-10-21.md) | SWE-bench 等公开评测集的核实与接入取舍 |
