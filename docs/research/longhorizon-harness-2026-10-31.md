# LongHorizon-Harness 一手资料深读（2026-10-31）

对象：论文《LongHorizon-Harness: Advancing Long-Horizon Agents for Real-World Tasks》，arXiv:2608.01964v1，2026-08-03 提交，29 页，cs.CV；作者 Ziyu Ma、Hailang Huang、Shun Zou、Yong Wang、Shidong Yang、Yiming Hu、Fei Wei、Xiangxiang Chu，论文署名机构行为 "DreamX Team, Alibaba Group"，代码仓库挂在 GitHub `AMAP-ML` 组织下，官网页脚同样写 DreamX Team（[abs 页](https://arxiv.org/abs/2608.01964)、[HTML 全文](https://arxiv.org/html/2608.01964v1)、[官网](https://lh-harness.pages.dev)）。

范围：论文 HTML 全文逐节读；仓库中英 README 与 `src/lh_harness/` 关键实现文件；PyPI 0.1.8 描述；项目官网。**不做本地安装、不跑评测**，因此下文所有性能数字都是"被声称"而非"被复核"。

证据标记约定：

- **【论文】**＝arXiv 正文/附录原话（给节号与锚点）。
- **【实现】**＝仓库 README 或 `src/lh_harness/` 代码（给文件与行号）。
- **【官网】**＝项目主页文案。
- **【推断】**＝我的分析或迁移建议，不是任何一方的声称。

## 结论

1. 论文的核心不是"更长的上下文"，而是**把任务状态搬出执行上下文**：状态是外部记录，只接受"从环境独立验证过的事实"，下一轮子任务由这条记录推导。三角色（Manager / Executor / Auditor）不是三个协作 agent，而是**权限不对称的三段流水线**：只有 Executor 能写环境，只有 Auditor 能产出可入账的证据，只有 Manager 能改状态。【论文】
2. 实现比论文多两道**确定性硬闸**：审计前对 workspace 做快照、审计后比对并可回滚（检测到改动即记 integrity violation）；以及 harness 侧完成守卫——Auditor 自己写了 `complete` 但列出非空 blocking 约束时，harness 直接拒绝该结论。这两点论文正文没有写。【实现】
3. "只读"是**提示词 + 工具拒绝列表 + 快照回滚**三层，不是 OS 沙箱。Claude Code 适配器的代码注释自认："deny-list expresses harness role separation, not a filesystem or process sandbox"，且 Manager/Auditor 跑在 `bypassPermissions` 模式下。【实现】
4. 成本结构反直觉且有用：Manager 只占总 token 的 2.0%–8.1%，Auditor 占 19.4%–38.1%，**审计是主要额外开销**；总额不固定——WeaveBench 2.3×、OSWorld 输出 token 3.6×、Terminal-Bench 反而 **−24%**。同一个框架下，强模型（Opus 4.7）在 Games 子集 token 降 33%，弱模型（Qwen 3.7-Plus）涨 3.2×。【论文】
5. **没有组件消融**：没有 MEA-minus-auditor、没有 fresh-context 消融、没有单模型 vs 混模型对比。只有按 benchmark/能力标签的成本分解与难度分层。这是全文最大的证据短板。【论文】
6. 数字有对不上的地方：摘要与引言写 OSWorld 2.0 子集 "20.0% → 34.3%"，正文 §3.2、Table 3 与附录 B.1 写 "20.6% → 35.3%"（partial 55.8→66.9）。以正文/附录为准，摘要数字应视为旧版残留。【论文】
7. 中文一手材料**没有**使用"外置状态""环境审计"这两个词。对应表述是"已验证状态 / 可信任务状态""独立检查真实文件、界面、日志和测试""保存通过验收的进度"。"外置状态""环境审计"是我们这类读者的事后概括。【实现/官网】

## 一手来源与抓取情况

| 来源 | 地址 | 状态 | 用途 |
|---|---|---|---|
| 论文 HTML v1 | <https://arxiv.org/html/2608.01964v1> | 200，解析出 89,490 字符正文，按节读取 | 主证据 |
| arXiv abs | <https://arxiv.org/abs/2608.01964> | 200 | 元数据、摘要、页数 |
| GitHub README（英） | <https://raw.githubusercontent.com/AMAP-ML/LongHorizon-Harness/main/README.md> | 200 | 安装/配置/角色/结果 |
| GitHub README（中） | <https://raw.githubusercontent.com/AMAP-ML/LongHorizon-Harness/main/README.zh-CN.md> | 200 | 中文术语核对 |
| 实现代码 | `src/lh_harness/{types,manager,auditor_agent,prompt_texts,role_prompts}.py`、`adapters/{base,claude_permissions,deepseek_harness}.py`、`environment/base.py` | 200 | 状态 schema、只读实现、适配器契约 |
| PyPI | <https://pypi.org/project/lh-harness/> | 200，最新 0.1.8（2026-10-21 发布，MIT，Python ≥3.10） | 分发形态；描述与英文 README 同源 |
| 项目官网 | <https://lh-harness.pages.dev> | 200 | 主张、结果表、成本分解 |
| HuggingFace paper 页 | <https://huggingface.co/papers/2608.01964> | **失败**：`fetch failed`（页面与 `/api/papers/...` 各试，含超时） | 仅知 README 声称登上 2026-W32 周榜第 1；未能核实 |

## 1 问题定义

### 1.1 为什么"在同一上下文里维护执行/状态/完成度"会坏

论文把困难归到三条挑战上（§1）：(i) **复合误差与目标漂移**——早期动作的错误沿轨迹累积并扭曲后续选择；(ii) **context rot**——历史变长后相关信息越来越难取出，一旦上下文利用率过某个阈值性能骤降；(iii) **任务状态丢失**——长程任务需要"待满足的要求、已完成的动作、已产出的工件、从环境发现的事实"这份状态始终准确且最新，而 agent 往往保不住、更新不了。三条各自引了外围文献（Lost in the Middle、Chroma 的 context rot 报告、long-horizon survey、METR 的 horizon 曲线），**没有做自己的诊断实验**。

在此之上论文给出两个**结构性**缺陷（§1，这是全文的立论核心）：

1. **任务执行与任务状态管理共用同一个不断增长的上下文**。agent 用同一份上下文既做事又记账，执行历史越长，账越难查。
2. **任务执行与完成度判断仍然耦合**。agent 自己做子任务、自己判是否完成，"一个错误判断可能被记进任务状态，并成为后续决策的前提"。

### 1.2 论文如何论证"错误自评会传播"

论证是**机制性**的，不是实验性的，分三段：

- 前提：现有 harness 里"子 agent 汇报自己的工作总结，复核只是可选动作而非强制闸门"（官网把这句写得更直白："a subagent reports a summary of its own work, and review is an optional action rather than a mandatory gate"）。【官网】
- 传导：未经验证的前提一旦进入记录，后续每一次决策都继承它，表现为重复尝试、目标漂移或过早终止。【官网/论文】
- 反证：把自评挡在状态之外——Executor 的报告 `o_i` 只是"未验证的执行摘要"，只有 Auditor 的 `v_i` 才是"以环境为依据、可以推进持久状态的证据"（§2.4 末）。

**证据强度**：支撑"传播"的经验材料只有 §3.4 的四组配对案例（同任务、同 Qwen 3.7-Plus、baseline vs 本方法），例如 `WEB_task_16` 里 baseline 发现 Wireshark 的 "Decode As" 对话框无响应，但这个观察留在不断增长的历史里，于是"对同一交互重试 400+ 步"仍拿不到剩余证据，0.59；本方法把卡住的交互和未解证据缺口写进状态，后续 executor 直接补图表与抓包证据，0.92。这是**存在性证据**，不是对照实验：论文没有测"错误自评被记入状态的比例"，也没有测传播链长度。§3.4 的所有案例都是成功侧叙事，失败案例只在附录 B.2 的分类讨论里出现。

## 2 核心主张：把长程执行重述为 task-state management

### 2.1 "外置"到底外置了什么

论文的说法是：把任务状态维护为**执行之外的显式记录**，只用**从环境独立验证过的事实**更新它，并且**每个下一子任务都从这条记录加原始目标推导**（§1 贡献段、§2.1）。形式化上，第 i 轮开始时状态为 `S_i`，环境为 `e_{i-1}`，累计审计报告为 `V_{i-1}`；Manager 产出受约束的子任务契约 `c_i`；Executor 把环境从 `e_{i-1}` 推到 `e_i` 并交执行报告 `o_i`；Auditor 只读检查 `e_i` 得到 `v_i`；Manager 把 `v_i` 并入 `S_{i+1}`。跨轮**只保留**任务状态与支撑它的审计报告，Executor 的原始轨迹每轮丢弃（§2.1）。

一个容易漏掉但很关键的措辞：论文把审计报告称为"**唯一的跨轮记忆**"（Fig. 1 caption："Audit reports are the only cross-round memory"）。也就是说被外置的不只是"状态"，还包括"状态凭什么成立"的证据链。

### 2.2 外置状态里存什么

论文的定义（§2.2 Task-state update）：任务是"与任务相关的记录的**结构化集合**"，记录分三类——**requirement**（从原始任务派生的目标或约束）、**artifact**（执行中创建或修改的产出）、**fact**（后续轮次需要的环境信息）；每条记录带四种状态之一 `completed / pending / blocked / untrusted`，并**保留支撑其当前状态的审计证据引用**；初始 `S_1` 由原始任务构造，requirement 全部标 `pending`。

实现层把同一套东西落成**自然语言协议**，而不是 JSON（`src/lh_harness/prompt_texts.py` L108–L135）：

| 论文概念 | 论文表述 | 实现载体（Manager 每轮必须输出） |
|---|---|---|
| 状态容器 | structured collection of records | `当前任务状态:` 文本块，强制分四桶：**已完成 / 未完成 / 阻塞·风险 / 不可信·不可复用** |
| 证据引用 | "retains references to the audit evidence" | 每条事实必须引 auditor round（如 `round_003`）；无证据标"待审计"；明文禁止采用 executor 未审计的自述 |
| 契约 | goal / acceptance criteria / boundary constraints / prior relevant evidence | `任务契约:`，**跨轮稳定**，要求写清"真实被消费的目标状态、权威输入、**状态载体**、允许流程、持久化边界、验收约束和证据" |
| 路由依据 | "checks dependencies and prerequisites" | `依赖判断:`：目标状态、状态创建者（GUI/CLI/CLI+GUI）、已满足前置、未满足前置、本轮路由理由 |
| 控制决策 | `q ∈ {execute, done, blocked, ask}` | `下一步:` 五选一：`GUI任务 / CLI任务 / 请示用户 / 完成 / 阻塞`（代码另有 `invalid`） |

**与论文的两处实质差异**（【实现】vs【论文】）：(a) 论文说状态是"结构化集合"，实现是**受模板约束的自由文本**，靠正则头 `当前任务状态:` 切块（`role_prompts.py` L617–L650），因此不存在机器可校验的 schema；(b) 论文的控制量是 4 个，实现是 5 个，GUI/CLI 的路由被显式化，`ask` 被写成"请示用户"并且**禁止把需要真人的交互拆给 executor**。

## 3 Manage-Execute-Audit 循环

### 3.1 一轮的输入输出

| 角色 | 输入 | 输出 | 权限（论文） | 权限（实现） |
|---|---|---|---|---|
| **Manager** | 原始任务 `T`、当前状态 `S_i`、**全部**累计审计报告 `V_{i-1}`；**没有环境接口**，看不到应用状态、看不到工作区、不能调 GUI/CLI、不能改环境（§2.2） | `(S_{i+1}, q_{i+1}, c_{i+1})`，`q` ∈ execute/done/blocked/ask | 只读写记录，不碰环境 | Claude Code：`bypassPermissions` + 禁 `Bash/Write/Edit/NotebookEdit/Agent/mcp__*`，`workspace_read_only=True`；DeepSeek：`read-only` |
| **Executor** | `T`、`S_i`、契约 `c_i`、**仅契约引用的**历史审计报告（§2.3） | 环境迁移 `e_{i-1}→e_i` + 执行报告 `o_i`（动作、结果状态、产物、遇到的问题） | 唯一被允许有意改环境的角色 | `workspace-write`；仅禁 `Agent`；按角色加载 computer-use MCP |
| **Auditor** | `T`、`S_i`、`c_i`、契约引用的历史报告、`o_i`；**不含** executor 轨迹与推理（§2.4） | `v_i`：完成状态、完整性状态、契约审计结论 + 事实/证据/缺口 | 只读；可换观察视角但不能改任务相关状态 | `read-only`；禁 `Write/Edit/NotebookEdit/Agent`，`Bash` 仍可用但提示词限为只读命令；审计窗口内 workspace 快照比对 |

停止条件（§2.1）：审计后状态满足原任务、没有允许的子任务能推进剩余要求、需要用户输入、或**轮次预算耗尽**。论文 `N_max=25`，实现 `DEFAULT_MAX_ROUNDS=25`、硬上限 `MAX_ROUNDS=1000`（`types.py`）。

### 3.2 为什么 Executor 必须 fresh context

论文给的理由是"每轮只装本轮供给的信息，不接收更早轮次的原始轨迹；episode 内可以自由地多轮规划-观察-修正，episode 结束后轨迹与推理丢弃，只有 `o_i` 进入审计"（§2.3）。把它拆成可检验的三条：

1. **错误隔离**（论文原话的意图）：失败轨迹不再作为下一轮的前提。案例 `WEB_task_16` 的 400+ 步重试正是"失败留在历史里"的代价。
2. **上下文有界**【推断】：每轮上下文规模与任务长度解耦，只与"当前子任务 + 相关证据"有关，这也解释了为什么 Terminal-Bench 上总 token 能降 24%——轮数没有膨胀，而每轮不必重读全部历史。论文只把成本归因于"任务与所需轮数"，没有把这条写成机制。
3. **审计可比**【推断】：审计对象是"这一份契约对应的环境变化"，而不是一段无法定界的连续轨迹。

实现上这不是靠提示词，而是**每轮起独立子进程**：`executor_prompt.txt` → 适配器跑一个 bounded episode → `executor_output.txt`，原始轨迹另存 `executor_raw_trajectory.jsonl` 且不回灌（`manager.py` 轮目录写入逻辑）。

### 3.3 状态在轮间怎么流转（实现）

- Manager 输出先落 `manager_plan.txt`，再由 `extract_role_task_state()` / `extract_role_task_contract()` 从文本里切出 `task_state.txt`、`task_contract.txt`（`manager.py` L491–L499）。**状态不是被"改"出来的，而是每轮被 Manager 重写一遍**，上一轮状态只作为 fallback。
- 每轮目录：`logs/role_orchestration/rounds/round_XXX/{manager_plan,task_state,task_contract,executor_prompt,executor_output,auditor_report}.txt`，另有 `events.jsonl` 与 `report.json`。
- 所谓 checkpoint 不是一份可变的检查点文件，而是**重放 ledger**：resume 时读回已记录的 rounds，`current_task_state = rounds[-1].task_state`，轮次预算从已恢复轮次继续（`manager.py` L265–L278，注释原文："A resumed worker reopens the same ledger… replaying the ledger restores the full planning context"）。
- 上下文刷新靠配置裁剪，不靠摘要：`HarnessConfig` 里有 `role_verified_context_chars=60_000`、`role_history_chars=100_000`、`auditor_output_chars=24_000`（`types.py`）。也就是说"刷新"= 只重新注入契约/状态/相关报告 + 截断，而不是把旧对话压缩成摘要。

## 4 环境审计

### 4.1 独立性怎么保证

三条：Auditor 从**不含 executor 轨迹与推理**的新上下文开始；可以用 `o_i` 去**定位**相关文件、窗口、日志、进程，但完成判断必须来自"把环境与契约的目标/验收标准/边界独立比对"；结论必须有**直接从环境取得的证据**，executor 的完成声明不算证据（§2.4）。GUI auditor 用观察型交互看应用与屏幕状态，CLI auditor 用非变更命令与检查工具看文件、元数据、日志、进程、测试、工作区状态。

### 4.2 只读是怎么做到的（四层实现，论文只写到前两层的一半）

| 层 | 机制 | 来源 |
|---|---|---|
| 提示词 | "不要点击/输入/滚动/拖拽/改窗口/改任务文件"；Computer Use 仅限观察或截图；发现伪造产物**只报告，绝不修复、移动或删除** | 【实现】`prompt_texts.py` L187–L205 |
| 工具拒绝列表 | 禁 `Write/Edit/NotebookEdit/Agent`；`Bash` **不在拒绝列表里**，只靠提示词约束为"少量受控只读命令"，`Read/Glob/Grep` 照常可用 | 【实现】`claude_permissions.py`（`_AUDITOR_ROLES` 分支）、auditor 指令 |
| **快照 + 回滚** | 审计前后对 workspace 做清单（mode/size/mtime_ns/小文件摘要），检测到改动即记 `integrity_status=violation`，并按配置回滚到审计前快照；无法确认恢复时审计作废 | 【实现】`claude_permissions.snapshot_workspace`、`auditor_agent.py` L92–L101 |
| **完成守卫** | Auditor 自己写 `complete`，但正文列出非空 blocking 约束、或完整性为 violation、或契约审计非 aligned 时，harness 强制降级为 incomplete | 【实现】`auditor_agent.py` L133、L279 |

论文 §2.4 写到的是"指令层面的只读约束"（不许创建/编辑/覆盖/移动/删除、不许执行变更命令）加"harness 监控任务相关状态，检测到的改动记为完整性违规，该报告不能支撑 completed 记录"——即第 3 层的**检测**有写、**回滚**没写，工具拒绝列表与第 4 层的**确定性覆盖**完全没出现。这两层是更硬的保证，也是这套设计最值得抄的部分。

### 4.3 检查哪些环境证据

论文与附录给出的证据类型（§2.4、附录 C）：

- **文件/工件内容**：`DOC_task_2` 要求标题样式规范化，baseline 直接改 document XML、标题看起来对了就收工，得 0.00；本方法的 auditor **解析 docx XML** 逐个确认 15 个标题的底层样式，0.89。
- **产物来源与元数据**：`save_screenshot` 存图必须带 `.meta.json`，auditor 检查 `capture_source=real_screen`——**截图是不是真屏幕**本身是审计对象（附录 A.1 说明该工具"不提供新的环境控制能力、不额外暴露信息"，只是把当前真实屏幕存成官方要求的截图产物）。
- **前后状态序列**：`DOC_task_4` 要求保留修复前的公式错误证据，本方法把"缺失的修复前证据"记为 pending requirement，先取证再修，auditor 复核**九张截图的完整序列**，0.45→0.87。
- **日志/进程/服务/包/仓库状态**：附录 B.2.3 说 Terminal-Bench 增益最大的是系统管理类，因为这类任务"由安装、服务配置、PATH 管理、构建产物、持久化副作用等有状态过程主导"。
- **跨接口交叉**：附录 C 的 DevOps / Web 诊断案例要求"不同接口的证据先互相吻合，才允许把进度标完成"。

### 4.4 审计结论如何变成"可信状态" vs "仅作证据"

`v_i` 记三类结论（§2.4）：完成状态 `complete|incomplete|blocked`；完整性 `clean|suspect|violation`（查工作区改动、工件有效性与来源、删除约束）；以及"本次检查支持的任务状态更新"，含已验证事实、支撑证据、剩余缺口。

关键的分权：**Auditor 只能"提议"改动 requirement/artifact/fact 记录，由 Manager 决定如何并入 `S_{i+1}`**（§2.4 末）。所以 `o_i` 永远是未验证的执行摘要，`v_i` 才是可以推进持久状态的、有环境依据的证据。

注意官网把这条写反了两次："The task state lives outside execution and is **written only by the auditor**"，以及 P2 "Only a read-only auditor can update the record"。论文与实现都是 Manager 写、Auditor 只能提议。这类"营销页比论文更强"的措辞，引用时不能当依据。【官网 vs 论文】

实现则把分权落成 Manager 侧的硬规则：只有 auditor 前三行分别是 `状态: complete`、`完整性: clean`、`契约审计: aligned` 且正文支持全部原始要求时，才允许输出"完成"（`prompt_texts.py` L116）。

关于"accepted progress vs evidence"：README 的表述最清楚——"只有通过独立验证的结果才能成为可信任务状态。**被拒绝的结果只作为证据，不会被记作进度**"；"即使上下文刷新、操作失败或交付不合格，下一轮也会从原始目标和最后一个已验证检查点重新开始"。【实现】

### 4.5 失败结果如何进入下一轮

被拒绝的结果不会消失，而是带着结构化字段进下一轮：`missing[]`（含 `actionable` 布尔）、`blockers[]`、`action_guidance`、`state_summary`、`related_report_refs`（round id + 引用原因）（`types.py` 的 `AuditReport`）。Manager 下一轮把这些作为"未完成/阻塞·风险/不可信"桶的内容，并在契约里显式列"相关审计报告 + 相关已审计状态"，再决定路由。论文的说法是"未解决的失败与已完成进度都被带在执行历史之外；未满足的依赖在 Manager 定义后续子任务时仍然可用"（§3.4）。

### 4.6 论文没写、但实现里最硬的一块：验收约束反查

`AUDITOR_CONTRACT_BACKCHECK`（`prompt_texts.py` L225–L262）要求 auditor **不要默认 Manager 给的契约是对的**，先独立从原题重建并挑战验收约束，报告必须含 `验收约束反查:`：契约结论（必须与第三行控制头一致）、**原题约束清单**（最终消费者/**状态载体**、权威输入、候选选择、字段值、文件路径附件、保存提交持久化、格式样式精确文本单位精度、不能改不能漏不能多做、非目标保持、禁止捷径）、契约覆盖检查（遗漏/弱化/写歪/矛盾）、逐项反查（每条写原题依据、是否 blocking、独立证据、verified/unknown/violated/not_applicable）、阻断约束、评分风险、**建议契约修订**。规则明确："契约自述和 executor 自述不是独立证据"；"存在阻断约束或契约审计不是 aligned 时，即使局部子任务成功也必须输出 incomplete"。

同一段还给了**证据时间范围**规则，用来防止审计过度：缺少完整命令记录本身不是篡改证据；事后不可观察的"历史上没发生过某动作"默认只是非阻断残余风险，除非原题明确要求或契约在执行前安排了权威监控；不得仅为证明已不可观察的历史否定而要求重做，此时应建议"后续加前置监控"，改为独立验证当前持久化状态、候选身份、精确值与持久化污染。【实现】

这条设计解决的是论文完全没讨论的问题：**审计的判据本身可能是错的**。论文把"contract"当作 Manager 的产物、Auditor 的输入，默认它是对的；实现让 Auditor 反向审契约，并把 `needs_revision/invalid` 作为阻断完成的理由。

## 5 状态更新规则小结

| 问题 | 答案 | 来源 |
|---|---|---|
| 哪些事实算"经环境独立验证" | 由 auditor 从环境直接取得、对照契约的验收标准、且完整性为 clean 的证据；契约自述与 executor 自述都不算独立证据 | 【论文】§2.4；【实现】backcheck |
| 有没有 state schema | 论文没有字段级 schema、没有 IDL、没有 JSON 约定；实现是模板化自然语言 + 前三行控制头 + 四桶状态，正则解析 | 【论文】§2.2；【实现】`role_prompts.py`、`auditor_agent.py` |
| 有没有 ledger | 有，但是"每轮一份快照文件 + jsonl 事件流"，不是数据库；状态每轮被整体重写 | 【实现】`manager.py` |
| 有没有 checkpoint 文件 | 没有单独 checkpoint；恢复＝重放 ledger 取最后一轮 `task_state`/`task_contract` | 【实现】`manager.py` L265–L278 |
| context refresh 怎么工作 | 重新注入"契约 + 状态 + 相关审计报告"，并按 `role_*_chars` 截断；历史轨迹永不回灌 | 【实现】`types.py`、`manager.py` |

## 6 AgentAdapter / Environment 抽象

两个契约都极小，这是"不改各 agent 原生 loop"能成立的原因：

```
AgentAdapter.run_episode(prompt, env, budget, live_trajectory_path) -> EpisodeResult   # Protocol
Environment.exec(command, timeout, tee_path) / screenshot() / upload() / download()    # Protocol
```

（`adapters/base.py`、`environment/base.py`，均为 `typing.Protocol` + `runtime_checkable`。）

接入方式是**把 agent 当成一次有预算的 headless CLI 调用**：给 prompt、给角色对应的环境接口、给 episode 预算，后端保留自己的规划与工具循环，harness 只控制"喂进去的上下文、可用工具、环境权限、执行预算、以及收回来的报告"（§2.3 Backend execution）。README 列的四个后端分别落到 `claude`、`codex exec`、`opencode run`、`dsh --profile headless`；DeepSeek 适配器用隔离的 `DSH_HOME` 和按角色映射的 sandbox 模式（`permission_mode_for_role`：auditor/manager→`read-only`，executor→`workspace-write`）。【实现】

**角色权限隔离的真实形状**：

- 隔离发生在**后端调用参数**层（permission mode / deny-list / 只读 MCP），不在 harness 自己实现的沙箱里。
- GUI/CLI 的**路由不是权限隔离**：Manager 提示词明写"路由不是工具权限隔离"，CLI executor 仍被允许做少量 GUI 观察动作，GUI executor 也仍可调 bash/read/write 辅助，只是"CLI 不能替代真实屏幕操作和视觉确认"。【实现】
- 论文 §2.3 说"该角色之外的能力不可用，除非配置显式提供"，这句话在实现里只对**后端级权限**成立，对**工具级**并不成立（例如 GUI executor 仍持有 bash）。这是论文措辞比实现更强的一处。

## 7 实验

### 7.1 设置

| 维度 | WeaveBench | OSWorld 2.0 | Terminal-Bench 2.1 |
|---|---|---|---|
| 任务数 | 114（8 域） | 108（人类中位 1.6 h） | 分类见 Table 9，**每任务 3 次独立运行取均值** |
| 环境 | 容器化 Ubuntu 桌面 VM，每任务隔离 | 官方 `osworld-v2-2026.06.24` Docker VM，1920×1080 | Harbor + Docker 后端，保留原 CPU/内存约束 |
| 评分 | 官方 judge 模板，Claude Opus 4.7 做轨迹感知 judge；PassRate = 得分 ≥0.8 的比例 | 官方 `env.evaluate()`；Binary（分数=1）+ Partial（均值） | 每任务通过/失败，3 次平均 |
| 我们的配置 | Qwen 3.7-Plus + Claude Code 2.1.76；executor 1800 s、manager/auditor 300 s；`N_max=25` | thinking enabled、max_tokens 65536、effort=max；GUI 走 VM 内 computer MCP，CLI 走同 VM shell（**hybrid**） | Claude Code v2.1.211，容器内透明请求代理；temp=1.0, top_p=0.95, top_k=20, thinking=true, max_tokens=65536；单次 5 h |

backbone：主用 **Qwen 3.7-Plus**，泛化用 **Claude Opus 4.7**；三组自跑配置（Claude Code+Qwen、本方法+Claude Code+Qwen、本方法+Codex+GPT-5.6 Luna），其余数字引自官方榜/官方报告。baseline 是**同一个 Claude Code 评测框架**，三角色用同一 backbone，"以确保比较反映任务状态管理的效果而非模型差异"（§3.1）。

### 7.2 主结果（均为被声称值）

| Benchmark | baseline（Claude Code） | LongHorizon-Harness | 变化 |
|---|---|---|---|
| WeaveBench PassRate | 51.8 | **80.7** | +28.9 |
| WeaveBench Overall | 0.702 | **0.835** | +0.133 |
| OSWorld 2.0 Binary | 2.8 | **8.3** | 3.0× |
| OSWorld 2.0 Partial | 21.5 | **35.2** | +13.7 |
| Terminal-Bench 2.1 | 69.7 | **77.2** | +7.5 |
| OSWorld 2.0 Opus 4.7 子集（34 题）Binary | 20.6 | **35.3** | +14.7 |
| OSWorld 2.0 Opus 4.7 子集 Partial | 55.8 | **66.9** | +11.1 |

参照系（官方已报结果，非同条件）：WeaveBench 最强官方配置是 Claude Opus 4.7 + Claude Code，PR 41.2、Overall 0.532；OSWorld 2.0 最强官方是 Claude Opus 4.8 batched，20.6 / 54.8；Terminal-Bench 上本方法 + Codex + GPT-5.6 Luna 达 83.1。

域级结果（WeaveBench，PR 点数）：DSK 83.3→88.9、DOC 76.5→100.0、GAM 29.4→58.8、WEB 46.7→73.3、DAV 53.8→84.6、OPS 66.7→91.7、SPA 16.7→66.7、DES 20.0→80.0；八域全升。

### 7.3 成本

- OSWorld：平均**输出** token 28.9K → 104K/任务（3.6×）；WeaveBench 总 token 2.3×；Terminal-Bench **少 24%** 且成功率更高。
- 角色占比（WeaveBench / OSWorld / TB）：Manager **2.8% / 2.0% / 8.1%**，Auditor **19.4% / 24.8% / 38.1%**，余下为 Executor（77.8% / 73.2% / 53.8%）。
- 框架不带来固定倍数：Games 17 题上，Opus 4.7 从 16.5M 降到 11.1M（−33%），Qwen 3.7-Plus 从 10.7M 涨到 34.3M（3.2×）。论文的解释是强模型用更少的"审计-重规划"轮就满足契约，弱模型要把推理花在重复执行与恢复上。
- **没有延迟/墙钟时间分析**，也没有费用换算；只有 episode 超时预算（1800 s / 300 s）与"单次 5 h"这类上限。

### 7.4 有没有消融

**没有组件消融**。全文（含附录）不存在"去掉 Auditor""去掉 fresh-context""三角色同模型 vs 混模型""不做状态外置但保留多轮"这类对照。现有的分解只有三种：按角色的 token 占比（Fig. 5）、按 benchmark/域/能力标签/任务类别的增益（Fig. 6、Table 6–9）、按 backbone 的泛化（Table 4）。因此"哪个组件贡献最大"只能从间接证据推：Auditor 的 token 份额（19–38%）与"整体增益集中在可被验证为环境状态的任务上"（附录 B.2 开头）都指向审计环节是主要贡献者，但这是**推断，论文未做归因实验**。

仓库里倒是有 `--agent_gui false` 的 "CLI-only ablation" 开关（`eval/OSWorldv2-harness/docs/EXPERIMENT_PARAMETERS.zh-CN.md`），但论文没有报告这组结果。

### 7.5 局限、回归与失效模式

论文自己承认的：独立审计"能发现错误结果并发起恢复，但**不能提供模型本身不具备的能力**"，所以瓶颈在单步能力的任务（视觉定位、数学、算法、编码）增益小甚至为负（§3.3）。

具体回归（都是论文自己列的）：

- OSWorld 34 题子集：021 1.0→0.0、027 0.5→0.0、030 1.0→0.0、032 0.911→0.2、025 0.2401→0.0833（Table 5）。
- Terminal-Bench 分类：data-science −0.125、mathematics −0.167、model-training −0.083、video-processing −0.333（n=3 的小类）（Table 9）。
- WeaveBench：DSK 域**分数** −0.0205，但**通过率** +5.56 pp——"已经很强的 baseline 轨迹会被额外的验证与修复步骤影响"（附录 B.2.1）。
- Games 子集内也有反向：rhythm_autoplay 0.95→0.83、xmoto 0.92→0.85、kmahjongg 0.79→0.75、pysol 0.52→0.41（Table 7）。

附录 B.2.2 的五点观察是全文最诚实的失效模式清单：人机协同里"若新给的信息没有覆盖旧证据，verifier 可能确认一个**过期状态**"；隐式状态推断"在没有权威闭包（文件/数据库/应用设置/评测可见产物）时，**看似合理的证据会被误认为完成证据**"；跨源推理的失败常发生在"证据已理解，但最终答案写错了状态载体、存错边界或没走预期接口"；纯空间/动作级视觉需求仍受限于定位与光标控制。

### 7.6 数字与口径上对不上的地方

1. **摘要 vs 正文**：摘要与引言写 OSWorld 子集 "20.0% → 34.3%"，§3.2 / Table 3 / 附录 B.1 写 20.6→35.3（partial 55.83→66.86）。以正文为准。
2. **PassRate 定义**：§3.1 说"fully passed tasks 的百分比"，附录 A.1 与官网说"得分 **≥0.8** 的比例"。后者才是可复现口径。
3. **"nearly doubling the strongest officially reported result of 41.2%"**：41.2 是 Opus 4.7 + Claude Code 的官方成绩，而 51.8 是作者自己在 Qwen 3.7-Plus + Claude Code 下跑出的 baseline（且**任务 VM 内用 root**，官方用普通用户）。论文加了"参考点而非匹配比较"的免责，但"翻倍"的对比是跨条件的。
4. **OSWorld 的 baseline 是 GUI-only，本方法是 hybrid GUI+CLI**：附录 A.2 自己写明"与主要官方 baseline 通常只用 GUI 动作不同，本方法在 OSWorld-v2 上用 hybrid 工具池"。所以 OSWorld 的 2.8→8.3 里混着"多了一整套 CLI 工具"的效应，不纯是状态管理。
5. 术语漂移：附录 A.1 里把 auditor 写成 "verifier"，与正文 auditor 混用。

## 8 中文材料里的术语

| 英文概念 | 论文英文 | 中文一手材料实际用词 | 是否出现"外置状态/环境审计" |
|---|---|---|---|
| task state maintained explicitly outside execution | "explicit record outside task execution" | "已验证状态""可信任务状态""目标、进度和证据始终由同一套状态管理系统维护" | **否** |
| independent environment inspection | "independently inspects the environment" | "独立检查真实文件、界面、日志和测试，不直接相信 Executor 对结果的描述"；Dashboard 列"真实环境证明了什么" | **否**（最接近的是"独立验证""真实环境检查""验收"） |
| accepted progress | "verified facts / audited task state" | "已验证进度""通过验收的进度""保存…进度" | **否** |
| evidence (rejected result) | "evidence, not progress" | "被拒绝的结果只作为证据，不会被记作进度" | **否** |
| fresh context | "fresh, budget-bounded context" | "全新上下文""上下文刷新" | — |
| checkpoint | "checkpoint"（官网/README 英文） | "保存或恢复""最后一个已验证检查点" | — |
| contract | "subtask contract" | "边界明确的任务""任务契约" | — |

结论：中文材料的主线词是**"已验证/可信任务状态" + "独立检查" + "验收" + "检查点"**，不是"外置状态""环境审计"。代码内中文提示词另有"验收约束反查""给任务管理器的状态更新""完整性""契约审计"这类更工程化的叫法。写作时若用"外置状态""环境审计"，应注明是我们的概括。

## 9 与相关工作的关系

**论文没有 Related Work 章节**（全文检索无 "Related Work"）。它只在引言里用三条挑战挂了几篇外围文献，其余引用集中在 harness、benchmark 与自家前期工作。因此"与 compaction / Reflexion / self-verification 的关系"**在论文里不存在**；下文带【推断】的部分是我的分析，不是论文表述。

论文实际点到的：

- 长上下文退化：Lost in the Middle（Liu et al. 2024）、Context Rot（Hong et al. 2025）——用来支持"上下文变长后性能会掉"。
- 长程能力趋势：METR / Kwa et al. 2026；long-horizon agents survey（Dong et al. 2026）——用来支持"horizon 在变长，但更长不等于更可靠"。
- 独立判定：Agent-as-a-Judge（Zhuge et al. 2024）、Zheng et al. 2023——**只在引入 auditor 的一句里并列引用**，没有展开任何"LLM-as-judge 的可靠性"讨论。
- 现有 harness：Claude Code、Codex CLI、OpenClaw、Hermes Agent——被定位为"已经支持规划、分解、工具、隔离子 agent 执行或复核"，本方法的差异是"状态与执行分离 + 完成判定与执行解耦"两条结构性缺陷。
- benchmark：WeaveBench（Li et al. 2026）、OSWorld 2.0（Yuan et al. 2026）、Terminal-Bench 2.1（Merrill et al. 2026）。

**没有引用**：Reflexion、self-refine/self-verification 系列、context compaction / summarization 系列、任何 task-state / ledger / blackboard 类工作。

【推断】关系可以这样放：

- **vs context compaction**：compaction 处理"同一会话怎么装下更多历史"，本方法处理"哪些历史根本不该跨轮"。二者正交：本方法每轮上下文本来就有界，但仍可能需要 compaction 来处理单轮内的长工具输出。
- **vs Reflexion**：都以"外部反馈改下一轮"为核心，但 Reflexion 的反馈来自**同一个 agent 的自我反思**且写进同一段记忆；本方法的反馈来自**另一个只读角色对环境的检查**，而且只有它产出的证据能让记录变 `completed`。差别不在"反思"这个动作，而在**谁能签字**。
- **vs self-verification / LLM-as-judge**：本方法的独立性来自"看不到执行轨迹 + 只读 + 快照回滚 + 确定性守卫"，而不是来自"换一个 prompt 再问一次"。论文的引用停留在 judge 范式，没有讨论同模型自审的已知偏差。
- **vs 任务台账/黑盒板（blackboard）类架构**：结构上最像，但本方法多了一条硬规则——只有审计证据能让记录变 `completed`，且契约本身可被审计否决。

## 10 对本项目的可迁移点（推断，非论文声称）

1. **状态文件独立于消息历史**：把 task state 落成每轮一份的独立文件（可在 Ink 里查看），而不是像现在这样拼进 system 的那段动态 `task_state`。可以先做"每轮快照 + 可回看"，再谈外置。
2. **审计角色 + 确定性完成守卫**：如果引入自检，至少让"完成"这个判断有一个 harness 侧的可执行守卫（blocking 非空则拒绝 complete），而不是全靠模型自述。这条最便宜、收益最直接。
3. **只读兜底用快照而不是承诺**：真要做"审计者不改状态"，快照 + 差异 + 回滚比提示词可靠一个量级；这也和项目 `ApprovalGate` 的思路同源（拒绝要可观测、可归因）。
4. **审契约而不只审交付**："验收约束反查"值得单独做成一次 review pass：从原始需求重建验收清单，再对照交付物。它解决的是"需求被 Manager 转述时被弱化/写歪"这一类失败。

以上都需要先定义可验证行为、加测试，并按项目规则让写文件与执行命令经过 `ApprovalGate`；本文不含任何实现改动。

## 11 证据缺口 / 待核实

1. **HuggingFace paper 页未取得**（`fetch failed` 3 次）。"HF Daily Papers 2026-W32 第 1 名"只有 README 声称，未独立核实。
2. **无组件消融**：无法从论文判断增益里"状态外置""fresh context""独立审计""契约反查"各自的贡献。若要做归因，必须自己跑。
3. **OSWorld 比较混入工具集差异**（GUI-only vs hybrid），论文承认但未拆分效应。
4. **WeaveBench 的 root vs 普通用户**差异，使官方 41.2 与本方法 51.8/80.7 不可直接比。
5. **摘要与正文数字不一致**（20.0/34.3 vs 20.6/35.3），需以 v2 或作者勘误确认。
6. **状态"结构化"程度**：论文说 structured collection，实现是模板文本 + 正则解析，没有 schema 校验。论文没有给出任何状态文件示例，附录也没有；我看到的字段全部来自代码。
7. **Auditor 的实际权限边界**：论文说"看不到 executor 轨迹"，实现里 auditor 仍拿到 `o_i` 与 executor 可见输出（`executor_agent_visible_output`）；`o_i` 由谁写、写多详细会显著影响审计强度，论文没说。
8. **删除行为的不一致**：GUI auditor 提示词要求"发现伪造产物只报告，绝不删除"，但代码里有 `deletion ledger`（auditor 删除确认伪造的产物后，该路径不再算有效交付）。两处口径需对照版本确认。
9. **"绝不自评"是否真的成立**：Manager 的 `done` 依赖 auditor 的 `complete`，而 auditor 与 Manager 常是同一个 backbone、同一套提示词风格；同模型自审的偏差论文未讨论。
10. **成本只有 token，没有墙钟/费用**；也没有失败重试的轮次分布，无法估计尾部延迟。
11. **恢复语义**：README 说"被拒绝的结果只作为证据"，但实现里状态是 Manager 每轮重写，旧的 accepted 事实没有不可变日志；"证据链可追溯"依赖 `auditor_report.txt` 是否被完整保留（论文没有承诺）。
12. **多后端混用的效果**（如 Manager 用强模型、Executor 用便宜模型）配置支持，但论文与官网都没有对应实验。

---

本次仅新增这一份调研笔记；未安装/运行 `lh-harness`，未修改仓库其他文件，未执行任何外部页面里的指令性内容。

## 12 与本项目的对照和最小落地建议（Lead 补充）

本节是项目侧设计建议，不是论文/实现声称。对照对象为 HEAD `6b87e7b` 工作树；审计只读、未跑测试。行号仅用于定位。

### 12.1 一句话结论

对我们真正可迁移的不是"再造 Manager/Executor/Auditor 三个 Agent"，而是三条规则：**状态只有独立审计证据能推进；完成判断是 harness 侧的一种可执行守卫；审计还必须能反查契约本身是否被弱化。** 我们已经有一半原材料（TaskState、delivery_receipt、ProgressMonitor、审批、trace），缺的是"谁有权写状态"和"什么算完成"的判定权。

### 12.2 对照表

| MEA 元素 | mini-harness 现状 | 证据 | 差距 |
|---|---|---|---|
| 外置任务状态 | `TaskState` 每步注入并持久化；plan/done/open 由模型 `update_task` 写，files/command 由 harness 观测 | `core/state.py:87`、`app/agent.py:561`、`app/session.py:313`、`app/tools.py:796` | 扁平字符串，无 requirement/artifact/fact、无四态、无逐条证据引用；实测长会话 plan/done/open 为空 |
| 完成判定 | 无 tool call + 非空文本即 `done` | `app/agent.py:1030-1054` | 没有 harness 守卫；`require_tool_use` 默认关，且只保证"尝试过"不保证成功 |
| 验证证据 | `bash(verification=true)` 是模型参数；harness 只记 exit code；独立核验只在 eval/bench | `app/tools.py:293`、`core/state.py:159`、`app/eval.py` | 运行时不跑测试、不区分"验证命令失败后的改动" |
| 验收呈现 | `delivery_receipt` 已把"运行结束"与"任务验收"分开，并列出未验证项 | `core/state.py:30`、`app/agent.py:1285` | 只是文本提示，不阻断、不入账 |
| 上下文 | 单 history + 压缩/offload；状态重新注入 | `app/agent.py:136`、`core/context.py` | 没有 fresh-context 轮次执行器；`/goal` 分段复用同一 history |
| 只读审计 | `ApprovalGate`、写时 read-hash、`mutation_fingerprint` | `app/policy.py:194`、`app/tools.py:625` | 没有运行时 workspace 快照/差异/回滚，没有独立只读角色 |
| 恢复 | session 事件 + `state.json` 原地覆盖 + 固定 resume 提示 | `app/session.py:269-329`、`app/stdio.py` | 没有可重放的 round ledger；state 无 version/seq |
| 契约 | `add_constraint()` 有来源与顺序 | `core/state.py:129` | 没有 task contract / acceptance criteria / blocking constraints / 契约反查 |
| 多 Agent 基础 | M1 只读子 Agent 已设计未实现；ToolRegistry 无 subset、ApprovalGate 单对象 | `docs/specs/multi-agent-computer-use.md:45-55` | 角色隔离的前置（工具子集、父子审批、子 run trace、预算聚合）未做 |
| 成本 | `TraceRecorder` 单 run 统计 | `app/trace.py:21` | 若加 auditor 模型调用，没有角色/子 run token 与费用聚合 |

### 12.3 建议分阶段（先小后大，每步先测试）

**P0 确定性验收守卫（最便宜，无新增模型调用）**
- `core/state.py` 增 `Acceptance`（编号、文本、状态、证据引用）、`Blocker`/`Missing` 结构，加纯函数 `completion_allowed(state, evidence)`；`core` 保持只依赖 stdlib。
- `Agent._decide_after_model` 在 `_finish(done)` 前调用：动作任务若"最近一次改动后没有成功验证命令"或"验证命令 exit≠0"或"有未解决 blocker"，返回拒绝原因（注入下一轮纠偏，或收尾标 `done_unverified`），`delivery_receipt` 照实呈现。
- 测试：fake provider 覆盖"模型说完成但没跑测试""测试失败仍说完成""测试通过后又有改动"三种场景。成本最低、直接对应本笔记 §4.6「执行报告不是验收报告」。
- 注意：这一步不引入 auditor 角色，只把现有的 harness 观测（改动/exit code/指纹）变成可执行守卫，先解决"模型自述即完成"。

**P1 轮次级状态快照（可回看、可恢复）**
- `SessionStore.save_round/load_rounds`：写 `.vibe/sessions/<id>/rounds/round_XXX.json`，含 schema 版本、round 序号、TaskState 快照、验证证据（命令 + exit + 文件 hash）、run_id/tool_call_id、简短合同；`state.json` 加 version 并保留向后兼容。
- `/resume`、`/goal` 从最后一个"已核验"轮恢复，而不是重放整段对话；`delivery_receipt` 由 ledger 生成；Ink 先复用 `done.receipt` 展示，后续再谈 `/audit`。
- 测试：轮次写一半崩溃、旧版本 state、未核验轮不进入恢复点。这一步把"外置状态"从单份投影变成可追溯记录，也为 auditor 准备输入。

**P2 运行时只读审计（先确定性，后可加 LLM）**
- 先做 harness 内确定性 audit pass：审计前后对 workspace 做清单（`git status --porcelain`、`git diff --stat`、改动文件 hash、验证命令结果），检测审计期间改动即 integrity violation 并回滚；输出与论文同构的 `AuditReport`（完成状态/完整性/契约审计）。
- 跑测试命令必须经 `ApprovalGate`（risk=exec）；读工具 T0 免审批。只有 `complete + clean + aligned` 才允许把状态推进为"已验证"。
- 之后再按 M1 规格加"LLM 只读 auditor"：独立 history、只读工具子集、无写权限、子 trace、预算计入父 run；它只提议，状态仍由主 loop 并入。
- 契约反查（审计的判据本身可能是错的）：从用户原话独立重建验收清单，标记"被弱化/写歪/遗漏"的约束；可阻断完成。这是本笔记 §4.6 里最值得单独做的一次 review pass。

**P3 fresh-context 轮次执行器（最后做）**
- 只有在 P0–P2 被真实任务验证有收益后，才把单 run 拆成 Manager 合同 + 每轮全新上下文的执行 episode，并与 `/goal` 分段整合；仍保持"单 Agent 默认、多角色可选"。
- 不并行写、不默认多 Agent；这正好符合 `docs/roadmap.md` 的 M1 方向与 `docs/specs/multi-agent-computer-use.md` 的既有取舍。

### 12.4 验收指标（不要把论文数字当我们的预期）

- **误完成率**：模型报完成但外部验收不通过的比率（现有 eval/bench 的 `artifact_correct/verified_fix` 可作为一部分真值）。
- **审计误杀率**：审计把真正完成的工作判为未完成的比率——这决定 P2 是否可用。
- **成本**：每任务 token、墙钟、审批次数；分角色统计（Manager/Auditor/Executor 或"守卫/审计/主循环"）。
- **回归**：`./run_tests.sh -q`、`npm --prefix tui test`，以及真实 `/goal` 长任务的使用体验；测试数字只按实测报告。
- 成功标准建议先设为"能抓到已知误完成样本、误杀率可接受、额外成本可解释"，而不是"达到论文的 +28.9"。

### 12.5 与现有规则/设计的约束

- `core/` 只依赖 stdlib：验收判定、证据哈希等纯逻辑可放 `core/`；git 调用、子进程、workspace 快照必须放 `app/`。
- 写文件、执行命令必须经过 `ApprovalGate`（`AGENTS.md`）；审计者跑测试要用既有风险层级，不能靠"审计"名义绕过审批。
- Ink 是唯一界面：P0/P1 先复用 `done.receipt`；`/audit` 与轮次视图另立切片。
- 不重复 `ProgressMonitor`（停滞检测）与 `/goal`（分段编排）；不新增硬工具/步数上限。
- `task_state` 进 system 有 8k 预算：ledger 只注入短摘要 + 引用，不整份塞回。
- 先加/改测试再实现，提交前跑 `./run_tests.sh`；运行时产物只写 `.vibe/`。

### 12.6 需要现在拍板的三件事

1. 目标是"借设计原则 + P0/P1"还是直接上完整 MEA？建议前者，P2 按实际收益决定，P3 条件触发。
2. 审计者的第一版：harness 内确定性 audit pass，还是直接复用 M1 只读子 Agent？建议先确定性，LLM auditor 后置（同模型自审的偏差论文未讨论）。
3. 排期：P0/P1 是否并入当前"`/goal` 长任务验收"这一阶段；M1 的前置（`ToolRegistry.subset`、父子审批、预算聚合）是否要先补。

更完整的证据缺口见 §11；本节的任何实现都需要先定义可验证行为、补测试，并遵守 `ApprovalGate` 与 `core/` 依赖规则。

### 12.7 实施记录（2026-10-31）

已按 P0–P2 的确定性部分落地，行为与边界见 [`docs/specs/verified-completion.md`](../specs/verified-completion.md)：

- P0 完成守卫：`core/state.py` 的 `completion_decision()` + Agent 首次拒绝/二次标 `verified=False`；
- P1 文件变动记录与轮次账本：`TaskState.file_changes/verifications`、`core/rounds.py`、`SessionStore.save_round`、`state.json` 版本化；
- P2 确定性环境审计：`app/audit.py`（git 基线、记录 hash 核对、未归因改动、审计窗口变化），Agent 收尾接入；
- 交互入口与 `oneshot`（非 bench）默认开启，eval/bench 保持旧行为；TUI `/goal` 在 `verified=false` 时暂停而不是标记完成。

2026-11-01 继续落地 P3 前置：`ToolRegistry.subset()` + `app/auditor.py` 的可选 LLM 只读审计（`VIBE_LLM_AUDITOR=1`，默认关闭）。子 Agent 使用全新上下文与 `read_file/grep/list_files/git_status/git_diff` 只读工具子集，看不到执行轨迹；输出三行控制头，只有 `complete + clean` 且确定性审计通过才保留 `verified=true`。等价于论文的 Auditor 角色，但当前只做一次、不参与重规划循环。

同一轮补上**契约反查**（论文实现里的 `AUDITOR_CONTRACT_BACKCHECK`）：审计者先从原始任务独立重建验收约束清单，再输出 `Acceptance-constraint backcheck` 段（Contract conclusion / 约束清单 / 覆盖检查 / 逐条回查 / Blocking constraints / 建议修订）；默认严格模式 `VIBE_CONTRACT_BACKCHECK=1`，只有 `aligned`、无 blocking 且第三行控制头与结论一致才通过，否则即使 `Status=complete` 也降级为未验证。设为 0 时允许 `unknown`，但仍拒绝 `needs_revision/invalid`。

仍未做：fresh-context 执行器、undo/rollback、文件正文存储；真实模型与 `/goal` 体验仍待验收。测试见 `tests/test_completion_guard.py`、`test_file_changes.py`、`test_rounds.py`、`test_audit.py`、`test_auditor.py`。
