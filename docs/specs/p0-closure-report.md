# 长任务 / 后台 / 进阶模式 P0 收束报告

日期：2026-11-06。基线：`6d670db`（报告更新前 HEAD）。
范围：[long-horizon 设计](../design/long-horizon/README.md) 01–16 的 P0 落地情况。

## 1. 验证结果

| 检查 | 结果 |
|---|---|
| Python 全量 | **1603 passed / 13 skipped** |
| TUI | **414 passed / 1 failed**（415 收集）；失败为环境 pty smoke（`out of pty devices`）；含 14 个真实 Python 后端跨层集成用例 + TUI/Python 命令集合一致性检查；计划覆盖层新增 `d` 版本比较、`t` 步骤标题编辑、`k` 步骤类型编辑 |
| 长任务 fake 场景 | **14/14 PASS**（`--runs 1`）；重复运行 `--runs 3` → **42/42**，各场景 pass_rate=1.0（含 5xx/时钟/多段注入场景） |
| 07 attach 人工验收 | TC-01–09 通过（[记录](manual-acceptance-07-results.md)） |
| Plan/SubAgent 人工验收清单 | TC-16-01–08 已编写；自动化/真实模型部分已记录（[清单](manual-acceptance-16-plan-subagent.md)、[结果](manual-acceptance-16-results.md)），真实 TUI 键路待；新增 [TUI 终端快速验收清单](manual-acceptance-tui-quick.md) 便于终端一次走完 |
| 真实模型 Plan P1 冒烟 | deepseek-flash：只读任务 done、单步+验收 acceptance=true、两步计划+验收 acceptance=true、deepseek-flash/chat 多步+验收与审批帧样本通过；脚本步骤门禁/重试修复后连续 5/5 ok（审批均 1 次）；/explore ok；真实后台 goal worker、后台 Plan worker、edit 步骤（文件范围+审批+验收）通过；验收失败自动修复（--repair）连续 2/2；真实 TUI 人工键路待（[记录](real-p1-plan-smoke-2026-11-07.md)） |
| 文档链接 | 104 个 docs 文档，**0 失效**（`tests/test_docs_links.py` 持续校验） |
| 一键验收 | `./scripts/verify_all.sh [--with-real --real-runs N]` 汇总 Python/TUI/bench/doctor/redteam（2026-11-07 `--real-runs 2` 实测全通过，真实 suite ×2） |
| 本地自检（2026-11-07） | `--doctor` exit 0（含 key）；`--bench-history` 11 次无回归；`--cleanup-bench --log-dry-run` 11 candidates；`--trace-summary --trace-all-sessions` 28 会话/457 LLM calls/失败工具分布 |

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
| 08 | 长任务评测 | P0 fake 场景 14/14（含 5xx/时钟注入、2 个多段） | `bench_long.py`、`benchmarks/long/` |
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
  → CLI 只读：--tasks（含 APPROVALS/PRIORITY）/--plans/--acceptance/--notifications/--approvals/--evidence/--trace-summary（可跨会话/时间窗）/--bench-history/--cleanup-bench/--plan-card
  → CLI 响应：--decide-approval（无 TUI 时经 worker attach 通道）
  → /plan background：创建计划绑定 task + detached worker；TUI 退出后继续
  → --merge-task：合并审批预览含计划可信验收命令；合并后重跑，失败 exit 1 + 通知
  → goal 资源上限：段数/时长/总 token/总费用（后台按 budget.jsonl 累计）
