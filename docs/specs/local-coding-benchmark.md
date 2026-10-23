# 本地编码基准

日期：2026-10-23。状态：3 个任务与跑分链路已实现；真实模型基线待运行。

## 目的

SWE-bench 首题只能证明评测接入可用，内部 eval 只是文件操作；它们都无法回答“这次改动让编码能力变强还是变弱”。本地基准用真实仓库副本、真实隐藏测试和真实 Agent 循环，提供一个**便宜、可重复、可归因**的标尺，作为后续系统提示、工具与循环改动的对照。

## 任务

| id | 类型 | 初始 bug | 隐藏验收 |
|---|---|---|---|
| `recall-guard` | Python 安全 | 移除 `ContextBuilder` 的 offload 路径校验 | 符号链接逃逸、绝对路径、非法引用；合法引用仍可读 |
| `footer-outcome` | Ink/TS | 页脚重复追加结果标签 | done / cancelled / stopped(token_budget) 各只显示一次 |
| `tool-count` | stdio 协议 | 取消 run 的 `run_end.tool_calls` 记 0 | 两个工具结果后取消，计数必须为 2 |

任务合同：初始 `red.sh` 失败；Agent 修复后 `check.sh` 退出 0；评分前用仓库原始测试覆盖工作区测试，防止改测试通过；隐藏测试不在工作区公开。

## 跑分流程

1. `git archive HEAD` 复制仓库到 `.vibe/bench-local/<stamp>/<task>/workspace`，链接 `.venv` 与 `tui/node_modules`。
2. 执行 `setup.py` 注入 bug，`red.sh` 确认初始失败。
3. 通过 `./run_vibe.sh --stdio` 启动真实 Agent 循环，自动批准 workspace/session 权限（基准环境），记录 steps、tokens、工具调用、审批与最终回复。工作区会剔除 `benchmarks/`、`scripts/bench_local.py`、基准规格与 `MILESTONES.md`，防止 Agent 直接读到隐藏测试或任务提示；runner 在报告里记录 `workspace_clean` 与 `hidden_leak_attempts`。
4. 修改前后文件哈希对比，列出改动文件与测试文件改动。
5. 执行 `check.sh` 评分；`report.json` 保存全部证据，`summary.json` 汇总。

参数：`--tasks`、`--timeout`（单任务秒数）、`--prepare`（不调模型）、`--fake`（验证链路，预期全 FAIL）。模型与预算沿用当前环境的 `VIBE_*` 配置。

## 指标与失败分类

- 通过率、steps、tokens、tool_calls、耗时、审批次数、改动文件数。
- 失败分类：没有复现失败、改错文件、修改测试、未运行验证命令、最终报告与工具输出不符。

## 基线（2026-10-23，`20261023-113622`）

| 任务 | 结果 | 步骤 | tokens | 工具调用 | 耗时 | 改动 |
|---|---|---:|---:|---:|---:|---|
| `footer-outcome` | 通过 | 24 | 425,906 | 28 | 63s | `tui/src/app.tsx` |
| `tool-count` | 通过 | 17 | 286,104 | 22 | 71s | `app/stdio.py` |
| `recall-guard` | 失败 | 18 | 323,430 | 23 | 70s | `core/context.py`（只加了格式校验） |

总计 59 步、约 1.04M tokens、73 次工具调用、约 3.4 分钟；自动批准 workspace 权限各 1 次，未修改测试文件，两份通过的最终报告与证据一致。`recall-guard` 的失败分类为 `incomplete_fix`：Agent 让可见测试通过，但没有处理任务所要求的“读不到工作区外”的符号链接绕过；隐藏测试正确判负。基线之后补充了“根因与安全边界”系统提示规则（见 W1 记录），作为第一个 A/B 改动，重跑前不改变结果口径。

**污染与隔离（2026-10-23）**：首轮基线时工作区仍包含基准自身文件，`recall-guard` Agent 未读取隐藏测试（日志核对）；第二轮（`20261023-114623`）的 Agent 在副本里发现并直接运行了 `benchmarks/local/recall-guard/hidden/test_bench_recall.py`，随后给出 3/3，这轮不能作为 A/B 证据。runner 已改为从工作区剔除基准夹具、评分脚本、基准规格与 MILESTONES，报告新增 `workspace_clean` / `hidden_leak_attempts`；第三轮在隔离后的工作区重跑，才是根因规则的干净对照。

## 基线协议

每次改动 harness 后，在相同模型、预算与任务集上运行一次，只对比同协议结果；先记录基线（当前版本），再改动并重跑。真实模型运行费用由运行者确认。

## 局限

- 当前 3 题都是“恢复已知修复”，难度低于开放式编码；先用于回归与对照，后续再加开放式任务（例如新增 CLI 功能、跨文件重构）。
- 注入脚本依赖源码锚点，仓库改动后需同步更新并在 `--prepare` 中确认 RED。
- 自动批准权限只用于基准副本，不代表真实审批体验。

## 验证

- `--prepare`：3 题全部 RED。
- 对未注入 bug 的工作区运行 `check.sh`：3 题全部退出 0（隐藏测试对修复后的代码有效）。
- `--fake`：链路可跑通、正确记为 FAIL（fake 不会修 bug）。
- 真实模型基线：待运行。
