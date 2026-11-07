# Plan / SubAgent 人工验收结果（自动化部分）

日期：2026-11-07。清单见 [TC-16 清单](manual-acceptance-16-plan-subagent.md)。
说明：本文件记录可在本机自动化/真实模型环境复现的部分；真实 TUI 键路因本机 pty
限制（`openpty: Operation not permitted`）仍需用户在支持 pty 的终端执行。

## 1. 真实模型结果

模型：deepseek-flash（另用 deepseek-chat 交叉验证）。

| 场景 | 结果 | 证据 |
|---|---|---|
| 单 Agent 只读任务 | done，2/2 工具成功，3614 tokens | [真实 P1 记录](real-p1-plan-smoke-2026-11-07.md) |
| Plan 生成 | proposed，含 steps/criteria/verification | 同上 |
| 两步计划 + 验收 | s1/s2 done，acceptance=true | 3/3 干净会话样本 |
| 审批帧路径 | 5 次/3 次审批，s1/s2 done，acceptance=true | 2/2 样本 |
| deepseek-chat | 多步+验收 1/1，审批 1/1 | 同上 |
| `/explore` 子 Agent | status=ok，subagent_event + 子任务 | 同上 |

## 2. 可复用脚本

```bash
set -a; . ./.env; set +a
VIBE_TASK_RUNTIME=1 ./.venv/bin/python3 scripts/real_p1_acceptance.py
```

- 临时 git 工作区；
- Plan 生成 → 批准 → 执行 → 审批帧 → 验收；
- `/explore` 子 Agent；
- JSON 报告 `.vibe/real-p1-acceptance/<timestamp>.json`；
- 失败默认保留工作区，`--discard-on-failure` 可关闭；
- 最终修复后连续 5/5 样本 ok，s1/s2 done、acceptance=true、审批均 1 次；
- `--background` 真实后台路径 deepseek-flash 4/4、deepseek-chat 1/1（worker exit 0、
  plan steps+acceptance）。

## 3. TC-16 自动化映射

| 用例 | 自动化证据 | 状态 |
|---|---|---|
| TC-16-01 模式/只读规划 | 真实 plan.v1 + 单测 plan mode 工具子集 | 通过（真实模型） |
| TC-16-02 审批与门禁 | `test_bound_unapproved_plan_blocks_write_at_gate`、`plan_binding` | 通过 |
| TC-16-03 步骤证据/完成门 | 真实 s1/s2 done + ledger evidence；单测 | 通过（真实模型） |
| TC-16-04 验收命令 | 真实 `true` 与 `grep -q real-p1` acceptance=true；`grep NOT_PRESENT` acceptance=false；失败/超时/重试单测 | 通过（成功+失败路径） |
| TC-16-05 stale/修订/版本 | 单测 can_execute stale、revise/list/diff、审批窗口 | 通过（单测） |
| TC-16-06 子 Agent | 真实 `/explore` ok + subagent_event；单测取消/depth/批量 | 通过（真实模型） |
| TC-16-07 CLI 只读视图 | `tests/test_cli_tasks/plans/acceptance/notifications*.py` | 通过 |
| TC-16-08 侧栏摘要 | `tui/tests/sidebar-*.test.tsx` + TUI 测试 | 通过（组件级） |
| 真实 TUI 键路 | pty smoke 环境失败 | **待用户终端** |

## 4. 未完成

- 真实 TUI 键路与终端渲染；
- 真实多 provider（当前两个 deepseek 型号）；
- 真实模型大样本/失败样本统计；
- 多步 + 非 `true` 的真实业务验收命令。

结论：Plan/SubAgent P1 的**真实模型与自动化路径已通过**；TUI 键路与更大样本仍待。

## TC-16-04b 真实验收失败自动修复（2026-11-07）

- 命令：`VIBE_TASK_RUNTIME=1 ./.venv/bin/python3 scripts/real_p1_acceptance.py --repair`
- 结果：run done、s1/s2 done、acceptance=true、`plan_repair` 1 次、marker 已创建、审批 3 次；
- 稳定性：首次实现 1 通过 / 1 次 provider ConnectTimeout；补 provider 错误重试后连续 2/2 通过。

## TC-16-09 真实 worktree → merge → 合并后重验（2026-11-07）

- 命令：`VIBE_TASK_RUNTIME=1 ./.venv/bin/python3 scripts/real_p1_acceptance.py --merge`
- 结果：edit 计划在后台 worktree 执行 s1/s2 done、acceptance=true → task completed；
  merge 审批后 `merged=true`、`post_merge_verified=true`、`merge_completed.verified=true`；
- 稳定性：连续 2/2 通过。

## TC-16-10 一条命令四种真实链路（suite，2026-11-07）

- 命令：`VIBE_TASK_RUNTIME=1 ./.venv/bin/python3 scripts/real_p1_acceptance.py --suite`
- 覆盖：foreground / background / repair / merge 四种模式；
- 结果：deepseek-flash **4/4**、deepseek-chat **4/4** 全通过；
- 期间修复：后台 plan 步骤提示注入、修复轮与只读约束冲突、纯分析步骤 manual 证据。

## TC-16-11 TUI 审批链路跨层集成（无需 pty，2026-11-07）

- `tui/tests/backend-integration.test.tsx` 启动真实 `run_vibe.sh --stdio --fake`，用 Ink `App` 驱动：
  - `/plan list`、`/cost` 的真实返回（捕获命令参数转发回归）；
  - `VIBE_FAKE_DEMO=1` 运行：两次审批（写文件 / 执行命令）→ done，校验 `.vibe/fake-demo.txt` 内容；
  - 计划覆盖层：写入 proposed 计划 → `/plan` 打开 → 按 `y` → 真实后端把计划置为 approved；
  - `/explore`：prompt 转发成功，返回「子 Agent ok」并收到 `subagent_event`；
  - `/notifications plan_acceptance`：kind 过滤生效（只出现 plan_acceptance）；
  - `/plan step s1`：step_id 转发成功，返回步骤定义与 files；
  - `/task` + `/approvals`：seeded task meta + 待审批后分别显示「待审批 1」与审批条目；
- 结果：TUI **393/394**（唯一失败为环境 pty smoke）。真实键盘/渲染路径仍需人工终端验收。
