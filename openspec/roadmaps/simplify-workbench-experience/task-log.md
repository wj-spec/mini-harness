# 工作台体验简化：任务记录

> 状态：ACTIVE；阶段 1–4 工程实现完成，真人体验待确认项保留
>
> [整体设计](design.md) · [实施路线图](roadmap.md)

## Review 处置

2026-11-13 恢复：strategy 与 plan-eng 均 completed，本轮前缀回执匹配；STRATEGY-01 ✅ 采纳，在 design 决策 2、4 补充系统/用户/开发循环比较与主次。plan-eng findings=[]。此前 voice-fallback 有效结果保留，runner=claude reason_code=runner-error，使用同宿主降级意见。历史故障记录保留，不再作为当前阻塞。

本轮 result_ref：.vibe/roadmap-review/ux-recovery-20261113/{strategy,plan-eng}.md；requested_model=gpt-6.1-sol、requested_effort=medium，实际 effective_effort=unknown。

以下为方案初稿时的历史故障，已由上述恢复记录解除：

🔸 **当时未审待恢复**：strategy 请求失败并重试后仍返回 Bad Request；plan-eng 返回 Bad Request。未取得有效独立评审结果，不能标为通过。

runner=claude reason_code=runner-error：outside-voice 退出 1，报告 Not logged in；同宿主降级 fallback 已完成，首行前缀与本轮匹配，findings=[]；不是跨模型意见，也不替代失败的双镜。未登录故障不要求用户此刻处理，可在恢复评审工具后继续。

恢复方式：在工具可用的会话中重跑 strategy 与 plan-eng，再逐条处置 findings。voice 已按技能完成同宿主降级，不把外部登录作为继续工作的必要条件。本轮不因评审故障修改用户系统配置，也不启动开发。

## 2026-11-13

### 阶段 0 / 整体方案起草

- 对应 roadmap.md 概览：整理回执分层、配置契约、功能指引和应用内设置五阶段；前两阶段详细规划。
- 需求来源：用户要求先给整体方案、记录文档，再着手开发；“本轮回执”问题已澄清为每轮结束重复出现。
- 路由：存量项目体验演进；gate-0 的范围、痛点、目标、近期划分和备选取舍已有讨论及代码依据；无需求真实性未定/变现信号，选直接生成，新增拷问维度为空。开放源代码是分发背景，不是本轮商业化验证目标。
- 新建 simplify-workbench-experience，保留 improve-workbench-ux 原规划与验收历史；重入扫描未发现待续 DRAFT 文档。
- 本轮只落文档和文档评审，不修改运行行为、不提交、不启动实现。此前工作区改动保持原状。
- 运行产物按用户 AGENTS.md 放入 .vibe/，覆盖技能默认的 .outside-voice 运行目录；正式文档放 openspec/roadmaps/。
- 后续：用户阅读方案后，从阶段 1 开始细化 spec 和开发。
- 耗时未计量，不填写推测时长。

### 评审派发记录

run_id=ux-plan-20261113；规则根=/Users/endless/.sdflow/workflow。
模型解析本轮执行一次：host=codex；mid=gpt-6.1-sol / medium；voice=claude / opus（high）。Codex 通则承载为 not-carried，使用生成的前缀文件。

prefix_path=.vibe/roadmap-review/ux-plan-20261113/prefix-principles.md；prefix_version=principles-b9a054bafa9c。双镜派发截止记录：2026-11-13 06:24 UTC（各自派发后 60 分钟上限）；voice timeout=300s，fallback 若触发则本轮预算 300s。Codex 闹钟任务 ID 不适用。

| task_id | 角色 | 请求模型 / effort | 上下文模式 | result_ref | 状态 |
| --- | --- | --- | --- | --- | --- |
| strategy | 计划完整性与范围 | gpt-6.1-sol / medium | fork_turns=none，只读三件套 | .vibe/roadmap-review/ux-plan-20261113/strategy.md | failed：Bad Request（含一次重试） |
| plan-eng | 工程可行性与跨域风险 | gpt-6.1-sol / medium | fork_turns=none，只读三件套 | .vibe/roadmap-review/ux-plan-20261113/plan-eng.md | failed：Bad Request |
| voice | 独立模型意见 | claude opus / high | 同步净化 context | .vibe/roadmap-review/ux-plan-20261113/voice.md | failed：未登录，退出 1 |
| voice-fallback | voice 失败时同宿主兜底 | gpt-6.1-sol / medium | fork_turns=none，只读 | .vibe/roadmap-review/ux-plan-20261113/voice-fallback.md | completed：findings=[]，本轮前缀回执匹配 |

