# 全链路验收收束（2026-11-05）

基线：`bf371e7`（本轮验收报告提交前的 HEAD）。范围：任务运行时 / 后台 worker / 目标续段 / 审批 / 预算 / 隔离与合并 / attach 控制面。
本报告只陈述**已实际运行验证**的内容；未做人工真实 TUI/真实模型长任务验收。

## 1. 验证命令与结果

| 检查 | 命令 | 结果 |
|---|---|---|
| Python 全量回归 | `./run_tests.sh` | **1285 passed, 13 skipped**（约 32s） |
| 运行时定向验收 | `./run_tests.sh tests/test_task_store.py tests/test_task_runtime.py tests/test_stdio_tasks.py tests/test_worker.py tests/test_worker_attach_control.py tests/test_goals.py tests/test_budget_ledger.py tests/test_approvals.py tests/test_workspace.py tests/test_merge.py tests/test_killswitch.py tests/test_cli_tasks.py tests/test_cli_merge_stop.py` | **81 passed, 0 failed** |
| TUI 构建 + 测试 | `cd tui && npm test` | **354 passed / 1 failed**；失败项为 `cli.test` 的 pty smoke，环境报 `out of pty devices`；`npm test` 已固定 `NO_COLOR=1` 避免终端颜色环境差异 |
| 文档链接 | 65 个 docs/specs + long-horizon 文档的相对链接扫描 | **0 失效** |
| CLI smoke | `./run_vibe.sh --tasks` | 输出 `no tasks under <workspace>`，退出 0 |
| 工作树 | `git status --short` | 干净（报告提交前） |

环境：macOS，Python 3.12.13（项目 `.venv`），Node v24.16.0，git 可用。
说明：全量运行偶发 `test_browser_actions_smoke::test_browser_worker_kills_node_even_when_process_scan_is_unavailable` 时序失败，单跑与重跑通过；已按环境 flake 记录，未改其断言。

## 2. 能力矩阵（本次验收覆盖）

| 能力 | 规格 | 状态 | 证据 |
|---|---|---|---|
| TaskRecord/TaskStore：任务身份、状态机、原子写、锁/stale 恢复 | task-runtime.md | **已实现** | `test_task_store.py`；`app/runtime/tasks.py` |
| stdio `run.task` / `task_state` / `/goal` 接入 | task-runtime-integration.md | **已实现** | `test_stdio_tasks.py`；`app/stdio.py` |
| 后台 worker：独立 stdio 子进程、worker.pid/lock/log、ctrl.sock、事件回放 | process-supervision.md（P0a/P0b） | **已实现** | `test_worker.py`；`app/runtime/worker.py` |
| 后台 worker 在 git 仓库自动创建 worktree、工具写入根指向 worktree | workspace-isolation.md（P1） | **已实现** | `test_worker.py::test_worker_uses_git_worktree_when_available` |
| GoalRecord v2、legacy goal.json 迁移、段同步 | goal-record.md（P0） | **已实现** | `test_goals.py`；`app/runtime/goals.py` |
| worker 按 GoalRecord 续段（资源停止自动下一段） | goal-record.md（P1a） | **已实现（worker 路径）** | `test_worker.py::test_worker_goal_loop_continues_resource_stops` |
| 预算账本 budget.jsonl、trace 记账、段/任务结算 | budget-ledger.md（P0） | **已实现** | `test_budget_ledger.py`；`test_stdio_tasks.py` |
| TaskRuntime `check_start` 硬预算（`VIBE_TASK_BUDGET_HARD=1`） | budget-ledger.md（P1 部分） | **已实现** | `test_budget_ledger.py::test_check_start_hard_budget` |
| 审批收件箱 approvals.jsonl、一次性/过期/归属/脱敏 | approval-inbox.md（P0） | **已实现** | `test_approvals.py`；`app/runtime/approvals.py` |
| worker 审批：request→inbox、attach→resolve、超时→failed(approval_timeout) | approval-inbox.md（P1 部分） | **已实现** | `test_worker.py::test_worker_approval_timeout_fails_task_and_expires_inbox` |
| TUI `/approvals` 只读列表 | approval-inbox.md | **已实现（列表）** | `test_stdio_tasks.py`；TUI app test |
| Workspace 写锁 + git worktree + stale 报告 | workspace-isolation.md（P0/P1） | **已实现** | `test_workspace.py` |
| MergeGate：审批、漂移/脏工作区/冲突拦截 | workspace-isolation.md（P1） | **已实现** | `test_merge.py` |
| 合并后验证命令 | workspace-isolation.md（P1） | **已实现（CLI/API）** | `test_merge.py::test_merge_runs_post_merge_verification` |
| CLI `--stop` workspace kill switch / worker stop 信号 | workspace-isolation.md（P1） | **已实现** | `test_killswitch.py`；`test_cli_merge_stop.py` |
| CLI/TUI stop/merge 入口（TUI `/stop-workers`、`/merge`） | workspace-isolation.md | **已实现** | TUI app tests / CLI tests |
| TUI attach：`--attach-task --session`、controller/observer、心跳租约、capability、task_snapshot、replay/live 锁、attach v2 帧（snapshot/approval list/decide） | control-plane-attach.md（P0） | **已实现** | `test_worker_attach_control.py`；TUI `attach-args` / protocol tests |
| TUI `/attach`、`/attach now` | control-plane-attach.md | **已实现（一键重启未人工验收）** | TUI app tests |

## 3. 已知限制 / 未验收

1. **TUI pty 验收缺失**：当前执行环境没有可用 pty，`tui/tests/cli.test.ts` 的交互式终端 smoke 失败；真实 TUI + worker attach 的交互流程（尤其 `/attach now` 重启）未人工验收。
2. **真实模型长任务未验收**：以上以 fake provider / 脚本化 child / git fixture 为主；真实模型、真实长任务 ≥2 段、跨小时、工具链成本未验收。
3. **07 v2 仅覆盖 attach 层**：`task_snapshot_request/approval_list_request/approval_decide` 已实现；完整 v2 stdio 双栈、`task_snapshot.approval_pending` 的正式语义与协议升级仍未做。
4. **非 git 工作区只做了默认拒绝**：没有任务写/读分类；`VIBE_ALLOW_NON_GIT=1` 是人工逃生口，不是只读保证。
5. **合并后验证未接审批/计划**：`--merge-verify` 是显式命令，尚未从 Plan/verification_plan 自动生成；失败不回滚合并。
6. **预算**：预留、per-child 归并、摘要/审计成本强制计入仍未做；`check_start` 只在硬模式生效。
7. **TODO**：worktree stale 清理 CLI、合并审批的 TUI 完整流程、长任务评测（08）、无人值守安全默认值（09）、模式/Plan 实现（10–16）仍未开始或未完成。
8. **flaky**：browser smoke 在负载下偶发时序失败。

## 4. 复现

```bash
./run_tests.sh
cd tui && npm test
./run_vibe.sh --tasks
./run_vibe.sh --stop --workspace .
# 后台任务端到端（fake、git 仓库；见 tests/test_worker.py）
```

## 5. 结论

核心后台长任务链路已经形成可运行闭环：

**TaskRecord/TaskStore → `/goal` 接入 → worker（可选 worktree）→ GoalRecord 续段 → 预算记账 → 审批收件箱/超时失败 → attach 观察/控制 → MergeGate 合并与后验**

该链路有确定性 Python 回归（1285 项）与 81 项定向验收；TUI 构建与 354 项测试通过，剩余 1 项受 pty 环境限制。**尚未达到生产完成**：真实模型长任务、TUI 人工验收、完整 v2 双栈、08/09/10–16 仍是后续工作。
