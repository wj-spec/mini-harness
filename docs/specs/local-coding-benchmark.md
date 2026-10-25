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
| `stream-truncation`（held_out） | Provider 流式 | 截断工具调用重新变为可执行、异常断流不标记 | 只执行完整解析的调用；无 `[DONE]`/`finish_reason` 的流标 `truncated` |
| `restore-guard`（held_out） | 工具边界 | 移除恢复会话的重新读取守卫 | 重开后首次修改已有文件必须重读；外部改动仍拒绝；新文件不受影响 |
| `execution-chain`（held_out，跨层压测） | Agent + stdio + Ink | 执行证据链在 agent/core/stdio/tui 五处断裂 | 工具消息保留 execution；命令记录含退出码；Ink 显示 `零退出 · exit=N` |

任务合同：初始 `red.sh` 失败；Agent 修复后 `check.sh` 退出 0；评分前用仓库原始测试覆盖工作区测试，防止改测试通过；隐藏测试不在工作区公开。`held_out: true` 的题目未参与提示与修复调优；`--runs` 多次运行与 `config`（模型/预算/版本）一起用于判断可比较性。

**暂停与续做压测（2026-10-25）**：`--pause-tokens N` 用低预算让首轮停在 `stopped`，随后以 `--continue` 重开进程并发送 `/resume` 指令续做；报告写入 `resume_used` 与逐阶段 `phases`（状态、步数、tokens、run_id），`check.sh` 在续做结束后判定最终状态。fake 冒烟已验证两阶段链路（首轮 `stopped` → 续做 `done`）。真实模型的暂停/续做运行待跑，用于验证长任务恢复、上下文压缩与恢复守卫的协同。

**扩展记录（2026-10-25）**：三题均为“恢复已知修复”之外的补充——`stream-truncation`、`restore-guard` 覆盖流完整性与恢复守卫，`execution-chain` 是首个跨文件/跨语言压测题（注入点在 5 个文件，隐藏检查同时覆盖 Python 与 Ink）；三题均已用 `--prepare` 验证初始 red、恢复修复后 `check.sh` 通过。真实模型运行结果：`execution-chain` 3/3 通过且每次改动全部 5 个文件；同题三次 tokens 为 19.5 万 / 44.3 万 / 72.5 万，说明**单次运行不足以比较成本**，汇总新增 `per_task` 的 min/median/max 分布，后续对照需 `--runs` + 分布口径。

## 跑分流程

1. `git archive HEAD` 复制仓库到系统临时目录（不在仓库内，避免父级 `.git` 让 Agent 看到外层仓库），`git init` 并提交初始副本，链接 `.venv` 与 `tui/node_modules`；报告仍写在 `.vibe/bench-local/<stamp>/`。
2. 执行 `setup.py` 注入 bug，`red.sh` 确认初始失败。
3. 通过 `./run_vibe.sh --stdio` 启动真实 Agent 循环，自动批准 workspace/session 权限（基准环境），记录 steps、tokens、工具调用、审批与最终回复。任务提示附带副本的绝对路径，并声明“唯一工作目录、不要访问其它仓库”；报告用 `outside_workspace_commands` 标记跑出副本的命令，只有无改动又越界时归为 `wrong_directory`。工作区会剔除 `benchmarks/`、`scripts/bench_local.py`、基准规格、`MILESTONES.md` 与 `tests/test_bench_local.py`，防止 Agent 直接读到隐藏测试或任务提示；runner 在报告里记录 `workspace_clean` 与 `hidden_leak_attempts`。
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

**第三轮环境问题（2026-10-23，`20261023-115821`）**：隔离后 `workspace_clean=true`、无隐藏泄漏，结果 3/3（88 步、约 1.62M tokens）。但报告显示副本仍嵌在外层仓库的 `.vibe/` 下：`tool-count` Agent 在外层仓库路径执行测试与反证，验证对象不是副本；同时被剔除的 `scripts/bench_local.py` 让副本里的 `tests/test_bench_local.py` 变红，干扰全量测试判断。该轮只作为能力信号（2/3 → 3/3），环境与成本口径由第四轮重测；runner 已改为仓库外独立临时目录 + 独立 git 仓库，并补剔除对应测试。

**第四轮（2026-10-23）**：工作区已隔离为仓库外独立 git 仓库。`recall-guard`（39 步、约 756k tokens）与 `tool-count`（13 步、约 199k tokens）均在副本内完成并通过，`outside_workspace_commands` 为空；`footer-outcome` 的 Agent 却从临时目录 `cd` 到 `/Users/.../mini-harness` 外层仓库，把外层已修复的代码当成工作区，判为 `wrong_directory`（seed 实际已在副本生效）。因此在任务提示中加入唯一工作区绝对路径与越界检测，footer 单独重跑；其余两题可作为第四轮的干净结果。

### A/B 结论（诚实规则 vs + 根因/安全边界规则）

| 口径 | 通过 | 步数 | tokens | 工具调用 | 耗时 |
|---|---:|---:|---:|---:|---:|
| v1 基线（只有诚实规则） | 2/3 | 59 | 1,035,440 | 73 | 205s |
| v4 干净（+ 根因/安全边界） | **3/3** | 62 | 1,141,757 | 77 | 294s |
| 变化 | +1 | +5% | +10% | +5% | +44% |

干净结果：`recall-guard` 39 步/756k tokens（补上符号链接防护）、`tool-count` 13 步/199k（比基线 17 步/286k 更低）、`footer-outcome` 10 步/187k（基线 24 步/426k 更低）。能力提升成立，代价是 `recall-guard` 型安全任务验证更充分、耗时上升；其余两题反而更省。注意当前 3 题都是“恢复已知修复”，且提示规则针对这批任务调过，存在过拟合风险；下一步需要未见过的任务族（开放式功能/跨文件改动）再验证泛化。

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
