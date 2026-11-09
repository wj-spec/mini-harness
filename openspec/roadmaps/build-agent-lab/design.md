# Agent 实验台：观测、版本与评测设计

> 版本：v2，2026-11-08。状态：规划方案已复核，待实施规格；功能尚未实现。
>
> [实施路线图](roadmap.md) · [任务与评审记录](task-log.md)
>
> 适用对象：mini-harness 的个人开发与使用。本文是配套实验台的目标设计，现有能力以源码和 [MILESTONES](../../../docs/MILESTONES.md) 的验收证据为准。

**2026-11-08 用户反馈增量**：首版结果阅读原型不足以支撑真实业务，当前明确优先定位实际编码任务的失败、反复和高消耗。
补充 [业务旅程与诊断边界](business-workflows.md)，并以三个真实历史样本重做诊断原型。下一版入口围绕任务结果、请求/工具/上下文、证据假设与最小验证；版本对比和双榜评测承接后续验证。
这是 v2 复核后的设计输入，尚未纳入独立实施评审，不改变下文 P1–P7 的未实施状态。

## 需求与目标态

### 痛点与受众

Owner 需要持续改善 coding Agent，并能解释每次改动的收益与代价。现有运行记录和评测入口分散，难以从真实失败追到实际版本，再以相同条件验证修复。Git 提交、任务结束、一次评测分数分别只能回答部分问题。

首要用户是本项目开发者；AI 助手消费同一份机器可读证据辅助分析。产品成功取决于一次优化能否形成可核查的闭环，而非收集的事件数或面板数量。

主要外部能力标尺是 **SWE-bench 与 Terminal-Bench**。内部小题集服务快速自检，真实使用回归服务问题防复发；两者与主基准各有用途，不能替代主基准的运行、评分与对照。

### 目标态与验收门槛

| 需求 | 优先级 | 可观察的目标与验收方法 |
|---|---|---|
| R1 解释运行 | 必须 | 从一个任务定位各段运行、实际配置、关键动作、代码变更和验证证据；缺失记录明确显示未知 |
| R2 标识版本 | 必须 | 区分 Agent 实现、被操作仓库、题集和评分器；同一 HEAD 下的不同有效改动不会被合并成同一版本 |
| R3 对照实验 | 必须 | 固定条件比较基线与候选，逐题展示改善、退步、成本与不可比原因，保留所有尝试 |
| R4 失败转回归 | 必须 | 将一个真实失败整理成独立案例；重建初始状态、确认失败、验证修复，再加入题集新版本 |
| R5 管理基线 | 必须 | 基线引用明确版本、协议及完整证据；后续回归可定位到具体案例并恢复此前的运行配置 |
| R6 阅读与分析 | 后续必须 | 通过本地工作台串联运行、版本对比、案例和实验；人工诊断与机器事实分别展示 |
| R7 外部集成 | 按需 | 能选择导出到观测后端；关闭后仍可完成本地闭环 |
| R8 主基准评测 | 必须 | SWE-bench 与 Terminal-Bench 各自完成固定版本运行、原生评分导入、逐题对照和证据追溯；子集与全量、不同版本分别显示 |

端到端产品验收：选择一个实际遇到的 coding 失败，完成“取证 → 案例 → 修复 → 对照 → 回归 → 基线”，展示每个结论对应的原始证据。一次演示通过仅证明流程可用，不能代替模型能力的统计证据。各阶段独立验收范围见路线图。

### 范围与不做的事

- 做：单人、本地优先的任务证据、版本身份、实验比较、案例管理与基线记录。
- 暂不做多租户 SaaS、账号/计费和远程协作：目前只有个人使用需求；出现第二位独立使用者或跨机器协作瓶颈时重评。
- 暂不做自动修改提示词并自动发布：尚无经过验证的评分和回滚依据；积累可信基线后再评估。
- 两个主基准分别对标，不合成一个跨榜综合分；正式榜单发布由用户另行发起并遵循当时资格与审核规则。需要正式上榜时补齐提交材料与资格核对，不把发布纳入默认运行副作用。
- 暂不实现逐 token 重放或承诺模型结果完全重现：保存环境和输入不能冻结远端模型；出现确定性本地推理需求时另评。
- 本轮不创建新仓库、不部署服务、不运行付费评测；这些属于后续实施工作。

## 1. 从第一性原理推导

一次可观察结果可表述为：

`结果 = F(Agent 实现, 模型与参数, 有效指令与上下文, 工具与权限, 初始仓库, 运行环境, 人工干预, 随机因素)`。

一次评测结论还依赖 `题集 + 评分器 + 实验协议`。因此：

| 基本事实 | 推导出的设计约束 |
|---|---|
| 优化需要比较前后结果 | 每个实验先声明假设、实验变量和保持不变的条件 |
| 不同原因可以产生相同分数 | 保留逐题结果和过程证据，不能仅存总分 |
| 记录缺失无法靠展示补回 | 区分事实、派生指标、人工标签和推测；缺失不能补成成功或零消耗 |
| 代码与上下文共同决定行为 | Git commit 之外记录实际生效输入；哈希与可恢复快照分别管理 |
| Agent 会写文件和执行命令 | 阅读历史与重新执行分离；执行仍经过 ApprovalGate 和现有隔离边界 |
| 真实模型具有波动 | 预先定义重复次数、停止条件和失败处理，保留所有尝试 |
| 观测是辅助能力 | 观测失败不能更改权限或任务结果；评测证据缺失则阻止生成可信比较结论 |
| 优化时间与评测预算有限 | 先覆盖高频失败与关键不变量，再根据证据扩题和接平台 |