### 方案初稿收尾核对（历史状态）

① 未通过：独立评审故障，包保持未审待恢复；结构检查只能证明 Review 处置小节存在且非空，不能证明评审已完成或每项已处置。

② 通过：三件套链接互通；阶段 1、2 回指对应 design 决策；阶段 0 记录对应 roadmap 概览。

③ 通过：三件套没有引用历史讨论存档。

④ 不适用：直接生成路径，无历史讨论备忘、无全局 ADR/术语写入。后续待决事项在 design 第 5 节附触发条件。

文档检查：3 份 Markdown 本地链接与围栏检查通过；git diff --check 通过。另生成可浏览评审页 review/roadmap-review.html，仍标记方案草案。本轮没有改产品行为，未运行产品测试；后续提交产品改动前仍需 ./run_tests.sh。未执行 git add / commit。

评审参数说明：requested_effort=medium；fallback 的参数获接口接受且结果完成，effective_effort=unknown，未取得实际档位元数据。双镜失败无成功接受证据；未尝试猜测模型或 effort 回退。

### 阶段 1 / 开始实施

用户明确批准推进；新建 implement-simplify-workbench-experience-p1，分支 codex/receipt-ux-p1。已有未提交的 Logo、启动和 README 工作保留，本次不自动提交。评审四项当前结论：①通过（STRATEGY-01 已处置）②通过 ③通过 ④不适用；机械小节检查不等同逐项审查。

### 阶段 1 / 工程交付

- 已实现结构化 outcome 与同任务续段聚合、最终计划验收覆盖、轮次/检视持久化，以及 Ink 共享展示。普通问答静默，变更紧凑，异常可见，原始证据保留。
- Python 全量 1987 passed / 4 skipped；末次字段校验后 outcome 与 core 边界 10/10；Ink build + 低并发全量 760/760。默认并发曾发生固定等待失败，独立复跑通过，原日志与处置保留。
- 实际 PTY Demo 两次单键审批后写文件并展示紧凑结果，退出 0。OpenSpec strict 与 diff 检查通过。
- [阶段 1 验证记录](../../changes/implement-simplify-workbench-experience-p1/verification.md)列出命令、日志、场景及限制。真人连续使用尚未确认，阶段 1 不标整体验收。阶段 2–5 未开始。
- 继续保留已有工作区修改；未 commit、push 或归档。

### 阶段 2 / 配置分层实施

用户明确要求开始下一阶段；阶段 1 真人体验项继续保留，不作为阻塞本次配置整理的条件。新建 implement-simplify-workbench-experience-p2，复用当前工作区，不改用户 .env。

已完成最小模板、默认服务只填 Key、用户/开发/契约三份配置文档；47 个原变量逐项核对。VIBE_LLM_MAX_OUTPUT_TOKENS 未接入，删除示例并纠正说明；goal 段数/时长的 0 不代表无限，文档按现行前后台行为解释。45 项专项测试和免 Key 真实 PTY Demo 通过，全量最终复验中。

### 阶段 2 / 工程交付

最终 Python 全量 1993 passed / 4 skipped；首轮文档锚点错误已修正并保留失败日志。专项 45 项与新目录 start.sh --demo 实际 PTY 通过，不请求付费 API、不创建 .env。OpenSpec strict、diff 和链接检查通过。未修改 Ink 源码，本轮未重复 Ink 全量；未把复用依赖的 Demo 当成冷安装验证。

交付与限制见[阶段 2 验证记录](../../changes/implement-simplify-workbench-experience-p2/verification.md)。下一阶段是功能区与情境指引，尚未开始。本轮未提交、推送或归档。


### 阶段 3 / 功能区与情境指引交付

