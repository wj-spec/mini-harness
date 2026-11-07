# 长任务 / 后台 / 进阶模式 P0 收束报告

日期：2026-11-06。基线：`6d670db`（报告更新前 HEAD）。
范围：[long-horizon 设计](../design/long-horizon/README.md) 01–16 的 P0 落地情况。

## 1. 验证结果

| 检查 | 结果 |
|---|---|
| Python 全量 | **1501 passed / 13 skipped** |
| TUI | **394 passed / 1 failed**（395 收集）；失败为环境 pty smoke（`out of pty devices`）；含 8 个真实 Python 后端跨层集成用例；计划覆盖层新增 `d` 版本比较、`t` 步骤标题编辑 |
| 长任务 fake 场景 | **12/12 PASS**（`scripts/bench_long.py --runs 1`；含 2 个 5xx 注入场景；另有 `--real` 真 provider 入口） |
| 07 attach 人工验收 | TC-01–09 通过（[记录](manual-acceptance-07-results.md)） |
| Plan/SubAgent 人工验收清单 | TC-16-01–08 已编写；自动化/真实模型部分已记录（[清单](manual-acceptance-16-plan-subagent.md)、[结果](manual-acceptance-16-results.md)），真实 TUI 键路待 |
| 真实模型 Plan P1 冒烟 | deepseek-flash：只读任务 done、单步+验收 acceptance=true、两步计划+验收 acceptance=true、deepseek-flash/chat 多步+验收与审批帧样本通过；脚本步骤门禁/重试修复后连续 5/5 ok（审批均 1 次）；/explore ok；真实后台 goal worker、后台 Plan worker、edit 步骤（文件范围+审批+验收）通过；验收失败自动修复（--repair）连续 2/2；真实 TUI 人工键路待（[记录](real-p1-plan-smoke-2026-11-07.md)） |
| 文档链接 | 78 个 specs/long-horizon 文档，**0 失效** |

## 2. 16 块 P0 状态

| # | 主题 | P0 结果 | 主要入口/证据 |
|---|---|---|---|
| 01 | 任务运行时 | 已实现 | `app/runtime/tasks.py`、`task_runtime.py`、`--prepare-plan-task/--run-plan-queue` 排队入口 |
| 02 | worker 监督/attach | 已实现 | `worker.py`、`supervise.py`、`control-plane-attach.md` |
| 03 | 审批收件箱/超时 | 已实现 | `approvals.py`、worker approval timeout |
| 04 | 隔离/worktree/merge/stop | 已实现 | `workspace.py`、`merge.py`、`killswitch.py`、`--prune-worktrees` |
| 05 | GoalRecord | 已实现 | `goals.py`、goal continuation、confirmed criteria 可信验收 |
| 06 | 预算账本 | 已实现 | `budget.py`、`budget-ledger.md` |
| 07 | 控制面 attach | 已实现 | controller/observer、心跳、v2 帧、TC-01–09 |
| 08 | 长任务评测 | P0 fake 场景 12/12（含 5xx 注入） | `bench_long.py`、`benchmarks/long/` |
| 09 | 无人值守默认 | P0 部分 | conservative profile、env scrub、pause、两槽串行、deadline、retention、events 软上限告警、doctor 运行时提示、外部 webhook 告警通道、profile 放宽审计 |
| 10 | 模式状态机 | P0 chat/act/plan | `modes.py`、TUI `/chat` `/act` `/plan` |
| 11 | 模式契约 | P0 profile | mode profile、plan 只读子集 |
| 12 | Plan 产物 | P0 已实现 | `plan.py` schema/原子存储/渲染 |
| 13 | 计划审批门 | P0 已实现 | propose/approve/reject、hash、审计、执行门禁 |
| 14 | 执行绑定 | P0 已实现 | step ledger、证据推进、完成门 |
| 15 | 计划验收 | P0 已实现 | criteria 映射三态、可信命令执行、接入完成门、rounds/`/evidence` 三态展示 |
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
  → 完成门：required 步骤 + 可信验收命令（可配置验收失败自动修复回灌）
  → subagent 只读执行（TaskRecord/usage/取消/扇出）
  → /explore 只读探索 + subagent_event；/notifications 查看后台通知
  → /plan 覆盖层：折叠/展开、↑↓选择、s步骤详情、e修订、y/n审批、Enter执行
  → 侧栏：p 打开计划步骤面板（↑↓ 选择、done/total、✓/✗）
  → 通知：plan_proposed/approved/rejected/acceptance、goal_segment/stopped、
    subagent_finished、workspace_queued、merge_*；可选 webhook 外发
  → CLI 只读：--tasks/--plans/--acceptance/--notifications/--approvals/--evidence
  → CLI 响应：--decide-approval（无 TUI 时经 worker attach 通道）
  → /plan background：创建计划绑定 task + detached worker；TUI 退出后继续
  → --merge-task：合并审批预览含计划可信验收命令；合并后重跑，失败 exit 1 + 通知
  → goal 资源上限：段数/时长/总 token/总费用（后台按 budget.jsonl 累计）
```

## 4. 已知限制（P0 之后仍需）

1. **真模型 P1**：2026-11-07 已跑通 Plan 多步+验收+审批帧、`/explore`、后台 Plan/goal、edit 步骤，
   以及验收失败自动修复（`--repair` 修复后连续 2/2）、worktree→merge→合并后重验（`--merge` 连续 2/2）、`--suite` 六种模式（含 queue/goal）跨模型 **6/6 ×2**；仍有真实 TUI 人工键路与更大样本；
   08 长任务 benchmark 仍为 fake worker 场景（含 5xx 注入）。
2. **Plan TUI**：覆盖层 + 侧栏步骤面板、审批/修订/执行、审批时限已接；完整审批卡片与更细粒度编辑仍待。
3. **子 Agent**：已有子 TaskRecord/subagent_event/取消/depth/批量扇出；attach 回放、按 mode 委派开关、父预算硬上限联动仍待。
4. **09 剩余**：events 软上限告警、红队清单自动化、只读任务并发、外部 webhook 告警、profile 放宽审计已实现；
   文件集级并行写、profile 变更的审批入口仍待。
5. **08 剩余**：5xx 注入、goal 成本 cap、重复运行统计已实现；时钟/seed 注入、`agent_version`、
   真实 5xx 服务端注入仍待。
6. **环境**：真实 TUI 验收受 pty 限制；browser smoke 在负载下偶发 flake。
7. **生产级**：合并后重验（真实链路 2/2 已跑通）、外部 webhook 告警、doctor 运行时自检、
   计划任务 prepare+drain（含跨会话/跨工作区串行、continue-on-failure）排队入口已接入；并行队列/优先级与完整观测/追踪仍非生产完成。

## 5. 结论

01–16 均已有可运行、可测试的 P0 基础，任务运行时到 Plan/SubAgent 的主链路已打通，并有自动化与人工验收证据。**这不是生产完成**；下一步优先真模型长任务 P1、Plan TUI 体验、09 剩余安全项，再做整体生产化收束。
