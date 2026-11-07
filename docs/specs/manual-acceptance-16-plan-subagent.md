# Plan / SubAgent P0 人工验收清单

日期：2026-11-06。范围：Plan 模式、计划审批/执行/验收、只读子 Agent。
自动证据见 [P0 收束报告](p0-closure-report.md)；本清单供真机人工复核。

## 0. 环境

```bash
cd /path/to/mini-harness
./run_tests.sh -q
(cd tui && npm test)
./run_ink.sh --fake    # 无 API key 的界面冒烟
```

真模型验收需要已配置 `VIBE_API_KEY`（以及可选 `VIBE_BASE_URL/VIBE_MODEL`）。
自动化部分可先跑 `scripts/real_p1_acceptance.py`（见 USAGE）；本清单仍用于 TUI 人工键路。
建议使用一次性 git 仓库作为 `--workspace`，不要在含未提交改动的主工作区做。

## TC-16-01 模式与只读规划

1. 启动工作台：`./run_ink.sh --workspace /tmp/vibe-acceptance`；
2. 输入 `/plan 阅读 README 并给出一个只读计划`；
3. 等待 run 结束。

**期望**
- 生成 `tasks/<task>/plan/plan.v1.json`，status=proposed；
- 计划包含 objective/steps/acceptance 字段；
- plan 模式工具仅只读 + `submit_plan`，不应出现写文件/命令执行。

## TC-16-02 计划审批与执行门禁

1. `/plan` 打开覆盖层，确认目标/步骤；
2. 未批准时按 Enter 或 `/plan execute`。

**期望**
- 未批准：拒绝执行并提示先批准；
- `/plan approve` 后 status=approved，approval 含 plan_hash/capability；
- 未批准计划即使模型尝试 write/exec 也被 execution gate 拒绝，文件不产生（自动测试 `test_bound_unapproved_plan_blocks_write_at_gate`）。

## TC-16-03 步骤证据与完成门

1. 批准计划后 `/plan execute`；
   - 长计划可 `/plan background`：TUI 退出后 worker 继续执行（`--tasks` 查看）；
2. 观察 `start_plan_step` / `complete_plan_step` 与工具调用。

**期望**
- `plan/execution.v1.json` 记录 step 状态；
- read/file_change/command_ok 证据写入对应 step；
- required 步骤未完成时 run 不能 done，任务 paused(plan_incomplete)。

## TC-16-04 验收命令

1. 计划包含 confirmed criterion + `verification_plan`（source=plan_approval）；
2. 执行计划，批准验收命令；
3. 分别测试成功命令与失败命令。

**期望**
- 成功：run done，`acceptance.v1.json` status=true，通知 `plan_acceptance`；
- 失败：run stopped/acceptance_<code>，结果文本含失败命令与 exit/输出摘要；
- 设置 `VIBE_ACCEPTANCE_RETRIES=2` 时 flaky 命令可重试（最多 3）。

## TC-16-05 stale / 修订 / 版本

1. 已批准计划执行中发送纠偏（steer）；
2. `/plan list`、`/plan diff 1 2`、`/plan revise 新目标`；
3. 批准新版本后 `/plan execute`。

**期望**
- steering 后 approval.stale=true，写/执行门禁返回 plan_stale；
- revise 生成 version+1 draft，旧版内容不变；
- `/plan execute` 选择最新可执行 approved 非 stale 版本，最新 draft 会回退并提示；
- 设置 `VIBE_PLAN_REVIEW_TIMEOUT_SECONDS` 时超时批准被拒绝。

## TC-16-06 只读子 Agent

1. 输入 `/explore 读 README 总结项目结构`；
2. 等待 command_result；
3. `/task` 或 `--tasks` 查看。

**期望**
- 出现子 Agent 开始/结束 notice 与 `subagent_event`；
- 创建 `kind=subagent` TaskRecord，状态 completed；
- 子 Agent 不可写文件/执行命令（只读子集）；父取消/深度限制见自动测试。

## TC-16-07 CLI 只读视图

```bash
./run_vibe.sh --tasks --workspace /tmp/vibe-acceptance
./run_vibe.sh --plans --workspace /tmp/vibe-acceptance
./run_vibe.sh --acceptance --workspace /tmp/vibe-acceptance
./run_vibe.sh --notifications --workspace /tmp/vibe-acceptance
```

**期望**
- 均只读、不创建 session；
- plans 含版本/status/stale/objective；
- acceptance 含 status/codes；
- notifications 含 worker/plan_acceptance 事件。

## TC-16-08 侧栏摘要

1. 打开 `/sidebar`（Git 变更侧栏）；
2. 打开 `/plan`。

**期望**
- 侧栏 header 出现 `计划 vN status done/total步`；
- 有验收结果时追加 ✓/✗；
- 按 `p` 打开计划步骤面板，↑↓ 选择步骤，`p` 返回变更；
- 侧栏不阻塞主 TUI。

## 记录

| 用例 | 环境 | 结果 | 备注 |
|---|---|---|---|
| TC-16-01 |  |  |  |
| TC-16-02 |  |  |  |
| TC-16-03 |  |  |  |
| TC-16-04 |  |  |  |
| TC-16-05 |  |  |  |
| TC-16-06 |  |  |  |
| TC-16-07 |  |  |  |
| TC-16-08 |  |  |  |

当前限制：无 API key 环境无法完成真模型步骤；pty smoke 在无 pty 设备环境失败（`out of pty devices`）。