用户明确授权继续；复用当前工作区，建立 implement-simplify-workbench-experience-p3。核对现有按键与能力后使用 Ctrl+T；主区只复用一行提示，主动打开五区指引。阶段 1、2 的真人体验缺口保留。

实现情境提示开关、只填空草稿的三类示例、已有计划/检视/任务/权限入口；能力缺失不提供动作，审批优先，返回保留中文草稿光标和阅读位置。关闭提示仅影响当前工作台实例；不扩展到配置持久化和模型编辑。

Python 全量 1993 passed / 4 skipped；Ink 最终全量 771/771。首次 Ink 769/771 暴露命令菜单顺序和验收提示优先级回归，修正后完整复验，未削弱原断言。真实 80×24 PTY Demo 经指引开关、草稿保护、两次审批、写入和紧凑结果后退出 0；未创建 .env，也没有付费请求。

证据与边界见[阶段 3 验证记录](../../changes/implement-simplify-workbench-experience-p3/verification.md)。阶段 3 工程交付，真人常用终端体验另列；下一阶段为应用内模型设置，尚未开始。未提交、推送或归档。


### 阶段 4 / 应用内模型配置交付

用户授权继续，建立 implement-simplify-workbench-experience-p4，复用安装目录 .env 和现有 ApprovalGate；阶段 1–3 真人体验缺口不抹去。实现 /model、配置帮助入口、来源解释、隐藏 Key、文件版本复核保存、显式最小请求检测，以及普通 OpenAI 兼容连接空闲应用。

运行中不替换客户端；Demo、自定义 provider、额外审计需保存后重启，后台进程不热更新。新 trace 记录非敏感模型快照，恢复不匹配时阻止调用并列出原模型/服务/代理；旧记录的完整来源无法补证，保留兼容提示。

Python 最终 2007 passed / 4 skipped，Ink 778/778。真实 PTY 在隔离目录保存虚构 Key、返回 Demo 草稿并完成两次工具审批和验证，退出 0；没有修改用户 .env 或发起付费请求。保存成功后错误提示未保存的问题已修正并新增回归。失败日志与范围见[验证记录](../../changes/implement-simplify-workbench-experience-p4/verification.md)。

阶段 4 工程交付；真实服务与真人终端体验继续列为待确认。下一阶段是任务限制与高级设置，尚未开始。未提交、推送或归档。


### 阶段 5.A / 设置中心与单段限制交付

2026-11-13，用户授权继续。按阶段 5 原有“内聚能力分批”原则，先落实 /settings 与四项单段限制；goal 总预算和 MCP 生命周期分别列为 5.B/5.C，不把只读状态当作编辑完成。

新增限制来源、审批保存与空闲应用，复用模型 .env 原子写入；task/goal 首次运行 trace 保存限制快照，续做保留原值，缺失快照明确提示启动限制，损坏快照受控停止。设置页接入模型、权限，数字校验与三尺寸、中文草稿和返回路径通过测试。

Python **2021 passed / 4 skipped**，Ink **785/785**。真实 PTY 通过三次审批：设置保存一次、Demo 写文件与检查两次；断言配置保留虚构 Key、无回显、新任务快照步数为 12。具体证据和红灯修复见 [5.A 验证记录](../../changes/implement-simplify-workbench-experience-p5/verification.md)。

阶段 5.A 工程完成，阶段 5 整体尚未完成；下一批为 goal 总预算与前后台恢复契约。真人、真实服务和 MCP 生命周期验证继续另列。未提交、推送或归档。

### 阶段 5.B / 目标总预算与前后台恢复交付

2026-11-13，用户授权推进下一批。新增 /settings 的目标分组（g 切换），保存和应用独立；后端创建目标时固定四项预算，所有续段在开始前按原起点与账本累计检查。前台投影和重启恢复按任务快照对齐，后台环境放宽不覆盖已有目标预算，手动恢复不绕过已耗尽的总额度。

补齐旧目标迁移：身份匹配的 goal.json 优先，旧 GoalRecord 非零已知值可用，含义不明的零值不取消启动保护；损坏快照拒绝续做。真实 worker 验证 1 段已耗尽目标在环境改 999 后仍无新段或模型文本。