```

## 4. 已知限制（P0 之后仍需）

1. **真模型 P1**：2026-11-07 已跑通 Plan 多步+验收+审批帧、`/explore`、后台 Plan/goal、edit 步骤，
   以及验收失败自动修复（`--repair` 修复后连续 2/2）、worktree→merge→合并后重验（`--merge` 连续 2/2）、`--suite` 六种模式（含 queue/goal）跨模型 **6/6 ×2**，
   `--suite-runs 2` 扩样本后 flash/chat 各 **2×6/6**；剩余为真实 TUI 人工键路。
   08 长任务 benchmark 仍为 fake worker 场景（含 5xx/时钟注入），真实模型长任务样本仍有限。
2. **Plan TUI**：覆盖层 + 侧栏步骤面板、审批/修订/执行、审批时限、审批卡（约束/验收/验证命令/门禁/hash）已接；步骤标题/类型/文件范围/依赖均可在 draft 阶段编辑（依赖有环检测）；更丰富的可视化仍可继续。
3. **子 Agent**：已有子 TaskRecord/subagent_event/取消/depth/批量扇出/可配置预算、CLI `--explore`；attach 回放、按 mode 委派开关、父预算硬上限联动仍待。
4. **09 剩余**：events 软上限告警、红队清单自动化、只读任务并发、外部 webhook 告警、profile 放宽审计已实现；
   文件集级并行写仍待；profile 放宽审批门已实现（`--approve-unattended-profile` + `VIBE_UNATTENDED_REQUIRE_APPROVAL`）。
5. **08 剩余**：5xx 注入（含本地 mock 服务端 503→200）、fake 时钟偏移注入、goal 成本 cap、
   重复运行统计、`agent_version`（bench 报告）已实现；seed 注入当前不适用（无随机采样）。
6. **环境**：真实 TUI 验收受 pty 限制；browser smoke 在负载下偶发 flake；TUI 侧栏测试已改为等待目标帧，不再受负载影响。
7. **生产级**：合并后重验（真实链路 2/2 已跑通）、外部 webhook 告警、doctor 运行时自检、
   计划任务 prepare+drain（含跨会话/跨工作区、continue-on-failure、1-8 并行、priority 排序）排队入口已接入；
   trace 观测摘要（`--trace-summary`）已提供，跨机器集中观测/追踪仍非生产完成。

## 5. 待用户拍板

见 [待用户拍板事项](open-decisions.md)：模型自发委派（P2）、文件集级并行写、真实 TUI 人工验收执行、跨机器集中观测。

## 6. 结论

01–16 均已有可运行、可测试的 P0 基础，任务运行时到 Plan/SubAgent 的主链路已打通，并有自动化与人工验收证据。2026-11-07 的生产化加固把以下项目推进到可验收状态：

- 真实验收：`--suite` 六模式（foreground/background/queue/goal/repair/merge）deepseek-flash/chat 各 **6/6**；
- 长任务评测：fake worker 场景 **14/14**（含 5xx/时钟注入、2 个多段）；
- 计划闭环：失败自动修复、合并后按计划重验、任务排队（跨会话/跨工作区/并行/priority/continue-on-failure/dry-run）；
- 无人值守：profile 放宽审计 + opt-in 审批门、events 软上限告警、外部 webhook、doctor 运行时自检；
- 观测：`--evidence`、`--trace-summary`（可跨会话）、TUI `/trace`、agent_version 进评测报告。

**这不是生产完成**；剩余为：真实 TUI 终端渲染/键盘人工验收（沙箱无 pty，已给 [10 分钟清单](manual-acceptance-tui-quick.md)）、
模型自发委派（设计列为 P2，需用户拍板）、attach 回放子 Agent（当前无使用路径）、文件集级并行写、跨机器/跨提交趋势对比。

## 7. 100 轮加固摘要（2026-11-07）

### 当前基线（第 114 轮，2026-11-07）

- Python **1603 passed / 13 skipped**；TUI **414 passed / 1 failed**（无 pty）。
- 长任务 fake：单轮 **14/14**；三轮 **42/42**，各场景 pass_rate=1.0。
- 真实模型：deepseek-flash/chat 各 **6/6**；`verify_all.sh --with-real --real-runs 2` 实测通过。
- 只读视图 12 个、真实后端跨层用例 14 个、文档链接 0 失效（有测试守护）。



第 55–100 轮在 P0/P1 基础上做生产化加固，所有改动均有测试与真实模型证据：

- **协议与前端一致性**：TUI/Python 命令集合一致性测试、命令参数校验补齐（notifications/explore/trace/plan）、
  计划参数 `kind/files/depends_on` 白名单、畸形帧/重复 id/busy 拒绝；真实后端跨层用例 **14 个**。
- **计划编辑与审批**：目标/步骤标题/类型/文件范围/依赖（环检测）可在 draft 阶段编辑；
  审批卡（目标/约束/验收/验证命令/步骤/门禁/不可执行原因/hash）在 TUI 覆盖层、`/plan card`、CLI `--plan-card` 三处可用。
- **队列**：`--prepare-plan-task`/`--run-plan-queue` 支持跨会话/跨工作区、并行、priority、continue-on-failure、dry-run；
  `--set-task-priority` 与 TUI `/queue`、`/priority` 让队列可查看/可调整。
- **观测与运维**：`--trace-summary`（单会话/跨会话/时间窗/TUI `/trace [H]`、失败工具与 LLM 错误分布）、
  `--bench-history`（回归标记）、`--cleanup-bench`（只清时间戳运行目录）、`--evidence`、`--doctor`
  （待审批/events/worktree/profile 审批门/benchmark 回归/真实模型报告）、`./scripts/verify_all.sh` 一键验收。
- **无人值守/告警**：profile 放宽 opt-in 审批门（hash 失效即拒绝启动）、webhook 仅 2xx 成功且重试一次。
- **子 Agent**：`/explore` 预算可配置（`VIBE_EXPLORE_*`）并新增 `--explore PROMPT` CLI（JSON 输出）。
- **真实模型**：`--suite-runs 2` 稳定性样本，deepseek-flash/chat 各 **2×6/6**；计划生成缺失自动重试一次。
- **损坏状态容错**：只读视图统一「跳过 + 计数 + stderr 告警」，并有全损坏工作区集成测试。
- **基线**：Python **1603 passed / 13 skipped**；TUI **414 passed / 1 failed**（无 pty 环境）；长任务 fake **14/14**；
  真实 suite 双模型 **6/6**。

剩余均依赖用户/环境：真实 TUI 终端键路人工验收、模型自发委派（P2）、文件集级并行写、跨机器集中观测
（见 [待用户拍板事项](open-decisions.md)）。
