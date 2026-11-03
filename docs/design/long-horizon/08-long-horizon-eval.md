# 08 长任务评测：从单题跑分到可重放、可封顶的长程验收

> 状态：讨论稿（2026-11-02；2026-11-03 01–05 决策对齐见 §9，用户回答见 §10）；不是 spec，未承诺实现。
> 所属：长任务与后台任务 / 第 8 块（长任务评测）
> 前置/相关：[01 任务运行时](01-task-runtime.md)、[02 后台进程监督](02-process-supervision.md)、[03 无人值守审批](03-unattended-approval.md)、[04 隔离与并发](04-isolation-concurrency.md)、[05 目标状态](05-goal-state.md)、[06 预算账本](06-budget-ledger.md)、[07 控制面与协议](07-control-plane.md)、[16 子 Agent 的有机结合](16-subagent-integration.md)；相关规格：[evaluation](../../specs/evaluation.md)、[local-coding-benchmark](../../specs/local-coding-benchmark.md)、[harbor-benchmark-lessons](../../specs/harbor-benchmark-lessons.md)、[verified-completion](../../specs/verified-completion.md)

## 1 问题与范围

长任务的失败形态和单题不同：不是「题答不出」，而是「跑着跑着丢了、漂了、超支了、恢复后重复执行、模型自评完成」。现有验证基本停在单 run 或单题，唯一例外是本地基准的一次暂停续做（§2），因此后台执行没有可量化的验收方式。

2026-11-03 已确认的决策改变了评测基线：TaskRuntime 权威 + 后台任务默认开启（范围待确认，[01 §9](01-task-runtime.md)）；每 task 一个 worker + supervisor（[02 §9](02-process-supervision.md)）；required 审批过期→任务失败（[03 §9](03-unattended-approval.md)）；写串行/读并发与 kill switch（[04 §9](04-isolation-concurrency.md)）；`goal-record.json` 与用户确认的 `acceptance_criteria`（[05 §9](05-goal-state.md)）。本块要把这些行为变成可重复、可归因、可封顶的场景；06 预算账本仍未定稿。

- 在范围内：长任务与后台场景集、指标、故障注入、对照组与阈值、成本上限、报告口径。
- 不在范围内：执行机制与任务模型（01/02）、账本实现（06）、子 Agent 的 A/B 具体设计（16，本块只提供框架）。
- 原则：先 fake 确定性场景，再真模型小样本；**没有收益就停**，沿用 W6 的判定纪律（`docs/specs/w6-benefit.md:92-98`）。

## 2 现状与证据

**已有验证层及其长任务适用性**

| 层 | 覆盖什么 | 对长任务是否够用 | 证据 |
|---|---|---|---|
| 单元/回归 | 单点机制与崩溃修复（缺 tool 结果补 `interrupted`、幂等重跑） | 只证明零件，不证明跨段 | `tests/test_session.py:163-175`；`tests/test_context_persistence.py:115-120` |
| fake smoke / 内部 eval | 接线；10 个小任务在宿主单 run 跑，自动审批 | 单 run，无中断/续段 | `docs/specs/evaluation.md:7-13`；`app/eval/internal.py:298-310,313-330` |
| 本地编码基准 | 注入 bug、初始 RED、隐藏测试、`verified_fix`、A/B 与 config 冻结 | 单 run 修复；`--pause-tokens` 只做一次暂停续做 | `docs/specs/local-coding-benchmark.md:20,26-34,55-63`；`scripts/bench_local.py:365-469,408-417` |
| SWE-bench Lite dev | 容器执行 + 官方独立评分；默认 1 题、1M token/600s | 单题分钟级，无跨段 | `app/eval/swebench.py:416-431,450-459` |
| Harbor / SWE-bench Verified | 2026-11-02：500 题集、5 题 smoke 5/5、20 题 oracle 19/20、旧策略 19 题 15/19、新策略 3/3、官方 3/3 | 单题短任务；「口径纪律」值得继承 | `docs/specs/harbor-benchmark-lessons.md:9-17,219-231` |
| TUI 测试 | 协议校验、渲染、真实子进程接线 | 不证明长时运行 | `README.md:96` |

注意：[README 的工程验证](../../../README.md)（`README.md:96-101`）仍是 **2026-10-31** 口径；Harbor Verified 的 2026-11-02 结果只在踩坑记录里，引用时必须标日期。

**长任务机制现状**