## 2. 现状与架构边界

### 2.1 现状核查（2026-11-08）

本轮工作树含其他未提交工作；以下是已读取源码的结构事实，不是全量测试结论，也不把 Git HEAD 等同于工作树。

| 已有能力 | 事实入口 | 本项目要补齐的部分 |
|---|---|---|
| run/LLM/tool/approval/usage 事件 | [trace.py](../../../app/session/trace.py)、[agent.py](../../../app/runtime/agent.py) | 跨来源统一身份、实际运行快照与证据完整性 |
| 会话日志、轮次与完成证据 | [store.py](../../../app/session/store.py)、[evidence.py](../../../app/session/evidence.py) | 跨 run 聚合与证据导航 |
| trace 聚合 | [observability.py](../../../app/session/observability.py) | 关联版本与评测结果，区分不可得的统计 |
| 内部 eval 的协议指纹和独立检查 | [internal.py](../../../app/eval/internal.py) | 固定执行者版本，明确实验变量并比较候选 |
| 本地基准的隐藏检查、多次运行、续做成本 | [bench_local.py](../../../scripts/bench_local.py) | 完整快照、统一协议身份与案例生命周期 |
| SWE-bench 源码哈希、数据 revision、评分器信息 | [swebench.py](../../../app/eval/swebench.py) | 纳入统一索引；不能用公开开发子集代替完整榜单 |
| 长任务历史 | [history.py](../../../app/eval/history.py) | 当前按 mode/scenarios 分组；补模型、环境、评分器等比较约束 |
| Harbor 容器内自定义 Agent | [adapter.py](../../../bench/adapter.py)、[bench README](../../../bench/README.md) | 已有 BaseInstalledAgent、telemetry 与 patch 导出；需补主基准身份、试次映射、版本兼容和轨迹材料 |
| Verified 评分辅助与历史运行记录 | [补丁收集](../../../scripts/collect_predictions.py)、[报告汇总](../../../scripts/bench_report.py)、[经验记录](../../../docs/specs/harbor-benchmark-lessons.md) | 历史记录含官方 harness 三题结果；本轮未复跑，不认定完整 Verified 或 Terminal-Bench 已验收 |
| OTel 分阶段设计 | [OTel 规格](../../../docs/specs/otel-observability.md) | 尚未实现；作为可选投影出口，不是实验台事实源 |

### 2.2 当前与目标数据流

```text
当前：Ink → Python Agent → session/trace/rounds/budget
      多个 eval runner → 各自报告 → 各自历史查询

目标：Ink → Python Agent → 本地原始证据
                          ↓ 采集/封装适配器
      既有 eval runner → 版本化证据包 → 导入校验 → 可重建索引
                                                ↓
                              运行详情 / 实验比较 / 案例 / 基线
                                                ↓ 可选
                                         OTel / 外部平台

      显式实验请求 → 既有 runner → ApprovalGate → 独立工作区 → 独立评分
```

### 2.3 组件及归属

| 组件 | 职责 | 部署/技术边界 |
|---|---|---|
| 采集与封装适配器 | 捕获身份、引用本地事实、封装证据 | mini-harness `app/session/`；runtime/入口注入配置，不反向 import runtime |
| 评测适配器 | 冻结实验条件、调用既有 runner、转换报告 | `app/eval/` 与现有 scripts；日常运行不能 import eval |
| 主基准执行与评分桥接 | 关联 Harbor job/trial、SWE-bench prediction/evaluation、原生结果 | 复用 `bench/adapter.py` 和官方工具；Harbor 为可选评测依赖，不进入 core 或日常启动 |
| 证据协议 | 定义版本、对象身份、完整性与错误语义 | 由生产端维护协议及兼容夹具；消费者只能按协议读取 |
| 导入与查询服务 | 校验包、建立索引、计算派生视图 | 实验台独立本地进程；首选 Python + SQLite，依赖在实施时锁定 |
| 本地工作台 | 证据阅读、比较、案例编辑与实验记录 | 配套 Web 界面，候选 TypeScript/React；不替代 Ink 的 Agent 操作入口 |
| 外部观测适配器 | 将已落盘证据投影为外部 trace/score | 可选，遵守既有 OTel 的默认关闭、隐私及 bench 边界 |

## 3. 架构决策

### 决策 1：独立实验台，通过证据协议连接

**选择**：按配套项目设计，先在本仓保存规划与生产端协议；消费者不直接 import mini-harness 私有模块。独立仓库的实际创建和技术栈细化留到本地工作台阶段。

