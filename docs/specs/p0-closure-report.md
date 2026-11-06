# 长任务 / 后台 / 进阶模式 P0 收束报告

日期：2026-11-06。基线：`f9c6e92`（报告更新前 HEAD）。
范围：[long-horizon 设计](../design/long-horizon/README.md) 01–16 的 P0 落地情况。

## 1. 验证结果

| 检查 | 结果 |
|---|---|
| Python 全量 | **1366 passed / 13 skipped** |
| TUI | **362 passed / 1 failed**；失败为环境 pty smoke（`out of pty devices`） |
| 长任务 fake 场景 | **10/10 PASS**（`scripts/bench_long.py --runs 1`；新增 `--real` 真 provider 入口） |
| 07 attach 人工验收 | TC-01–09 通过（[记录](manual-acceptance-07-results.md)） |
| 文档链接 | 74 个 specs/long-horizon 文档，**0 失效** |

## 2. 16 块 P0 状态

| # | 主题 | P0 结果 | 主要入口/证据 |
|---|---|---|---|
| 01 | 任务运行时 | 已实现 | `app/runtime/tasks.py`、`task_runtime.py` |
| 02 | worker 监督/attach | 已实现 | `worker.py`、`supervise.py`、`control-plane-attach.md` |
| 03 | 审批收件箱/超时 | 已实现 | `approvals.py`、worker approval timeout |
| 04 | 隔离/worktree/merge/stop | 已实现 | `workspace.py`、`merge.py`、`killswitch.py` |
| 05 | GoalRecord | 已实现 | `goals.py`、goal continuation |
| 06 | 预算账本 | 已实现 | `budget.py`、`budget-ledger.md` |
| 07 | 控制面 attach | 已实现 | controller/observer、心跳、v2 帧、TC-01–09 |
| 08 | 长任务评测 | P0 fake 场景 10/10 | `bench_long.py`、`benchmarks/long/` |
| 09 | 无人值守默认 | P0 部分 | conservative profile、env scrub、pause、两槽串行、deadline、retention |
| 10 | 模式状态机 | P0 chat/act/plan | `modes.py`、TUI `/chat` `/act` `/plan` |
| 11 | 模式契约 | P0 profile | mode profile、plan 只读子集 |
| 12 | Plan 产物 | P0 已实现 | `plan.py` schema/原子存储/渲染 |
| 13 | 计划审批门 | P0 已实现 | propose/approve/reject、hash、审计、执行门禁 |
| 14 | 执行绑定 | P0 已实现 | step ledger、证据推进、完成门 |
| 15 | 计划验收 | P0 已实现 | criteria 映射三态、可信命令执行、接入完成门 |
| 16 | 子 Agent | P0 已实现 | 只读 runner、子 TaskRecord、usage、取消/depth/批量扇出、`/explore` |

## 3. 关键能力闭环

```text
TUI /goal /chat /act /plan
  → TaskRuntime 创建/绑定 task（goal/run/plan；mode）
  → worker（可选 worktree、两槽写串行、kill switch）
  → goal 续段 / plan submit_plan
  → plan propose/approve（hash+audit）
  → /plan execute 绑定 approved plan（plan_id/version）
  → step ledger（start/complete + 工具证据）
  → 完成门：required 步骤 + 可信验收命令
  → subagent 只读执行（TaskRecord/usage/取消/扇出）
  → /explore 只读探索；/notifications 查看后台通知；/plan 覆盖层审批/修订
```

## 4. 已知限制（P0 之后仍需）

1. **真模型 P1 未做**：08 全部为 fake worker 场景；未验证真实长任务、成本与完成质量。
2. **Plan TUI**：计划卡片折叠/分页/编辑、stale 重批、审批 deadline 未完成；`/plan` 目前是文本渲染 + approve/reject/execute 命令。
3. **子 Agent**：stdio 事件/attach 展示、按 mode 的委派开关、父预算硬上限联动未完成；batch fan-out 只有基础限制。
4. **09 剩余**：events.jsonl 软上限/告警、红队清单自动化、只读任务并发与文件集并行、profile 变更审批入口。
5. **08 剩余**：真实 seed/时钟/5xx 故障注入、成本 cap、`agent_version`、重复运行统计报告。
6. **环境**：真实 TUI 验收受 pty 限制；browser smoke 在负载下偶发 flake。
7. **生产级**：合并后重验、跨任务队列调度、告警/观测仍非生产完成。

## 5. 结论

01–16 均已有可运行、可测试的 P0 基础，任务运行时到 Plan/SubAgent 的主链路已打通，并有自动化与人工验收证据。**这不是生产完成**；下一步优先真模型长任务 P1、Plan TUI 体验、09 剩余安全项，再做整体生产化收束。
