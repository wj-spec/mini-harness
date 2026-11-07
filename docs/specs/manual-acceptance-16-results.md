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
- 修复后 3/3 样本 ok，审批均 1 次。

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