最终 Python **2039 passed / 4 skipped**，Ink **790/790**。真实 PTY 完成目标预算改 3、审批保存与应用、新 goal 写入和检查、退出 0。全量中暴露的既有 TCP 单次 recv 假设已修正为完整读取，保留失败和最终日志。见 [5.B 验证记录](../../changes/implement-simplify-workbench-experience-p5b/verification.md)。

5.A/5.B 工程完成，阶段 5.C MCP 生命周期与高级编辑尚未实施；真人与跨平台体验继续另列。未提交、推送或归档。

### 阶段 5.C 首批 / MCP 连接管理

2026-11-14，用户授权继续。先交付 /mcp reload、/mcp disconnect 和安全状态查询；候选连接全部成功后才替换，失败保留原连接与工具。取消和清理失败路径有回归，后台连接不变。JSON 配置仍在原文件编辑，应用内编辑器未交付，5.C 不标整体验收。证据见 [说明](../../../docs/specs/mcp-lifecycle.md)。


### 阶段 5.C 第二批 / MCP 基础配置编辑

2026-11-14，用户授权继续。接入 /mcp edit（别名 /mcp config）及设置中心 m 入口；已有文件原位编辑，首次使用复用 .env，进程环境覆盖只读。保存、删除与重载明确分开，保留高级字段，输入不回显，旧环境留空保留，更换命令/参数必须重填环境。版本冲突、审批拒绝和保存失败保留草稿。

真实本地 MCP 验证保存不启动、重载后调用、删除后等待重载移除工具；隔离 PTY 经三次审批完成保存→连接→断开→返回草稿→正常退出。初次全量发现命令描述目录遗漏，补齐别名；边界走查补齐服务名与 servers/mcpServers 包装字段冲突，失败日志保留。最终数字和日志见[验证记录](../../../docs/specs/mcp-lifecycle.md)。

5.C 仍保留逐服务独立连接、高级字段编辑及真人/远程 MCP 验收；未修改用户配置，未提交、推送或归档。

最终回归：Python **2053 passed / 4 skipped**（1 条既有 tarfile 警告），Ink **797/797**。日志为 .vibe/mcp-editor-python-verified.log、mcp-editor-ink-final.log；实际终端结果为 mcp-editor-smoke.log，终端帧为 mcp-editor-terminal.log。


### 阶段 5.C 收束 / 交接真人验收

2026-11-14，用户授权持续推进至需要本人验收。已完成基础/高级分组和状态投影、当前/全部操作范围、单服务连接生命周期及旧临时授权清理。取消、拒绝、失败回退、另一个服务持续可用均有回归。

新增 ./start.sh --acceptance，沿用 ApprovalGate，隔离用户配置与工作区，提供 Demo 模型和本地 alpha/beta MCP。实际 80×24 PTY 七次审批验证启动、高级保存、单服务重连/断开、原草稿返回、Demo 文件与验证、退出 0；beta 进程标识在单服务操作期间不变，退出后进程组记录清空。

最终 Python **2060 passed / 4 skipped**（既有 tarfile 警告），Ink **802/802**；日志 .vibe/workbench-acceptance-python-final.log、workbench-acceptance-ink.log、workbench-acceptance-smoke.log。取消测试初版误用子协程主动取消，现改为取消外层管理任务，保留首轮失败证据。

工程准备已完成，真人体验未勾选；已提供 [10–15 分钟验收清单](../../../docs/WORKBENCH-ACCEPTANCE.md)。真实远程服务、跨平台冷安装与其他路线旧待办继续单列。不修改用户配置、不提交、不推送、不归档。


### 后续 UI / UX 方案待审（2026-11-14）

用户在隔离验收入口遇到开场 MCP 审批，明确希望从真实场景使用，并讨论首次配置与 UI 引导。现按用户要求记录[完整方案](../../../docs/specs/first-run-onboarding-ui-ux.md)：首次必要配置、真实首页、设置分类、主动 MCP 连接、审批信息层次、技术状态边界、分批实现与验收矩阵。

本轮仅撰写文档并补导航，状态为待用户审阅。未开发新行为，未将已完成工程或真人验收状态改写；待用户审阅后再启动方案中的 A–E 批次。