| 候选 | 系统代价 | 用户体验 | 开发循环 |
|---|---|---|---|
| 全部嵌入 Ink/后端 | 少一个进程，但观测与运行耦合 | 入口集中，跨版本表格受终端限制 | 每次 UI 演进触碰主工作台 |
| 独立本地实验台 + 稳定证据协议（采用） | 多一个协议和索引，可单独替换 | 可跨会话阅读与比较 | 生产端变动小，协议需双端夹具验证 |
| 平台全面托管 | 依赖其数据模型与服务 | 通用 trace/实验界面现成 | 仍需定制仓库、权限、评分适配 |

当前以开发循环和结果可信度为主：先稳定跨版本证据，接受额外的协议维护成本。已有 harness 正在演进，本规划是配套能力演进，不初始化整套新工程工作流。

### 决策 2：事实、索引、判断分别保存

**选择**：本地日志及不可变证据包保存事实；SQLite 保存可重建索引；人工注释和实验决定保存为带作者/时间/证据引用的独立记录。备选“直接把数据库当唯一源”恢复复杂；“完全扫描文件查询”可以作为 P1 只读入口，达到实测瓶颈再加索引。

开发循环优先：原始数据可以重新解释和迁移，不必为展示变化重跑模型。代价是需要管理引用和保留策略。

核心对象的最小合同如下；这是目标协议，字段精确 schema 在 P1 change 冻结。

| 对象 | 身份与核心字段 | 生命周期 |
|---|---|---|
| RunManifest | `schema_version:int`、`bundle_id:string`、`source_namespace:string`、`session_id:string?`、`task_id:string?`、`run_id:string`、`segment_id:string?`、`parent_run_ref:object?`、`agent_revision:object`、`target_revision:object`、`effective_config:object`、`started_at:string` | 启动时冻结；运行终态写单独 Result，不能回填改写起始事实 |
| EvidenceArtifact | `artifact_id:sha256`、`kind:string`、`relative_path:string`、`size_bytes:int`、`capture_status:enum`、`redaction_version:string?` | 只引用包内文件；复制或清理产生新记录，不能静默改正文 |
| RunResult | `run_ref:object`、`execution_status:string`、`stop_reason:string?`、`integrity:enum`、`artifact_refs:list`、`usage:object` | 追加；缺终态保持 incomplete，不能从最后一句回答推断成功 |
| CaseRevision | `case_id:string`、`revision:sha256`、`source_run_ref:object?`、`input_ref`、`fixture_ref`、`grader_ref`、`environment_ref`、`tags:list`、`exposure:enum` | 草案→可重建→启用→退役；内容变更生成新 revision，退役不删除历史 |
| SuiteRevision | `suite_id:string`、`revision:sha256`、有序 `case_revisions:list`、`split:string` | 成员/顺序/分组变化生成新版本 |
| Experiment | `experiment_id:string`、`hypothesis:string`、`variants:list`、`controlled_conditions:object`、`protocol_revision:sha256`、`planned_attempts:list` | 运行前冻结；补跑另建实验并引用此前实验 |
| Assessment | `attempt_id:string`、`grader_revision:sha256`、`validity:enum`、`outcome:enum`、`reason_codes:list`、`evidence_refs:list` | 与运行结果分离；重评分生成新记录，原评分保留 |
| Baseline | `baseline_id:string`、`agent_revision_ref`、`suite_revision_ref`、`protocol_ref`、`experiment_ref`、`decision_ref` | 人工选定；替换保留 predecessor；不自动发布代码 |

现有 run_id 不是全局身份。外部引用用 `(source_namespace, session_id, run_id)`；无会话来源由适配器明确提供 namespace，不能按时间猜关联。一次任务可以对应多段运行，父子关系未知则留空。正文未知字段保留，但未支持的主 schema 版本拒绝参与比较；迁移产生新投影版本，原包不改。

### 决策 3：版本身份与可复现程度分别表达

**选择**：固定四类版本：执行者、任务输入/目标仓库、题集、评分协议。执行者内容指纹覆盖源码、依赖锁定信息、实际生效配置和指令引用；目标仓库另存初始状态与最终 patch。只记 Git HEAD 作为便宜备选会误合并脏工作树；完全复制所有本机文件成本与隐私代价过大，采用明确清单内的内容快照。

版本记录需要同时区分：

- `identity_known`：知道使用了什么内容；仅有哈希也可能成立。
- `rebuildable`：依赖的源码、初始文件、指令与环境材料可取回且完整；只有哈希不成立。
- `rerunnable`：环境和必要凭据当前可用；凭据通过外部配置注入，不进入包。
- 模型结果可重复性：另记 provider 标识、返回的 revision/fingerprint（若有）、请求参数与时间；不宣称 bit-for-bit 重现。

**采集时机**：日常 run 启动时冻结有效配置和已加载指令，之后动态载入的 skill/指令记录时间和内容引用；结束时捕获目标仓库差异。运行中自身实现可能被修改且未隔离时标记 `execution_snapshot_unverified`，不能仅以结束时源码哈希冒充实际执行版本。正式实验先物化候选快照，再从该快照启动执行者。

未提交改动包括 tracked diff、删除、文件模式及实际使用的 untracked 文件；子模块、LFS、生成物和外部依赖按实际引用记录。缺少其中必要材料时标记 rebuildable=false。不自动 add/commit/stash 用户文件。

