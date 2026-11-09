# 计划模式 UX 复盘与改进方案

状态：2026-11-08 复盘与方案（未实施）。当前已交付的 plan 模式能力见
[计划审批门](plan-approval.md)、[结构化 Plan 产物](plan-artifact.md)、[任务模式](modes.md)；
本文件只讨论**交互层**改进，不改变硬只读 profile、计划 artifact/hash/审计与执行证据链。

实施进度：工作台 UX P3 已接通准确计划审阅卡、显式批准执行、正文分页与未知结果查询。恢复修复后，真实模型已完成受控单文件计划、两次工具审批与实际验收（exit=0），原会话离线重开未重复运行；由样本发现的风险提示缺口已用回归修复。最终 Python 1817 passed / 4 skipped、TUI 497/497。Ghostty 真人与学习 Review 仍待确认，详见 [P3 验证记录](../../openspec/changes/implement-improve-workbench-ux-p3/verification.md)。本报告原始问题与方案作为历史保留。

P4 当前增量：修订记录与重开反馈恢复已接通；成功回执丢失及另一后端发布新版后的恢复已通过实际进程验证，查询或拒绝不会重复调用模型。新增两项集成回归后 TUI 540/540；产品代码未变，前轮 Python 1885 passed / 4 skipped 和三尺寸恢复 PTY 证据继续适用。未发送反馈仍仅进程内保存；真实模型修订质量和 Ghostty 真人体验待验收，见 [P4 验证记录](../../openspec/changes/implement-improve-workbench-ux-p4/verification.md#实际进程故障恢复补验2026-11-08)。

P5 当前增量：命令入口与可用原因已接通，当前运行/下一次默认模式分别显示，运行中可选择或撤回 pending，重开不自动运行且说明草稿范围。全量 TUI 561/561、Python 1888 passed / 4 skipped，三尺寸实际 PTY 通过；本轮补跑并修正跨语言检查，早期 Python 结果复用的证据缺口已更正。80×24 固定样本正文从 8 行增至 12 行，三尺寸原生鼠标拒绝通过；真人验收仍待完成，见 [P5 验证记录](../../openspec/changes/implement-improve-workbench-ux-p5/verification.md#紧凑布局与原生点击2026-11-08)。

最新体验反馈：用户确认 P3 生成/阅读/执行/拒绝演示无问题，但认为快捷键审批体验很差。已优先增加可点击的工具审批按钮、更多授权、Tab/方向键导航和草稿/光标保护；新按钮真人体验待验收，P3 原生恢复及学习 Review 未确认。自动 TUI 509/509、Python 1877 passed / 4 skipped，三种尺寸实际 PTY 通过，见 [审批按钮证据](../../openspec/changes/implement-improve-workbench-ux-p1/verification.md#审批按钮增量2026-11-08)。P4 剩余范围保持。

设计来源：2026-11-08 对 `/plan` 实装的复盘、DSH plan mode 对比
（见 [真实 P1 记录](real-p1-plan-smoke-2026-11-07.md) 的背景）与
[产品 UX 复盘](../research/product-ux-review-2026-10-31.md) 的 plan mode 结论。

## 1 结论摘要

`/plan on|off|status` 不是 plan 模式的用户需求，而是**兼容既有 `/plan` 语义的实现开关**：
`/plan` 原本是“查看最近计划”，`/plan <任务>` 是“一次性只读规划”，为了再加入会话级粘性模式，
我们把内部状态机（active/pending/wanted）直接暴露成了命令。这是当前首要 UX 债。

目标交互模型：

- **一次表达意图**：`/plan <任务>` 直接进入规划并提交，不要求先 `on`；
- **审阅卡即操作**：计划提交后在对话流中出现 review 卡，直接 Approve & execute / Request changes / Dismiss；
- **反馈能驱动修订**：Request changes 自动发起计划修订 run，而不是只写一个 draft 文件；
- **批准后一步执行**：批准与创建执行 run 聚合；
- **粘性模式可见可控**：默认一次性；粘性模式由可见控件/快捷键开关，会话恢复时有显式提示；
- **命令面收敛**：`/plan` 不再承载 17 个平铺动作，查看计划与计划管理归入 `/plans`/覆盖层菜单。

## 2 当前用户旅程与摩擦

| 用户意图 | 当前路径 | 摩擦 |
|---|---|---|
| 先规划再执行 | `/plan on` → 输入任务 → 等待 → `/plan` 打开覆盖层 → `y` → `/plan execute` | 5 步；`on` 与任务描述分离；批准与执行断成两个动作 |
| 修改计划 | `/plan` → `r` → 输入反馈 | 反馈只写进 v+1 draft，不触发模型修订；用户需重新描述任务 |
| 临时问一句 | 先 `/plan off` | 粘性模式吞掉普通消息，缺少“只这一条用 act”的逃生 |
| 查看当前计划 | `/plan` | 与“开启模式”同名，命令本身无法区分意图 |
| 批准后继续 | `y` 退出 plan mode | 执行还要 `/plan execute`；批准意图与执行意图脱节 |
| 重启会话 | 静默恢复 plan mode | 下一条普通消息突然变只读规划，没有恢复确认 |
| 运行中切模式 | 命令被 `busy` 拒绝 | `pending` 状态可能显示，但 TUI 无法产生；展示了一个操作不到的状态 |

## 3 UX 缺陷

### 3.1 `/plan` 一名多义、动作数量爆炸

`/plan` 同时承担查看（`/plan`、`/plan all`）、一次性规划（`/plan <任务>`）、
粘性模式（`/plan on|off|status`）、审阅（`approve|reject|request-changes|dismiss`）、
计划管理（`list|step|edit-step|diff|acceptance|card|execute|background|revise|prepare-background`）。
后端校验串里有 **17 个 action**（[stdio.py:1815](../../app/stdio.py#L1815)），
补全菜单 38→44 项（[protocol.ts:20](../../tui/src/protocol.ts#L20)）。
用户需要记住“同一个命令词在不同上下文代表不同对象”，学习成本与误操作风险都高。

### 3.2 常见路径步骤多，且关键断链

- 规划路径：`/plan on` + 输入 + 等待 + 打开覆盖层 + `y` + `/plan execute`，共 5 个用户动作；
- **Request changes 断链**：`request_changes` 只生成 v+1 draft 并写入
  `Plan.review_feedback`（[plan_review.py:123](../../app/runtime/plan_review.py#L123)），
  没有把反馈送进模型上下文、也没有启动修订 run；模型看不到反馈，
  用户看到的“已生成 v2 draft”不等于“计划被改好”；
- **批准与执行断链**：批准只退出 plan mode，执行仍需 `/plan execute`；
  批准一个计划通常意味着“可以开始做”，当前没有把这两个意图聚合；
- 计划提交后 plan mode 仍开启（`submit_plan` 成功收尾后只发 notice），
  用户想讨论计划时下一条普通消息又会创建 plan run。

### 3.3 模式状态可见但不可操作

- 当前只有 paneTitle/hint/placeholder 文字（[app.tsx:1761](../../tui/src/app.tsx#L1761)），
  没有可聚焦/可点击的 Plan 控件；退出只能记住 `/plan off`；
- `pending` 在 TUI 几乎不可达：运行中 command 被 `busy` 拒绝，用户无法在 run 中排队切模式；
  但 UI 仍可能显示“计划模式（待生效）”，展示了一个用户操作不到的状态；
- 会话状态持久化后会静默恢复，下一条普通消息突然只读规划，没有恢复确认；
- 显式 `/act` 只覆盖一次 run，不关闭粘性 plan mode；用户以为切走了，下一条又回到 plan。

### 3.4 审阅动作没有成为“第一类界面对象”

计划提交后用户看到的是 notice 和命令提示，而不是一张可操作的审阅卡：

- 计划正文不在对话流直接展开，要打开 `/plan` 覆盖层；
- 批准 `y`、拒绝 `n`、改反馈 `r`、审阅稿 `m` 都是覆盖层按键，hint 一行已有十余个键
  （[app.tsx:1765](../../tui/src/app.tsx#L1765)）；
- 没有“Approve & execute”聚合动作，也没有“Request changes → 自动修订 → 新卡”的连续反馈；
- 工具审批有一套面板（y/s/p/n），计划审阅又是覆盖层/命令，两套交互语法并存。

### 3.5 概念层级过多，信息架构分散

`plan mode` / `plan task` / `plan artifact` / `plan execution` 是四个不同生命周期的对象：
mode 决定下一条消息怎么跑，task 是执行载体，artifact 有版本/hash/审批状态，
execution 有 plan_id/version 绑定与 step ledger。文档里清晰，但 TUI 没有一个统一的
“当前计划”视图把它们串起来，用户要在 title、notice、`/plan`、`/tasks`、`/evidence` 之间拼图。

### 3.6 可发现性、一致性与可访问性

- `/help` 是一整段长文本，新增 plan 命令后更长；覆盖层按键没有分组和“下一步建议”；
- 补全菜单把 6 条 plan 子命令平铺到顶层，`/plan` 与 `/plan on` 等并列；
- 审批卡、审阅稿、执行入口分散在命令、覆盖层、命令菜单三处；
- 终端仅键盘操作，但缺少聚焦/按钮式 affordance；错误恢复靠 notice 文本
  （如“用 /plan card 或 /plan markdown 查看”），用户要记命令而不是界面给动作。

### 3.7 缺少模式逃生与一次性覆盖

进入 plan mode 后，临时问问题、跑 `/act`、改配置没有低摩擦路径：

- 必须 `/plan off`；
- 没有“只这一条用 act”的快捷方式；
- Esc 目前只关覆盖层，不退出粘性模式；
- 没有“当前模式只影响下一条消息”的确认/撤销。

## 4 目标交互模型

原则：**模式是可见控件，不是要记住的命令；一个动作对应一个意图。**

| 维度 | 目标交互 |
|---|---|
| 入口 | `/plan <任务>` 一步进入并提交；粘性模式由 composer 的 Plan 控件/快捷键开关，命令只作 fallback |
| 查看 | 查看计划改为 `/plans` 或 `/plan view`，与“进入规划”解耦 |
| 状态 | 默认一次性规划；粘性模式 opt-in；composer 显示 `PLAN / pending / OFF`；会话恢复显式 banner |
| 审阅 | 计划提交后在对话流出现 Plan review 卡：`[a] Approve & execute`、`[r] Request changes`、`[d] Dismiss`、`[v] 版本差异` |
| 修订 | Request changes 自动发起修订 run：携带原计划、反馈、最近只读探索上下文；生成同 plan_id 的 v+1 proposed 或明确的新版本 |
| 执行 | Approve & execute 一步创建绑定计划的 act run；只批准不执行作为次级动作 |
| 命令面 | `/plan` 顶层只保留入口；管理动作收进覆盖层菜单或 `/plans` 子命令；补全不膨胀 |
| 逃生 | Esc / 可见控件退出粘性模式；提供“只用 act 跑这一条”的覆盖动作；pending 要么真实可排队，要么不展示 |

## 5 优先级落地方案

### P0（下一轮优先，建议 2 个切片）

#### P0-1 一步入口 + Plan review 卡 + Approve & execute

- 目标：`/plan <任务>` 一步进入；计划提交后在对话流直接出现 review 卡；
  卡上显示计划标题、摘要、版本 hash、完整正文入口和动作；
  `a` 一步完成“记录批准 + 创建绑定计划的 act run”，不需要 `/plan execute`。
- 后端：沿用现有 `plan approve` 与 plan 绑定执行路径；TUI 从最新 artifact 读取
  plan_id/version/hash，批准成功后按 `/plan execute` 相同路径创建 act run；
  只批准不执行作为次级动作。无需新增协议动作。
- TUI：新增 plan-review entry（复用现有 entry 渲染），动作键
  `a` 批准并执行、`d` 关闭、`v` 版本差异；`/plan <任务>` 不再需要先 `on`；
  `/plan approve|reject` 保留迁移窗口兼容。
- 验收：S1、S4；无任务运行时/无可用计划时给出明确错误，不静默丢失批准。

#### P0-2 Request changes 闭环

- 目标：用户反馈后自动跑一次“修订计划”run，模型基于原计划 + 反馈 + 只读探索产出新版，
  完成后替换为新 review 卡；不再只写 draft。
- 后端：plan run 支持待修订上下文（`plan_id`/`plan_version`/`review_feedback`），
  `submit_plan` 支持同 plan_id 的 v+1（或明确记录新 plan_id 的替代关系）；
  plan policy 提示词要求先读原计划、逐条回应反馈。
- TUI：review 卡 `r` 输入反馈后直接触发修订 run 并显示“修订中”；完成后刷新卡片。
- 验收：S2；审计记录 `plan_changes_requested` 与新版本 propose/approve 链。

### P1（P0 后一个迭代）

1. **粘性模式控件与恢复提示**：composer/status 显示 Plan 控件（开关、pending、退出键）；
   默认一次性，粘性 opt-in；重启恢复时显式 banner，不静默继承。
2. **pending 语义收敛**：运行中允许排队切换并在下一段生效，或移除 pending 展示；
   不允许 UI 展示用户操作不到的状态。
3. **命令面收敛**：查看计划迁到 `/plans` 或 `/plan view`；`list|step|diff|acceptance|card|edit-step`
   收进覆盖层菜单；补全只保留入口命令。
4. **计划正文与版本 diff 进入 review 卡**；失败/超时/无审阅通道的 fail-closed 提示可见。
5. **模式逃生**：Esc 退出粘性模式、“只用 act 跑这一条”的覆盖动作、`/act` 显式清除粘性模式。

### P2（后续）

- 计划审阅与工具审批共用通用 review/question seam（出现第二个实际使用者后再抽）；
- 多计划/多版本导航、跨会话计划历史；
- 可配置键位与更完整的终端可访问性；
- 真实用户可用性测试（当前仅确定性/协议层验证，UX 结论来自走查与 DSH 对比，未做真人测试）。

### 依赖与顺序

1. P0-1（入场/review 卡/批准即执行）先落地，闭合主旅程并给 P0-2 提供承载界面；
2. P0-2（修订 run）依赖后端 plan-run 修订上下文与 submit_plan 版本语义；
3. P1 的粘性控件/命令收敛建立在 P0 两步完成之后，避免同屏多套入口。

### 非目标

- 不改变 plan profile 的硬只读工具边界；
- 不把计划审阅降级成纯提示词；
- 不删除计划 artifact/hash/audit 与执行 step ledger；
- 不在 P0 阶段重写整个 TUI 状态机或引入 Web composer。

## 6 验收场景

- **S1 一步规划并执行**：用户输入 `/plan 实现 X` → 只读规划 run → review 卡出现 →
  `a` 批准并执行 → 创建绑定计划 act run；全程不需要 `/plan on` 或 `/plan execute`。
- **S2 反馈驱动修订**：review 卡 `r` 输入“补失败路径测试” → 自动发起修订 run →
  模型基于原计划与反馈产出新版 → 新 review 卡出现；审计可见请求/新版本链。
- **S3 查看不误入**：`/plans`（或 `/plan view`）只展示计划，不开启模式、不创建 run。
- **S4 粘性模式可见可控**：控件显示 active/pending；Esc 或控件退出；
  运行中切换按约定排队或明确拒绝；重启后显示恢复 banner。
- **S5 模式逃生**：plan mode 开启时“只用 act 跑这一条”不改变粘性状态；
  普通问题不会被误当作规划。
- **S6 兼容回归**：现有 `/plan approve|reject|request-changes|dismiss|markdown` 与
  `/plan execute` 在迁移窗口内仍可用，旧 session 的 plan 产物仍可读写。
- **S7 失败关闭**：审阅通道不可用/控制器重载时保持 proposed 状态并提示，
  不自动实施、不假装批准。

## 7 与路线图、里程碑的关系

- 本文件是 [roadmap](../roadmap.md) 中“计划模式后续方向”的交互层细化；
  P0 是下一轮优先，P1/P2 为后续。
- 本次 2026-11-08 已交付的 P0/P1 能力（会话开关、审阅三态、plan policy、submit 门禁）
  保持；本方案只替换交互承载方式，不回退安全/证据机制。
- 已有证据：Python 1663/1650/13、TUI 427/426/1（pty 环境失败），见
  [里程碑](../MILESTONES.md)。
