# 待用户拍板事项（2026-11-07 汇总）

长期自动开发已把 01–16 的 P0 与可落地的 P1 加固做完并验收（见 [P0 收束报告](p0-closure-report.md)、
[真实 P1 记录](real-p1-plan-smoke-2026-11-07.md)）。以下 5 项需要你决定后才能继续，而不是实现能力问题。

## 1. 模型自发委派（P2，read-only `delegate`）

**现状**：只有用户/命令触发的 `/explore` 只读子 Agent；设计文档
[16](../design/long-horizon/16-subagent-integration.md) 把「模型自行调用 delegate」列为 P2，需先有
08 的 A/B 指标与 10/11 模式委派策略。

**选项**：
- A. 保持不开放（默认，当前）。主 loop 仍是单 Agent，成本与可预期性最好；
- B. **推荐**：开放只读 `delegate` 工具（opt-in `VIBE_SUBAGENT_DELEGATE=1`）：
  - 仅 read-only 子集、不嵌套（depth=1）、受 parent 预算硬上限约束；
  - 工具结果标 provenance，子报告视为 untrusted claim；
  - 配套 A/B：单 Agent vs 委派 在长任务/大仓库问题上的 token、时延、正确率。
- C. 开放写子 Agent：需要 worktree + 审批归属 + merge 门禁完整串联，风险最高，不建议先做。

**需要你确认**：是否选 B、允许的模式（plan 只读探索 / act / goal）与成本上限。

## 2. 文件集级并行写（04 实验项）

**现状**：写任务按 workspace 锁串行；只读任务可并发；plan edit 步骤已有文件范围门禁。
**选项**：
- A. **推荐**：保持串行（可预期、不会产生跨任务写冲突）；
- B. 文件集声明 + 文件级锁，允许不相交文件集并行写（需要冲突检测、死锁规避、合并顺序规则）。

**需要你确认**：是否有“多个写任务同时跑”的真实需求；若有，从哪些任务类型先试点。

## 3. 真实 TUI 终端人工验收（环境限制项）

自动化已覆盖 9 个真实后端跨层用例，但 Ink 渲染/键盘手感只能在你本地终端验证。
**下一步**：在支持 pty 的终端运行 [TUI 终端快速验收清单](manual-acceptance-tui-quick.md)（约 10 分钟），
把结果记录到该文档的表格即可。重点：Plan 覆盖层按键（`s/t/v/d/a/e/b/y/n/Enter`）、审批面板、拖选复制、侧栏。

## 4. 真实模型长任务评测预算

**现状**：长任务 benchmark 16 场景中 14 个 fake-only（生命周期/故障注入，不消耗 token）；2 个已真实适配（`real-read-summary` 双模式、`real-task-deadline` real-only），2026-11-07 双模型 `--real` 各 2/2 PASS；
真实模型证据目前来自 `--suite`（六模式，含 plan/goal/repair/merge）与 `--suite-runs 2` 稳定性样本。
`bench_long.py --real` 可让 14 个场景改用真实 provider，但会消耗 token 且部分场景预期依赖 fake 行为，需要按场景挑选或改造。

**选项**：
- A. **推荐（已落地 2 个）**：场景 modes 机制 + read/deadline 已真实验证；是否再投入预算改造更多生命周期场景（如 `kill-switch`、`restart-attach`）；
- B. 保持现状（fake 生命周期 + suite 六模式），等真实使用反馈再扩；
- C. 全量 `--real` 跑 14 场景（成本高、需要先调场景预期）。

**需要你确认**：是否投入预算做 A、可接受的一次性 token 上限（例如 300k/500k）。

## 5. 跨机器/集中观测

**现状**：本地已有 `--evidence`、`--trace-summary`（可跨会话）、`--bench-history`、通知 webhook。
**选项**：
- A. 保持本地（当前），外发靠 webhook；
- B. 增加 OTLP/Prometheus 导出或集中日志汇聚（需要部署环境与目标系统信息）。

**需要你确认**：是否有实际的多机/集中观测需求与目标（OTLP endpoint / Prometheus / 日志平台）。

## 汇总

| # | 事项 | 当前默认 | 需要你决定 |
|---|---|---|---|
| 1 | 模型自发委派 | 不开放 | 是否 opt-in 只读 delegate（推荐 B） |
| 2 | 文件集级并行写 | 串行 | 是否有真实需求 |
| 3 | 真实 TUI 人工验收 | 待终端 | 按清单执行并记录 |
| 4 | 真实模型长任务评测预算 | 暂不投入 | 是否做 A（2-3 场景真实化）与 token 上限 |
| 5 | 跨机器集中观测 | 本地 | 是否需要与目标系统 |