用户可见的每次模型调用关联实际有效的输入/工具 schema 指纹及本地证据引用。敏感正文默认不进入可交换包；仅保留摘要的运行可以用于性能观测，缺少必要输入时不能被标成完整重跑案例。以可解释性为主，接受默认包可能无法重跑的代价。

### 决策 4：证据包有明确完整性与导入语义

**选择**：JSON/JSONL 与内容摘要构成可携带证据包；临时写入后完成校验再发布索引。原始日志可能轮转或正在追加，导出须记录已读文件身份、边界和截断状态，不能伪造尚未落盘的事件。相比即时只发网络事件，本地包可离线核查；代价是实时性低于直接遥测。

- 新采集事件具有来源内单调序号和稳定事件身份；旧日志以文件身份/字节位置确定来源记录，不能仅按“内容相同”去重合法重复事件。
- 重复导入同一 `bundle_id + digest` 幂等；同 ID 不同 digest 产生 conflict，禁止覆盖。
- `integrity` 取 complete / incomplete / corrupt / legacy_unknown；无权限、缺文件、未知版本单独给出原因。
- 已完成包的文件摘要与尺寸逐一校验；缺失引用展示 missing，不计作无变更或无错误。
- 时长使用执行端单调计时，墙钟用于展示；跨进程没有可靠父子关系时只做关联，不用时间排序推断调用树。
- token 记录 provider 实测/估计来源；未知价格显示 unknown，不显示零成本。任务总量按唯一计费事件汇总，父级汇总和子级明细不能重复计费。
- 显式导入/导出失败返回结构化错误；运行内的可选观测采集失败只影响观测状态和诊断，不改 AgentResult、审批或 stdout 协议。

**保留**：基线和启用案例引用的材料进入实验台受管证据存储，再允许清理普通源日志；记录 pin/引用关系。既有 cleanup 命令尚不知道这些引用，集成阶段必须补保护或将受管包置于其扫描范围外。手工删除仍可能造成 missing，UI 不能隐藏。

### 决策 5：结果正确性由版本化独立评分决定

**选择**：机制回归、真实模型能力题、真实使用回归分层维护；fake 与 real 分别报告。以独立测试、文件合同、约束检查作为正确性依据；LLM judge 仅补充软指标，其模型、prompt、rubric 也属于评分器版本。只采纳 Agent 自述或仅看退出状态的备选不能验证代码效果。

| 结果维度 | 合同 |
|---|---|
| 执行状态 | 保留 done/stopped/cancelled/error 及原因，不直接等同评分 |
| 评分有效性 | valid / invalid / not_graded；与候选无关的环境准备/评分设施故障、隐藏测试泄露等使结果 invalid；候选造成的失败按下述归属规则判定 |
| 能力结果 | pass / fail / unknown；仅在评分有效时给 pass/fail；预算内未完成按预定协议判 fail，不因正常暂停算通过 |
| 约束 | 分别记录未经授权副作用、禁止文件变更、测试篡改等；审批拒绝本身不是缺陷 |
| 覆盖 | 计划次数、实际启动数、有效数、无效数、取消数、缺结果数同时呈现；空题集返回 no_data |

无效运行不进入有效样本成功率，但必须报告有效样本数/计划数；存在缺测时不能据剩余题目自动晋升基线。人工取消不计为已完成；审批拒绝后如何交付由案例事先定义。运行内验证是过程证据，最终 grader 是单独信任域。

**故障归属**：在协议支持且 preflight 通过的环境中，候选破坏依赖、无法构建或产物执行失败属于能力 fail；不能因评分子命令非零就改记 invalid。评分设施自身失败才记 invalid，并附环境/评分日志。无法区分候选故障与设施故障时保留 invalid，禁止基于剩余样本作改善、采用或基线晋升结论。报告同时列出两边都有效的配对数/计划配对数及未配对原因；分母变化只能描述，不能被解释为能力提升。

评分器和隐藏材料放在 Agent 可写、可读工作区之外，评分在干净重建环境中应用最终产物；拒绝以修改 grader/测试获得通过。独立 worktree 仅隔离版本，并不是安全沙箱；敏感隐藏题需实际的文件/网络访问隔离，未建立时标记可信度限制并禁止当成无污染保留集。

修 bug 类案例须确认起始失败和参考修复可通过；新功能类使用合同不满足/满足的前后检查，不强套 RED 规则。真实失败提取须人工核对隐私和隐藏答案，再固化环境；原始 trace 只是来源，不能直接当作完整案例。

题目按 dev / regression / held_out 管理，记录已曝光历史。保留题一旦用于定向调优就退出保留集并产生新 suite revision；不能靠改标签抹掉曝光。建立不同任务族和外部仓库案例，缓解只测自身已知修复的偏差。

### 决策 6：对照实验先声明可比条件

**选择**：比较协议列出允许变化的维度，其余有效条件保持相同。只比较同一指纹无法比较被刻意修改的代码；任意两个总分直接相减又会混入题集和模型变化，所以采用“实验变量 + 控制条件”的显式合同。