- `/goal` 分段在 TUI：资源停止自动续段、其余暂停、`verified=false` 暂停；默认 8 段/4 小时；状态写 `<session>/goal.json`（`docs/specs/goal-mode.md:3,13-31`；`tui/src/goal.ts:31-34,75-137`；`tui/src/goal-store.ts:5,15-23,43-55`）。已确认方向是 `goal-record.json` 由 Python 单写、段决策迁到 TaskRuntime（05/01 §9），尚未实现；真实长任务验收未完成（`README.md:124`；`docs/MILESTONES.md:321`）。
- 后台默认开启（01 §9，范围待确认）与 controller/observer（02 §9）尚未实现：当前 TUI 关闭即取消、无任务列表/attach、无 per-task worker。
- 崩溃恢复有零件：信号→stop、崩溃补 `interrupted`、`--continue` 重载会话（`app/stdio.py:715-718`；`app/session/store.py:392-420`）；W2/W3 验收是**人工引导**的 Ink 步骤 + 取证脚本，不是可重复基准（`docs/specs/w2-w3-acceptance.md:3,8-25`）。
- 观测/成本口径分散：`run_end` per-run（`app/session/trace.py:152-171`）；`/cost` 只汇总会话内 `run_end`（`app/session/store.py:203-225`）；跨段总额只在 TUI `goal.json`；LLM 审计成本不计父 run（`docs/specs/verified-completion.md:123`）；审批等待从运行时限扣除（`docs/specs/practical-use-reliability.md:10`；`app/runtime/agent.py:1895-1896,1922-1923`）。账本按 task/goal/segment 记账仍是 06 的待定稿设计。
- 可继承的有效性纪律：Harbor 的 oracle preflight、官方 vs Harbor 口径分离、测试文件保护、completion guard、`agent_version` 核对（`docs/specs/harbor-benchmark-lessons.md:18-50,187-231,233-257`）；本地基准的 config 冻结与 A/B（`docs/specs/local-coding-benchmark.md:55-63,67-69`）。理论背景：长程执行的关键是外置状态与独立审计；审计占总 token 的 19.4%–38.1%，且该研究里没有组件消融（`docs/research/longhorizon-harness-2026-10-31.md:14-23,220-231`）——所以必须记录审计成本并做对照。

## 3 缺口

| 缺口 | 当前表现 | 影响 |
|---|---|---|
| 没有决策对齐的生命周期场景 | 无 TUI 关闭后台继续、重启检测/attach、detach、两任务槽、kill switch、worker 崩溃不重跑 | 01–05 已确认的行为无法验收，回归看不到退化 |
| 没有审批过期场景 | 审批只在单测/人工验收出现；无 required 过期→`failed(approval_timeout)` 用例 | 无人值守的失败语义无法回归 |
| 没有长任务指标 | 只有单 run 的 steps/tokens/cost/status | 完成率、verified 率、后台存活率、重跑与重复副作用无法比较 |
| 成本口径不闭合 | 跑分/会话各算；goal 总额在 TUI；审计不计父预算 | 跨段成本不可审计，超支无法在评测中拦截（依赖 06 §8 定稿） |
| 没有对照组与阈值 | 本地基准 A/B 只覆盖单 run；后台默认开启无回退对照 | 「默认开后台是否值得」只能靠感觉 |
| 没有漂移门槛 | `done` 但 `verified=false`、测试被改、已执行段被重跑都可能混入 | 长跑越久越容易「假绿」 |
| 不可重放 | 无 seed/时钟/故障计划版本 | 无法 CI 化，也难以判断两次运行同条件 |

## 4 设计选项与推荐

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A 扩 `bench_local.py` | 在现有 runner 加长场景与故障步骤 | 复用 workspace/RED/防篡改/`verified_fix`/config 冻结 | runner 语义是「单次注入修复」：固定 `red.sh`→Agent→`check.sh`，`--pause-tokens` 写死一次 resume；生命周期场景会把稳定回归床复杂化 | 不选为主方案 |
| B 新 `scripts/bench_long.py` + scenario 配置 | 独立场景目录与 runner；复用 workspace/哈希/防篡改/报告 helper；场景声明运行时、预算、故障、期望、成本上限 | 语义清晰；可 fake 先跑；可按决策扩场景；不污染本地基准 | 新增脚本与场景维护成本 | **推荐** |
| C 纯人工验收 | 沿用 W2/W3 的「脚本引导 + 人操作 + 取证」 | 真实终端与模型体验 | 不可重放、无门槛、无法统计 | 不推荐（仅作 P1 体验补充） |

推荐 B：长任务的核心是**生命周期、恢复与后台语义**，与单次修复评分模型不同；故障注入要可版本化；成本必须硬封顶。时间用「低预算强制分段」压缩：真实 2–8 小时的任务用较小每段预算（如 `max_run_seconds` 180–300）在分钟级复现多段，报告同时记「段数」与折算时长，**不把压缩场景墙钟当真实时长**。

```jsonc
// benchmarks/long/<id>/scenario.json（草案）
{
  "id": "detach-continue-3seg",
  "kind": "goal",                       // goal | interactive | cancel
  "runtime": {"task_runtime": "python", "background_default": true, "task_slots": 2},
  "goal": "修复 X 并让全部测试通过",
  "budget_profile": {"max_run_seconds": 180, "max_run_tokens": 200000,
                     "max_segments": 4, "compact_interval_tokens": 50000},
  "faults": [{"at": {"tui": "detach"}, "action": "detach"},
             {"at": {"step": 6}, "action": "kill9"},
             {"at": {"on_approval": 1}, "action": "approval_timeout"}],
  "expected": {"terminal": "done", "verified": true, "min_segments": 2,
               "max_rerun_executed_segments": 0, "max_duplicate_side_effects": 0},
  "cost_cap_usd": 0.8,                  // 占位；额度口径待 06 §8 定稿
  "seed": 7
}
```

**场景集**（P0 起至少覆盖以下 8 类；括号内为期望）

| 场景 | 触发/注入 | 期望 |
|---|---|---|
| detach-continue | TUI 关闭并确认「转入后台」 | 任务继续，最终 done+verified；无重复副作用 |
| restart-attach | TUI 重启/启动扫描到运行中任务 | 提示 attach/resume/cancel；attach 后状态一致；不自动重跑已执行段 |
| approval-timeout | required 审批挂起超过任务级 deadline | 任务 `failed`（reason=`approval_timeout`）；动作不执行；pending 清理并审计 |
| two-slot-write-serial | 同时两个后台任务（1 写 + 1 读） | 写任务串行、只读可并发；无交叉写；单写者规则可验证 |
| kill-switch | CLI `vibe task stop` 或 `.vibe/stop` | worker 在边界检查；SIGTERM→宽限→SIGKILL；状态 `cancelled` + 审计 |
| crash-no-rerun | worker 中途 kill -9 | 已执行段不自动重跑；仅未执行动作可在用户确认后续做；无重复副作用 |
| goal-criteria-pending | `acceptance_criteria` 未经用户确认 | 任务暂停等确认；未确认候选不参与完成判定 |
| no-duplicate-effects | 所有恢复/重启场景的横切断言 | 文件 hash、命令执行、审批决定均无重复 |

**指标**（报告 JSON + Markdown）：完成率；`verified` 率；未验证漂移（done 但 `verified=false`）；段数/续段数；后台存活率（detach 后完成）；重启检测与 attach 成功率；审批过期→失败率；写串行违规次数；读并发利用率；kill switch 生效延迟；已执行段重跑次数；重复副作用数；`control_taken` 次数；人工干预（steer/deny/manual resume）；成本（每 goal/段、预算利用率、审计成本占比）；时间（墙钟/active/审批等待分列）；可重复性（同 seed、config 冻结、`agent_version`）。

**故障注入**：TUI 关闭/detach（区别于 cancel）、supervisor 被杀、`.vibe/stop`、provider 5xx/超时、审批迟到/拒绝/过期、断网、磁盘满/会话保存失败、外部工作区改动、worker 中途崩溃、两任务同 workspace 争用、goal criteria 未确认。每类都要求「无重复副作用 + 状态可归因」。

**对照与阈值**（建议值，需拍板）：确定性 fake 场景 100% 通过作为门禁；恢复类重复副作用 = 0、已执行段重跑 = 0、悬空工具调用 = 0；真模型 P1 相对基线完成率与 `verified` 率不下降、人工干预不增加、成本增幅 ≤1.2×。对照组：A=旧行为回退（`VIBE_TASK_RUNTIME=0`：TUI 关闭=取消、无后台默认），B=后台默认开启 + TaskRuntime（01/02 §9）；成本 cap 额度与时间口径引用 06 §8 待确认项，不假设已拍板。基线先取现状 `/goal`，02 supervisor 落地后加对照。

## 5 最小实现切片