1. 运行前固定假设、候选快照、题目/评分器版本、模型参数、预算、环境、批准策略、并发度、重复次数、顺序和停止规则。
2. 默认单维变化；一次包级代码改动含多个行为变化时只归因于整个候选包，不能宣称证明某条 prompt 有效。
3. 按 case revision 配对；题目、grader 或关键环境变化时不给直接差值。历史记录缺必需字段返回 incomparable；可以并排阅读。
4. 同题可先预定每版本 3 次用于发现明显波动；这不是显著性保证。扩大样本须新实验，不能看到结果后只补有利样本。
5. transport/tool 重试属于同一尝试、计入成本；重新启动完整任务是新 attempt。断点续做仍关联原任务，预算和成本跨段累计。
6. 报告逐题胜/负/持平/未知、按任务族汇总、每次尝试结果和成本分布，展示配对有效覆盖和所有未配对原因；候选把失败题变成 invalid 时不得报告总体改善。禁止以 best-of-N 冒充单次成功率；区间估计方法在协议中固定，样本不足直接标注。
7. 成功率分母按决策 5；成本同时报告全部尝试总消耗和成功样本分布。近零基线不算百分比涨幅；时延区分模型、工具、审批等待。
8. 运行前核算 `候选数 × 题数 × 重复次数 × 单题上限`，批准最大调用/时间/token 预算；未配置价格时不承诺美元预算。无剩余额度停止新尝试，不自动扩预算。

结果可信度优先于自动化程度。接受早期人工选择实验和判断采用决定，后续以校准后的协议定义回归门禁。

### 决策 7：本地工作台只读运行，执行走既有边界

**选择**：运行页展示任务时间线、diff、验证与来源；对比页展示变量差异和逐题结果；案例页维护案例版本；实验页记录假设、尝试与结论。案例编辑与基线选择是实验台元数据操作，不能修改历史事实。

首个 Web 阶段只读 Agent 运行状态；需要发起实验时走专用请求、固定 runner 及 ApprovalGate。浏览历史不得重放工具调用、执行报告里的命令或把缺失批准视为允许。相比在 Web 内复制一套 Agent 控制面，此边界减少双端语义漂移，代价是日常控制仍在 Ink。

Langfuse 提供 trace/score/dataset/experiment 关联与题集版本；Phoenix 提供基于 OTel 的 tracing 和数据集实验。它们可承担通用视图，但不能替代本项目的仓库快照、独立 grader 和权限合同。后端选型延后到真实包适配试验，依据见参考资料，不预承诺 SDK/OTLP 接入已经兼容。

### 决策 8：遵循仓库的切片开发和证据验收

**选择**：每阶段先形成行为规格，再写失败测试、实现、评审和学习记录。依据 [协作协议](../../../docs/VIBE-PROTOCOL.md)、[AGENTS](../../../AGENTS.md)、[app 模块边界](../../../app/README.md)、[core 模块边界](../../../core/README.md)。实验台是验证开发的辅助工具，不能自行把自己的评分认定为人工验收完成。

- `core/` 继续只依赖标准库，不因观测引入第三方包，也不反向依赖 app。
- 生产端落盘/遥测适配在 app/session；评测编排在 app/eval；共享接线在现有入口，不新建 app 根级业务模块。
- 行为修改先加/改测试；提交前跑 `./run_tests.sh`。Ink 改动增加适用 smoke；真实模型 eval 按已授权预算单独记录。
- 每个切片记录自动验证、真实模型结果、人工使用验收三种状态；用户的 diff review、mutation 和学习笔记遵循既有协议，不由助手代签。
- 新命令/协议/文件落位同步 README、USAGE、STRUCTURE 与相关规格；只改文档时做链接、引用、差异检查，不把它写成产品测试通过。

### 决策 9：两个主基准复用原生执行与评分，实验台管理证据和比较

**选择**：SWE-bench 和 Terminal-Bench 均作为必需能力。SWE-bench 优先建议 Verified；Terminal-Bench 的目标版本由用户选择，协议不使用浮动 `latest`。现有仓库提及 2.1，可作为试点候选，但不能当作用户已选定版本，也不代表当前官网主榜。实现前锁定数据 revision、runner 与 grader 版本。

| 候选 | 系统代价 | 用户体验 | 开发循环 |
|---|---|---|---|
| 自建容器执行与重写评分 | 最大，可控性高但易偏离上游 | 单一入口 | 每次上游变更都要维护评分正确性 |
| 复用 Harbor/官方 harness，实验台统一证据（采用） | 增加薄适配与格式版本管理 | 同一界面看两榜，保留原生解释 | 复用现有 adapter，以官方评分结果作交叉验证 |
| 只展示外部榜单/总分 | 成本低 | 能看排名，无法解释本项目变化 | 无法重跑、查错和关联版本 |

当前以评分可信度和复用为主，接受各基准原生环境的差异及维护两条适配链的代价。

#### 主基准合同