| 阶段 | 内容 | 修改点 | 验收 |
|---|---|---|---|
| P0 fake/离线 | 上表 8 类场景各 1 个，每场景 ≥2 段；`bench_long.py` 支持 `--scenario --runs --fake --cost-cap`；故障注入器（detach/kill9/SIGTERM/provider stub 5xx/审批过期/`.vibe/stop`）；指标与报告 | 新增 `scripts/bench_long.py`、`benchmarks/long/<id>/{scenario.json,seed.sh,check.sh}`；复用 `scripts/bench_local.py` 的 workspace/hash/防篡改/报告 helper；依赖 01 最小 task API 与 02 worker，未落地前用 headless 驱动模拟 detach/重启（标注为过渡） | 同 seed 重跑 ≥3 次结果一致；detach 后继续；崩溃恢复无重复写/悬空工具；审批过期按 03 语义失败；kill switch 生效；超 cap 能中断 |
| P1 真模型小规模 | 3 个真实仓库任务（≥2 段）、每场景 2–3 次；记录 config/预算/`agent_version`；与基线 `/goal` 及回退模式对比分布 | 场景夹具与报告；成本 cap 与人工确认 | 报告含完成率/verified/后台存活/恢复/干预/成本；无收益则停止（不扩场景集） |

测试点：场景 schema 校验；故障计划可重放（同 seed 同 trace 摘要）；段决策与 01/05 规则一致；恢复后副作用去重；审批过期与 kill switch 的终态/审计正确；报告字段完整；fake 结果不得标注为模型能力。明确不做：不做 Harbor 500 题的长跑组合；不做 GUI/桌面评测；不做子 Agent 的 A/B（16 负责，本块只给框架）；不把 fake 场景通过当作能力证明。

## 6 与其它块的关系

| 块 | 关系 |
|---|---|
| [01 任务运行时](01-task-runtime.md) | 场景需要 TaskRuntime 身份/生命周期；01 未落地时 P0 用 headless 驱动过渡，指标口径不变 |
| [02 后台进程监督](02-process-supervision.md) | detach/后台继续、重启检测、worker 崩溃由 02 实现；本块的 kill/重启/attach 场景就是 02 的验收用例 |
| [03 无人值守审批](03-unattended-approval.md) | approval_timeout→`failed`、pending 清理与 `approval_id` 一次性由 03 定义；本块验证终态与审计 |
| [04 隔离与并发](04-isolation-concurrency.md) | 写串行/读并发、worktree、kill switch 由 04 定义；本块用两任务槽场景验收 |
| [05 目标状态](05-goal-state.md) | `goal-record.json` 与已确认 `acceptance_criteria` 决定完成判定；本块验证「未确认不参与完成」 |
| [06 预算账本](06-budget-ledger.md) | 成本 cap 与记账口径依赖 06 §8 的待确认项；本块不假设已定稿 |
| [07 控制面](07-control-plane.md) | attach/事件流是 P0 报告与故障注入的观测通道；无 07 时只能用 trace/rounds 事后重建 |
| [15 计划验收](15-plan-verification.md) / [16 子 Agent](16-subagent-integration.md) | 完成验证门是 `verified` 指标前置；16 的 A/B 复用本块 runner 与指标 |

## 7 与子 Agent 的关系

| 维度 | 本文结论 |
|---|---|
| A 谁发起 | 评测运行由 harness 配置或用户显式触发，不让模型获得新能力；模型工具调用型 `delegate` 属于 16 的 treatment，本块只负责跑它、量它 |
| B 能力边界 | 场景声明 `capability_profile`：只读 / 独立 worktree 写 / 共享工作区写（首版不纳入）；评测按 profile 构造工具子集与审批边界 |
| C 结果可信度 | 子 Agent 报告是 untrusted claim：完成率只按父侧证据（`verified`/审计/测试）计，不按自述计；A/B 的「有效」必须来自父侧 verify 门 |

现有原型是只读审计子 Agent（`app/session/auditor.py`），规格见 [multi-agent-computer-use](../../specs/multi-agent-computer-use.md) §3.5/§5；本块把它当作「harness 内部发起」的对照基线，不当作已验证事实来源。

## 8 开放问题与验收标准

待拍板：
1. 「长」怎么定义：用低预算压缩出的多段运行，还是真实 ≥2 小时的少量样本？两者如何折算（段数/预算/墙钟）？

回答：长时任务，按照我的理解就是任务复杂度够高，无论是用户使用plan模式造成的，还是任务特别复杂都能持续执行的任务。 这个需要你给出专业建议

2. 恢复成功怎么算：重启后 `done + verified` 且无重复副作用即可，还是必须无人工介入？人工 `/resume` 是否计入失败？

回答：`done + verified` 且无重复副作用即可。产品层面， 任务已经交付，用户自行判定是review还是其他。

3. 对照组范围：以「后台默认开启 + TaskRuntime」为 treatment、`VIBE_TASK_RUNTIME=0` 回退为 control 是否成立？范围仍按 01 §9 待确认。

回答：这部分的影响是什么 我不太清楚