| 项目 | SWE-bench | Terminal-Bench |
|---|---|---|
| 能力侧重 | 真实仓库 issue 的代码修复 | 容器内终端任务的端到端完成 |
| 首个版本 | 建议 Verified；Lite/dev 仍可作为独立 smoke 配置 | 精确版本待选择；2.0、2.1 和官网后续版本不得混算 |
| 执行 | 冻结 mini-harness 候选后复用 Harbor 安装型 Agent，或已有独立 runner | Harbor 运行本项目自定义 Agent，按任务配置执行 |
| 主评分来源 | 固定版本官方 `swebench` harness 对 prediction patch 评分 | 固定任务 revision 对应的 Harbor verifier 原生结果 |
| 必留证据 | 原始 patch、实际送评 patch、predictions、instance report、测试输出、轨迹、环境身份 | job/trial 配置、任务 revision、reward、异常、verifier 输出、轨迹、环境身份 |
| 主指标 | 官方 grader 的 resolved 与规定分母；任务/仓库分布、成本为辅助 | 所选版本规定的 reward 聚合和试次协议；逐题试次分布、成本为辅助 |

SWE-bench 的 Harbor reward 作为诊断视图保存，不能自动升级为官方 grader 成绩。两者不一致时显示 score_disagreement 并保留各自证据，不挑较高值。Terminal-Bench 的 Harbor verifier 则是其原生评分链；不能把 SWE-bench 的来源规则机械套用到它。

```text
Experiment + 固定候选 + BenchmarkProfile
  ├─ SWE-bench: Harbor/runner → 原始 patch → 明示转换 → predictions
  │                                                ↓ 官方 swebench grader
  │                                          原生报告 + 标准化索引
  └─ Terminal-Bench: Harbor job → task/trial → verifier reward + 原生报告
                                                    ↓
                                    关联 RunManifest → 实验台比较
```

`BenchmarkProfile` 至少包含 family、variant/version、dataset_revision、split、冻结的 task IDs、selection_hash、任务/镜像摘要、架构/资源/网络限制、runner_version、grader_revision、adapter_revision、patch_transform_revision（如有）、attempts_per_task、预算与原生聚合规则。实验、job、trial/instance、attempt 与本地 run 以显式映射关联，不能从目录名或时间猜测；两个候选的同一任务多次试验按预定 attempt 编号配对。

**原生分数与诊断统计分栏**：原生分数按该基准固定版本的分母、错误与超时处理、聚合公式原样保留；决策 5 的“有效样本成功率”只能作为诊断统计，不能冒充榜单分数。预定全量有缺题、oracle 失败或基础设施错误时，仍显示全部预定任务及原因，不通过删题缩分母获得全量成绩。

**保留完整产物链**：现有补丁收集脚本默认跳过空 patch、过滤测试文件块，且从 job 遍历收集；正式适配必须先核查它与所选协议的兼容性。原始/送评 patch 分别保存 hash 和转换版本；转换规则在运行前固定，不能根据测试结果选择 patch。重复 instance 不覆盖，缺预测/空预测仍保留在任务清单；新文件、二进制与文件模式变化须按官方可接受格式处理，不能静默丢失。

**容器与轨迹**：已有 adapter 的 `SUPPORTS_ATIF=False`，这不等于不能运行，但标准轨迹兼容尚未完成。先保存实际可观测的消息、工具动作与结果；若目标版本/提交路径要求 ATIF，则在该阶段补齐并校验格式，不能事后生成虚构轨迹。固定 CPU/内存/GPU/架构、任务超时、镜像、任务包和外部网络配置；缓存/预装镜像的任何修改都记录，是否符合目标榜单规则必须核对。

**基准与日常行为的差异**：`--bench` 的容器授权档位、禁用的 skill/MCP/profile 等写入 profile；它的成绩只描述该配置。不能据此宣称同模型的日常 Ink 工作流具有相同能力。自动批准仍经 Gate，放宽路径仅适用于一次性基准容器。

**分级运行**：预检/单题 smoke → 预先选定的小集合开发对照 → 固定全量。复用任务缓存但不修改隐藏检查或超时以获得高分；oracle 仅在与 Agent 分离的环境做准备验证，答案与隐藏日志不能进入 Agent 输入。官方所需重复次数覆盖默认“三次”的探索建议；重跑保留新 attempt 和原因，不能只取成功的重跑。

**榜单状态**：local_subset / local_full / submission_prepared / submitted / accepted 分别展示，必须有证据才能升级。公开材料生成可以离线进行，上传或提 PR 属于另行授权的发布动作；accepted 以维护者接纳为准。2026-11-08 核查：[SWE-bench Verified 提交规则](https://github.com/SWE-bench/experiments)存在研究机构与研究材料要求；[Terminal-Bench 2.1 文档](https://github.com/harbor-framework/terminal-bench-2-1)声明社区提交暂关闭、仅维护者运行结果入榜。实验台不承诺个人本地结果必然获官方接纳，具体发布时重新核对规则。

## 4. 存储与协议演进

以下路径为规划，实施阶段根据现有存储约定冻结，不能当作现有命令或目录使用。

```text
mini-harness/
  app/session/             生产端采集和证据封装
  app/eval/                评测与协议适配
  evals/ 或 benchmarks/    可提交的案例定义、夹具、评分器；沿用既有分工
  openspec/roadmaps/build-agent-lab/   本文档包
  .vibe/lab/               运行证据、导入诊断、实验和重跑材料，不提交

实验台自己的工作区/
  .vibe/lab/artifacts/     受管、按内容寻址的证据
  .vibe/lab/index.sqlite   可重建查询索引
  .vibe/lab/annotations/  人工判断与基线选择记录，独立备份
```

源格式、证据包 schema、索引 schema、评分协议各有自己的版本；升级任何一项不能静默重写其他版本。首版 reader 支持当前主版本与明确列出的 legacy 适配器；未知主版本拒绝索引为可比数据。包校验通过只证明字节与声明一致，不证明来源可信或模型确实执行了所有动作。

## 5. 失败、安全与性能边界

| 场景 | 处理者与用户可见结果 | 验证方式 |
|---|---|---|
| 无记录、空题集 | 查询返回 no_data；不显示 100% 通过 | 空目录/空集合夹具 |
| 缺配置、旧报告 | importer 保留来源，标 legacy_unknown/incomparable | 旧 report 导入 |
| 断行、崩溃、无终态 | importer 标 incomplete 和位置，保留可读部分 | 中途截断/进程退出注入 |
| 同 ID 异内容、摘要不符 | importer 报 conflict/corrupt，不覆盖已导入事实 | 篡改一个字节后导入 |
| 路径穿越、符号链接逃逸 | importer 拒绝包外读取；UI 不跟随路径执行 | 恶意路径、链接夹具 |
| 磁盘满、快照不可读 | collector 诊断并标缺失；显式导出失败，不发布完整包 | I/O 错误注入 |
| 评分设施/准备环境失败 | evaluator 按决策 5 判断归属；设施故障为 invalid，候选引起的构建/执行失败为 fail；归属不明不作改善结论 | 故障 grader/候选破坏依赖的对照 |
| 预算耗尽、人工取消 | runner 停止后续尝试，账本保留，按预定协议评分 | 两候选中途停止 |
| 外部 exporter 离线 | exporter 有界失败，Agent 结果与本地证据不变 | 离线/超时注入 |
| 索引损坏、源材料被删 | query 报告不可用，可重建索引；材料缺失不可重建 | 删除索引/删除被引用文件 |

导入内容属于不可信数据：不执行命令、不自动安装依赖、不渲染未净化 HTML；新 Web 界面仅绑定本机，状态变更需防跨站请求，文件读取限定受管根目录。凭据、完整环境变量、个人偏好正文不默认写入交换包。脱敏产生派生产物并记录规则版本，不能用脱敏后哈希冒充原始内容哈希。

P1 的资源边界：离线导出/导入不调用模型、不发外部请求；逐行/分块处理，不按日志总量一次性载入内存。实施规格在写实现前，以当前机器和固定 10 万事件夹具测量基线，并锁定导入峰值内存、耗时、运行采集开销三个数值门槛；不允许验收后反向调整门槛。Web 查询的延迟目标在 P5 补细时根据真实包规模确定。

日常采集的额外快照有大小/耗时上限，触限返回明确缺失原因；正式实验要求的快照无法完成时，preflight 阻止开始模型调用。资源上限的具体值由 P1 规格基线测量确定。

## 6. 测试与验收策略

| 层次 | 重点 | 必需反例 |
|---|---|---|
| 单元 | 身份、规范化摘要、状态分类、比较条件、成本聚合 | 同 HEAD 异内容；unknown 不转零；父子成本不双计 |
| 集成 | 真实日志导入、runner 适配、工作区重建、独立评分 | 重复导入；日志轮转/截断；缺评分器；隐藏文件隔离 |
| 端到端 | fake 小任务生成证据，再运行固定 A/B，最终真实失败转回归 | 一方失败/缺测仍完整报告；候选把困难题变成 invalid 不得显示改善；读报告零副作用 |
| 真实模型 | 固定协议小题集，多次运行与全部成本 | 预算暂停不当成功；保留题曝光后不能维持 held_out |
| 人工 | 在实验台定位失败、解释变量与分母、选择基线 | 没有证据时能识别未知，不被图表暗示成功 |

需求追踪：R1/R2 → P1；R3 → P2 并由 P3 扩展；R8 → P2（SWE-bench）/P3（Terminal-Bench）；R4 → P4；R6 → P5；R5 → P6；R7 → P7。每个实施 change 将本表细化为条款、测试、实现和证据映射。P1/P2 无需等待完整 Web 产品；报告和机器可读查询即为独立交付。

## 7. 假设、风险与回退

| 假设/风险 | 失效信号 | 应对 |
|---|---|---|
| 单人本地工作量可用文件与 SQLite 承载 | 固定样本超过锁定的资源门槛 | 优化增量索引，再依据实测评估服务化 |
| 已有 trace 足够定位主要失败 | 经常缺起始输入/父子关系/终态 | 补对应生产端事实，不在 UI 猜测 |
| 自身仓库题可代表部分日常需求 | 改善只发生在已知题，实际任务无收益 | 增加不同任务族/仓库的保留案例 |
| 评分器能反映完成 | 人工复核发现假阳性或假阴性 | 修 grader 形成新版本，旧结果不悄改 |
| 模型标识能稳定比较 | provider 别名漂移或重复运行波动过大 | 记录时间窗/可得修订信息，降级结论，重新配对实验 |
| 只读观测不会显著拖慢运行 | 超过 P1 开销门槛 | 关闭可选采集，保留本地原有 trace；优化后重测 |

回退路径：关闭新增采集/导出后恢复现有 Agent 路径；实验台删除索引后从原包重建；基线回退是显式选择旧引用和另行执行部署动作，不自动 reset 用户工作区。数据库结构升级采用可重建投影，不把数据库迁移成功等同证据完好。

## 8. 与既有规格的关系

| 既有文档 | 关系与本轮处理 |
|---|---|
| [roadmap](../../../docs/roadmap.md)、[MILESTONES](../../../docs/MILESTONES.md) | 服务于可靠 coding 与评测扩题；新增规划入口，不提升已有验收状态 |
| [evaluation](../../../docs/specs/evaluation.md) | 复用既有预算、协议指纹和独立评分；P2 改动协议时同步更新 |
| [Harbor 接入](../../../docs/specs/harbor-benchmark.md)、[经验记录](../../../docs/specs/harbor-benchmark-lessons.md) | 复用 adapter 和历史问题，P2/P3 核查源码、依赖版本与真实产物；较早规格的待验收声明不覆盖较晚运行记录，历史成绩不算本轮验收 |
| [本地编码基准](../../../docs/specs/local-coding-benchmark.md)、[长任务评测](../../../docs/specs/long-horizon-eval.md) | 逐个 adapter 接入，不把不同任务类型混为同一分数 |
| [OTel 规格](../../../docs/specs/otel-observability.md) | 保持 M0–M3 默认不外发 eval/bench；P1/P2 的本地证据合同不等于开启遥测，外发仍按其阶段决定 |
| [open-decisions](../../../docs/specs/open-decisions.md) | 集中观测端点与真实模型预算仍待相关阶段确定；本规划不替用户作出外发或费用授权 |
| [工作台 UX 规划](../improve-workbench-ux/roadmap.md) | Ink 运行控制、阅读体验继续按其切片推进；实验台处理跨运行分析，不重复实现控制面 |

本包尚未生成 capability specs 或正式 ADR。后续 change 从本设计导出可执行契约，不把规划草案当已生效运行协议。

## 9. 实施前决定与延后事项

| 事项 | 当前建议/状态 | 负责人及触发点 |
|---|---|---|
| P1 字段 schema、快照清单与资源阈值 | 方向见决策 2–4；实施设计锁定 | Implementer 提案，Owner 审阅；P1 实现前完成 |
| P2 首个题集与模型开销 | 建议 Verified 冻结小集合；fixture 验证先行，真实预算单独确定 | Owner 决策；首次付费运行前完成，不阻塞离线实现 |
| Terminal-Bench 主版本 | 显式待选；已有 2.1 文档仅为试点参考 | Owner 选择；P3 补细前固定版本及数据 revision |
| 独立仓库名称、Web 框架与部署方式 | 推荐本地配套应用；具体选型延后 | P5 补细时按实测包规模确定 |
| 成绩/成本回归阈值 | 不预设任意百分比 | P6 依据多次基线分布校准后由 Owner 确定 |
| Langfuse/Phoenix/OTel 目标 | 保留兼容方向，尚未选定或部署 | P7 以一份真实证据包试接后决定 |
| 备份位置与保留期限 | 基线材料必须可恢复；当前不约定外部服务 | P4 案例存储投入日常使用前确定本地备份方案；远程另行决策 |

## 10. 参考资料

2026-11-08 核查官方资料；这些资料支持候选能力，不构成项目已集成的证据。

- [Langfuse Evaluation Concepts](https://langfuse.com/docs/evaluation/core-concepts)：在线观测与离线实验的关联、任务及评分模型。
- [Langfuse Datasets](https://langfuse.com/docs/evaluation/experiments/datasets)：题集与历史版本支持。
- [Phoenix 官方仓库](https://github.com/arize-ai/phoenix)：OTel tracing、数据集、实验及提示词管理能力。
- [SWE-bench 官方评测指南](https://www.swebench.com/SWE-bench/guides/evaluation/)：prediction 格式与容器化评分。
- [SWE-bench 官方提交仓库](https://github.com/SWE-bench/experiments)：提交资格、材料与维护者核验规则；发布时重新检查。
- [Terminal-Bench 2.1 官方仓库](https://github.com/harbor-framework/terminal-bench-2-1)：版本变更、重复试验与社区提交状态；只适用于该版本。
- [Terminal-Bench 官方入口](https://www.tbench.ai/)：当前主榜可能变化，不能用入口展示替代固定数据 revision。
- [Harbor 自定义 Agent](https://docs.harborframework.com/agents/custom-agents)：安装型适配接口；本仓锁定版本需独立验证兼容性。
- [Harbor 本地结果视图](https://docs.harborframework.com/results/view-job-results)：可先复用原生 job/trial 查看器，实验台补充跨来源版本与比较关系。