4. 哪些指标进门禁：fake 100%、重复副作用 0、成本 cap 是否阻塞合并？由谁拍板阈值？

回答：这部分请你给出建议 我没有很好的想法

5. 场景来源与污染：用本仓库真实任务还是合成故障任务？成本 cap 的额度与时间口径等 06 §8 定稿。

回答：合成故障任务


验收条件：
- 8 类场景覆盖 detach/重启/审批过期/两任务槽/kill switch/崩溃不重跑/goal criteria/无重复副作用，每场景 ≥2 段。
- fake 场景同 seed 重复 ≥3 次，结果与副作用 hash 完全一致。
- 报告含完成率、`verified` 率、未验证漂移、后台存活率、恢复与干预、成本/时间/段数、可重复性。
- kill -9 与 detach 场景：无重复写、无悬空工具、不重跑已执行段，`verified` 判定正确且证据可回查；成本 cap 生效并引用 06 定稿口径；P1 真模型 ≥3 场景 ×2 次并记录 config/预算/`agent_version`；无收益不扩。

## 9 决策对齐（2026-11-03）

**受影响的已确认决策**：D1 任务运行时/后台默认开启（[01 §9](01-task-runtime.md)）、D2 worker 与 attach（[02 §9](02-process-supervision.md)）、D3 审批过期→任务失败（[03 §9](03-unattended-approval.md)）、D4 单写者/kill switch（[04 §9](04-isolation-concurrency.md)）、D5 GoalRecord 与验收标准（[05 §9](05-goal-state.md)）；06 预算账本仍未定稿。

**本块已按决策修改**
- 场景集扩到 8 类：detach-continue、restart-attach、approval-timeout、two-slot-write-serial、kill-switch、crash-no-rerun、goal-criteria-pending、no-duplicate-effects（§4 表）。
- 指标增加：后台存活率、重启检测/attach 成功率、审批过期失败率、写串行违规、读并发利用率、kill switch 延迟、已执行段重跑次数、重复副作用、`control_taken`；故障注入补：TUI detach（非 cancel）、supervisor 被杀、`.vibe/stop`、审批过期、worker 崩溃、两任务争用（§4）。
- 对照组写默认开启配置与 `VIBE_TASK_RUNTIME=0` 回退；成本 cap 明确引用 06 §8 待确认项，未假设已拍板（§4/§8）。
- `goal-record.json` 与 `acceptance_criteria` 对齐 05；后台/attach/审批语义在 §2/§6 按 01–04 §9 引用；全部 `path:line` 按分域重构核对。

**仍需要用户 review**
- §8 的「长」定义、恢复成功口径、对照组范围、门禁指标与阈值、场景来源均未拍板。
- 成本 cap 的额度、时间口径（active vs wall clock）与漏记成本处理等 06 定稿后再定。

## 10 用户回答处理（2026-11-03）

用户回答原文见 §8。处理结果：

**已确认**
- Q2：恢复成功 = `done + verified` 且无重复副作用；人工 `/resume` **不计入失败**，作为“人工介入”单独报告。
- Q5：以**合成故障任务**为主（可复现、可注入故障），不把本仓库真实任务当唯一来源。

**已确认（2026-11-03，采用 Lead 建议）**
- Q1 “长任务”操作定义：任务需要 **≥2 段**（至少一次资源/上下文停止并继续）**或 ≥1 次上下文压缩**，或 wall-clock ≥30 分钟；复杂度导致的多段即“长”，不要求真实 ≥2 小时。评测主用低预算压缩出的多段合成场景 + 1–2 个真实模型确认样本，报告同时给段数、tokens、wall。
- Q3 对照组影响：treatment=后台默认开启 + TaskRuntime，control=`VIBE_TASK_RUNTIME=0` 回退（同模型/同任务）。它测的是“整套行为变化”，不是单因素因果；只有 runtime 落地后才有意义；若 treatment 无优势，不得宣称长任务收益。
- Q4 门禁建议：
  - **Blocking（fake）**：所有场景通过、重复副作用 0、恢复状态一致、kill switch ≤2s、无孤儿进程、`approval_timeout` 语义正确；
  - **Advisory（真模型）**：完成率、`verified` 率、成本/时间——先建基线，阈值固定在 scenario config 并按 `agent_version` 比较；成本 cap 等 06 定稿后加入；
  - 阈值拍板人=用户（Lead 在基线出来后给建议值），不在实现中自行放宽。

**实现备注**：采用“多段/压缩/30min”定义与 blocking/advisory 门禁；阈值先按建议作为初始基线，真实数值随基线调整（实现细节，不改变口径）。
