# 产品层与用户体验对照：mini-harness vs opencode vs Claude Code

- 日期：2026-10-31（Asia/Shanghai）
- 对象：本项目（本地终端 Coding Agent，Python 后端 + React/Ink TUI，单 Agent，`pyproject` 里叫 `vibe`，README 里叫 mini-harness，v0.1.0 个人项目）
- 对照：**opencode**（2026-10 完成 V2 重构：主线 `@opencode/cli` v2.2.5 / 旧线 v1.19.5，MIT）与 **Claude Code**（v2.1.316，2026-10-31 发布，闭源商业产品）
- 方法：三份团队一手调研（opencode / Claude Code / 本仓 UX 审计）+ Lead 交叉核对；代码基线 HEAD `6b87e7b` + 工作树（含 10-31 未提交的完成守卫/轮次账本/环境审计）
- 证据与长文：[opencode 笔记](../../.vibe/ux-review/opencode.md)、[Claude Code 笔记](../../.vibe/ux-review/claude-code.md)、[本仓 self-audit](../../.vibe/ux-review/self-audit.md)、[证据 artifacts](../../.vibe/ux-review/artifacts/)
- **局限（先声明）**：本次评估未复跑真实模型任务（仓库 W1–W5 有历史真实验收，但本轮无 API key 未能复验）、未做干净 clone、沙箱禁止 PTY（全屏交互未真机验证，用协议/组件渲染/仓库 fixture 三层替代）；竞品未实机运行，事实来自官方文档、changelog、GitHub issue 与社区讨论；**评分是判断，不是测量**。竞品版本在 2026-10 仍高速变动，结论只代表 2026-10-31。

---

## 0. 结论（先看这一页）

**一句话**：如果按"产品"标准比，我们不在 opencode / Claude Code 的竞争层级——它们是成熟产品，我们是**机制很强、界面没跟上、还没产品化**的开发者原型；如果按"单 Agent、本地、中文、过程可核对"这个细分比，我们有一项**公开资料里未见同类机制的证据链资产**（只能说"未见"，不能断言对手"没有"）。

**三句评价**

1. **能力层**：核心编码 loop 已真实可用（W1–W5 真实验收），审批层级、策略文件、授权档位、完成守卫、轮次账本、环境审计在 2026-10-28~31 集中落地，后端机制**领先于界面表达**。
2. **体验层**：现在给一个全新用户，**3 分钟首用不成立**（6–8 步安装 + 3 个环境变量 + 必须 TTY）；日常使用"能用但费神"——信息密度高、审批默认逐次、新的授权档位在 Ink 里不可达、`verified=false` 只在收尾 notice 的一段文字里出现（无徽标/警示色，页脚仍写"任务完成"）。
3. **产品层**：我们还没有"产品"该有的东西——一键安装、引导、分发、文档-代码一致性、生态位、稳定的默认体验；但这些恰好都可以后补，而"可核对"这件事别人补起来比我们难。

**评分摘要（10 维加权，满分 5；依据见 §4）**

| 产品 | 综合体验分 | 功能覆盖 | 体验成熟度 | 差异化价值（对本用户） |
|---|---|---|---|---|
| Claude Code | **4.26** | 4.8 | 4.3 | 3.8 |
| opencode | **3.71** | 4.4 | 3.6 | 3.7 |
| mini-harness（我们） | **2.79** | 2.4 | 2.6 | **4.0** |

> 读法：我们与 opencode 差约 0.9 分、与 Claude Code 差约 1.5 分，差在"产品化/生态/交互范式"。拆开看更准确：**机制（可核对/审批模型）我们领先**，但**用户可见的验收呈现与日常顺手程度我们落后**（§4 第 5、6 维）。功能覆盖/体验成熟度/差异化是判断性再归组，不是独立测量。

**最该马上做的三件事**

1. **把 `verified` 变成一等 UI 状态**：`done` 时区分"已验证 / 待验证"，守卫拒绝时通知用户，加 `/evidence` 入口。否则"结果可验收"在界面上仍只是一行收尾文案。
2. **打通审批体验闭环**：`--autonomy` 透传到 Ink、档位徽标、审批卡显示"层级 + 命中规则 + 后果 + 总是允许会写入什么"、`/permissions` 可看可撤。否则用户反馈的"频繁审批"等于没解。
3. **补首用预检与引导**：`--doctor`、`.env.example`、fake 模式跑一次完整"写文件→审批→验证→回执"演示、README 三段式快速开始。否则每个新用户的第一步都是踩坑。

---

## 1. 产品层面对比：先判断"是不是同一类产品"

| 维度 | mini-harness | opencode | Claude Code |
|---|---|---|---|
| 产品形态 | 本地单机、单 Agent、Ink 全屏工作台 | 开源平台：TUI / 桌面 / Web / IDE / Server / SDK / 插件 / GitHub | 商业产品矩阵：CLI + VS Code / JetBrains + Desktop + Web + Mobile + Agent SDK + CI |
| 成熟度 | v0.1.0，个人项目，W1–W5 真实验收 | 223k stars，V2 刚完成大重构，日更 | v2.1.316，约 1.2 天/版，企业级交付 |
| 分发 | 源码 + `uv sync` + `npm ci` + 环境变量 | 一行安装（curl / brew / npm / 二进制 / Docker） | 一行安装（脚本 / brew / npm / 包管理器）+ 浏览器登录 + 自动更新 |
| 商业模式 | 自用，无 | MIT 免费；Go $10/月起；Console 企业 | Pro $20 / Max $100 / Ultra $200；Team/Enterprise |
| 差异化主张 | 过程可核对、结果可验收、本地、中文；受控浏览器动作（早期） | 模型中立（75+ providers）+ 可编程/可嵌入 | 最强扩展生态 + 多端 + 企业治理 + 默认自动化 |
| 主要用户 | 作者本人 / Agent 学习者 | 多模型、反绑定、TUI 重度用户、集成开发者 | 企业团队、终端工程师、轻编程用户 |

**判断**：三者共享同一个骨架（loop + 工具 + 上下文 + 审批 + 会话 + 可观测），但阶段不同。

- Claude Code 是"**精品化 + 平台化**"路线：默认值激进（2026-09 起默认 `auto` 分类器审批）、多 surface、生态最深，代价是系统复杂度外溢和额度不可预测。
- opencode 是"**开源平台 + 模型中立**"路线：广度与可编程性第一，代价是 V2 破坏性升级、稳定性与安全信任问题。
- 我们是"**单 Agent 可信工作台**"路线：可解释、可核对、可恢复优先，代价是产品化与生态几乎为零。

> 产品层的诚实结论：**我们现在不是"另一个 opencode/Claude Code"，而是"一个可以把单 Agent 工作过程讲清楚的学习型工作台"**。这个定位本身成立，但不能用"功能对标"的方式去比，否则永远输。

---

## 2. 用户旅程逐段对比（体验视角）

### 2.1 安装与首次运行

| | mini-harness | opencode | Claude Code |
|---|---|---|---|
| 到首屏 | 6–8 步：git clone → Python 3.11+/uv → `uv sync --extra test` → Node 22+/npm → `npm --prefix tui ci` → 3 个 `VIBE_*` 环境变量 → `./run_ink.sh --workspace`；必须真 TTY | 1 步安装 + `opencode` → `/connect` 选 provider / Console / 本地模型；`/init` 可选 | 1 步安装 + `claude` 浏览器登录；`/init`、终端设置向导、快捷键面板 |
| 零配置体验 | `--fake` 可看界面，但只有固定回复，**体验不到工具/审批/验收**；真实 demo 需要 API key | Console/Go 登录即用（需付费/额度），或自带 key；无 key 有明确 `/connect` | 订阅即可；首次有安全说明与设置向导 |
| 卡点 | 无 `doctor`、无 `.env.example`、缺 key 只有后端 stderr；非 TTY 直接 exit 2 | V1/V2 双线并存、两套安装脚本两套文档，容易装错；V2 安装脚本在部分终端极慢 | 需要订阅/网络；浏览器登录对纯终端用户有一步跳转 |
| 启动耗时 | 实测 `tsc` 0.24 s + 后端 ready 109 ms（依赖已装时），**启动很快**；PTY 首屏未测 | V2 共享 daemon 常驻，启动被掩盖；体感取决于升级状态 | 原生二进制，启动快；后台自动更新偶发干扰 |

**结论**：启动性能不是问题，**前置成本与错误引导**才是问题。"3 分钟首用"在干净机器上不成立（估 10–20 分钟，依赖下载时间不计）。

### 2.2 首屏与信息架构

- **我们（120×40）**：Mini 猫 + 品牌承诺 + 3 条 starter + 工作区/模型/会话 + 6 个常用入口 + 右栏状态（上下文/任务/工具/预算）。中文、完整、有响应式（80×24 收成 3 行状态条，60×20 可启动）。
- **问题**：右栏同时出现"基础上下文已装载 / 尚未请求模型 / 任务累计 0 / 尚未提交任务"四条近似空状态；"上下文"在右栏、反馈行、`/context` 三处重复；而用户最需要看的"**上一轮任务是否验证通过**"没有位置。80×24 下"当前工作区/常用入口"被挤出首屏。
- **opencode**：全屏多面板（会话区 + composer + 侧栏 + tabs + diff viewer + which-key），视觉和信息密度都高；代价是"文本墙"和快捷键认知负荷，社区有人因此离开。
- **Claude Code**：classic / fullscreen 双渲染器（fullscreen 固定输入框、防闪烁、鼠标可选），可自定义 statusline、主题（含色盲主题）、Vim 模式、输出样式、可访问性模式；成熟度明显更高，但入口/模式/命令数量也多。

**判断**：我们的 IA 骨架（主页 / 对话 / 审批 / 工具详情 / 上下文 / 技能菜单）是完整的，**缺的是信息优先级与视觉质量**，不是缺布局能力。

### 2.3 日常对话、改代码与阅读

| 能力 | 我们 | opencode | Claude Code |
|---|---|---|---|
| 多行/历史/中文输入 | ✅（Enter 发送、Ctrl+J、grapheme 编辑） | ✅ | ✅（Vim 模式、外部编辑器） |
| 工具调用展示 | 卡片 + 摘要 + `Ctrl+O` 全量参数/输出 | 折叠/分组/verbosity 循环 | transcript viewer、Ctrl+O、折叠 |
| diff 展示 | 审批预览里 unified diff；`/diff` 是命令结果；Git 侧栏是外部窗口 | **独立 diff viewer**：文件树 + hunk 跳转 + 审阅标记 + All/Committed/Uncommitted/Last turn 范围 | fullscreen 下 `/diff` 常驻侧栏，按轮查看，测试/生成文件折叠 |
| 会话内搜索 | 无 | 无（最高讨论度功能请求之一） | `Ctrl+R` 反向搜索 / transcript 搜索 |
| 输出视觉 | 无语法高亮（代码块仅底色）、无主题自定义 | 语义 token 主题 + 主题编辑器 | 语法高亮、主题、图片粘贴、OSC 8 链接 |

**判断**：日常"说、改、看"的闭环我们通了，但**"审阅"这一环明显弱**：diff 没有专属视图、没有按轮查看、没有标记已审。这恰好是"过程可核对"最该补的 UI。

### 2.4 审批与权限（机制细、体验弱的一项）

| | 我们（工作树） | opencode | Claude Code |
|---|---|---|---|
| 模型 | T0–T4 副作用层级 × once/session/workspace 作用域矩阵 × 用户/项目策略 × 整体档位（ask/edits/workspace/full）；项目策略只能收紧；硬边界（黑名单/护栏/沙箱）优先 | V2 有序 `permissions` 数组，last-match-wins；`always` 保存为项目级持久 allow 但永不能覆盖 deny；另有二元 `policies` 只能收紧 | 6 种权限模式（Manual/acceptEdits/plan/auto/dontAsk/bypass）+ allow/deny/ask 规则 + hooks + protected/critical paths + 可选 OS 沙箱；2026-09 起默认 `auto`（分类器后台审查） |
| 默认 | `ask`：所有 T1–T4 逐次弹 | V2 默认更保守（无匹配即 ask），但 `--auto` 一键放宽 | 默认 `auto`（有争议，但确实减少逐次审批） |
| 审批卡信息 | 工具名 + 预览（diff/命令），层级 `[审批层]` 在预览**末尾**，80×24 屏外；标题不含层级/后果 | 命中规则、fullscreen 详情、once/always/reject 三选一 | 模式说明、允许本会话/一次性、Tab 加注释、最近被拦复核 |
| 可调与可见 | 策略 JSON（手写）、`/permissions` 只显示规则计数、`--autonomy` **Ink 不接受**、档位无徽标、自动放行无标记 | 规则可读、`auto` 指示、配置热重载 | `/permissions` 规则管理、模式状态栏常显 |
| 撤销/持久 | once/session/workspace 仅当前进程；重启不继承 | always 持久到项目配置（且不能覆盖 deny） | 规则持久 + 可管理 |

**关键冲突**：用户 2026-10 的记录里第 1 条就是"频繁审批，想预设/项目级/更细粒度"。**机制已经做出来了，但用户既发现不了也改不动**：

- `./run_ink.sh --autonomy edits` 被 Ink 参数白名单拒绝（`exit 2`，实测）；
- TUI 源码里 `autonomy` 零引用，没有 `/autonomy`，首屏/状态栏无档位；
- `/permissions` 只有一行"授权档位：ask（每次询问）"+ 规则计数，不列规则、不说怎么改；
- 自动放行动作与普通执行在界面上无差别，用户不知道"刚才为什么没弹审批"。

**结论**：审批是"后端 4.5 分、界面 1.5 分"的一项。它比 opencode/Claude Code 的模型**不差**（某些维度更细），但**体验是三家里最差的**。这是本轮投入产出比最高的修复点。

### 2.5 中断、纠偏与停止

- 我们：运行中 Enter 纠偏（下一次模型请求生效）、`/followup` 排队、`Ctrl+C` 三阶段停止提示（已发送 → 后端确认 → 已取消但需核对），中文提示清楚；W2 真实验收。
- opencode：Esc 中断、后台化、steer + 排队分离、队列管理；已知问题 `--auto` 只对当前 tab 生效、审批无超时。
- Claude Code：Esc 中断保留已完成工作、运行中消息排队并可立即发送/取回、Ctrl+B 后台、/tasks 管理；细节做得最细。

**判断**：这块我们不落后，甚至"取消后仍需核对"的措辞比两家更诚实。缺的是**排队的可视化/取回**（我们只有 `/followup` 命令，没有"排队中"列表）。

### 2.6 恢复与长任务

| | 我们 | opencode | Claude Code |
|---|---|---|---|
| 恢复 | `--continue/--session` + 显式 `/resume`；不自动重放未知结果；崩溃对账；TUI 内**无会话选择器** | `/sessions`、leader 键、CLI `--continue/--session`、`--fork`、共享 daemon 多端重连 | `/resume` picker（搜索/分支/worktree 过滤）、`--from-pr`、`/branch` |
| 压缩 | 1M 预算（输入容量 960k）、分区预算、80% 主动压缩、原文 offload + `recall`、请求前快照 | 结构化交接摘要（Objective/decisions/completed/blockers/next/files）+ 保留最近 15k 原文 | 967K 触发、压缩保留清单明确、idle compaction、thrashing 诊断 |
| 长任务 | `/goal` 分段（资源停止自动续段，默认 8 段/4 小时）；`verified=false` 时暂停而不是完成 | 后台子代理/后台 shell/worktree，多任务并行是其强项（子代理生命周期有已知 bug） | `/goal`、后台任务、agent view、dynamic workflows、1M 上下文 |
| 撤销 | **无**（只有 `.vibe/backups/` 文件备份，用户不可见） | `/undo` `/redo`，独立 git object 快照，文档诚实列出不覆盖范围 | `/rewind` checkpoint（最近 100 个），明确不覆盖 bash-only 改动/子 Agent 编辑/外部改动 |
| 分支 | 无 | session fork | `/branch`、`--fork-session` |

**判断**：恢复语义我们**方向最稳**（不自动重放、副作用对账），但入口在 CLI、没有会话选择器；长任务有 `/goal` 这个好概念，但缺后台任务与撤销。**撤销是当前最明显的交互空白之一**——竞品都有、且都诚实标注了边界，我们可以做得更诚实（用 rounds + backups + git 实现"可回滚到某轮之前，并列出不可撤销副作用"）。

### 2.7 扩展与生态

| 能力 | 我们 | opencode | Claude Code |
|---|---|---|---|
| 模型 | OpenAI-compatible（含本地，改 `VIBE_BASE_URL`） | 75+ providers、本地模型自动发现、Models.dev | Claude 及云厂商/网关 |
| Skills | 自动发现项目/本机 skills（覆盖 Claude/Cursor/Gemini/Copilot/OpenCode 目录），按需加载 | skills + 自定义命令 + references + formatter | skills（自定义命令已并入）+ 子 Agent + hooks + 插件 + mods + marketplace |
| MCP | stdio only，启动与调用均审批 | 本地/远程/OAuth，V2 全部进 Code Mode | 本地/远程/OAuth、tool search、千级工具 |
| 浏览器/桌面控制 | B1 观察 + B2 本地受控动作已真实验收（click/type/select 逐次审批、拒绝后保持草稿、零提交）；范围窄 | Code Mode 浏览器自动化；桌面端 | Chrome 自动化 + computer use（部分订阅/企业） |
| LSP | ❌ | V1 有，**V2 明确移除** | CLI 未单列 LSP；通过 IDE 集成/插件获得代码智能 |
| hooks/插件 | ❌（工具扩展需改代码） | V2 插件 API（旧插件全断） | 生命周期 hooks、插件 market、mods 可画 TUI |
| 多 Agent | ❌（设计未实现） | 子代理 + 后台 + worktree（有生命周期 bug） | subagents / agent view / Agent Teams(exp) / workflows |
| 多端/远程 | 仅本地 TUI（+ macOS 侧栏） | TUI/桌面/Web/IDE/Server/SDK | 全 surface |
| 生态成本 | 103 个技能已出现菜单负担 | 插件 context 税、快捷键认知负荷 | 模式/命令/插件多，系统复杂度外溢 |

**判断**：生态是**结构性差距**，而且方向不同——对手在"平台化"，我们应该在"兼容 + 单点深化"：读 `AGENTS.md` 与多工具 skills 目录（含 `.claude/skills`）已经做了，继续做"能复用现有生态，但不建市场"更划算。

### 2.8 可核对 / 可验收（机制主场、呈现客场）

| 能力 | 我们 | opencode | Claude Code |
|---|---|---|---|
| 上下文可核对 | `/context` 分区、请求前快照、块来源/hash、压缩前/后、可回放 | 用量/窗口，压缩摘要 | `/context` 分类 + 优化建议、压缩保留清单 |
| 过程证据 | `trace.jsonl`、`rounds/round_XXX.json`（文件 hash/验证/审计/未验证项）、`audit.jsonl` | diff review + export + session timeline | transcript + `/export` + OTel + `/usage` 归因 |
| 完成判定 | **harness 守卫**：动作任务"完成"必须有覆盖最后改动的成功验证，否则 `verified=false`；环境审计（未归因改动/审计窗口变化/非 git 局限） | 公开笔记未见完成守卫（以模型自述为主） | 公开笔记未见完成守卫；`/rewind` 只解决"回退"，不解决"验收" |
| 用户可见性 | **弱**：`verified` 只在收尾 notice 的一段文字里（无徽标/警示色），页脚仍写"任务完成"；无 `/evidence`；rounds 需离开 TUI 打开 | 中：diff viewer/审阅标记好，但没有"这轮是否被独立验证" | 中：可观测面全但分散（/usage、/context、/status、OTel）；`/rewind` 有盲区 |
| 导出/人读 | 无（`/copy` 仅回复） | Markdown 导出、JSON export | `/export`、`/copy`、transcript viewer |

**判断**：这是公开资料里我们**机制领先**的维度（未见对手同类机制），但"最后一公里"没走完：证据呈现弱、不可导出、不可一键核对。**如果只修一件事，就修这个**——它同时能兑现产品承诺、抵消生态劣势、形成记忆点。

### 2.9 成本与可观测

- 我们：`/cost`（session 级 token/费用/tools/last）、`/context`、右栏预算；但价格默认 0（显示 `$0.000000`，无"未配置价格"提示），"任务累计"与"当前上下文"两个数字易混，默认不限预算。
- opencode：默认 TUI 只显示 output token/s，逐轮 token 要开 debug；`opencode stats` 汇总；V2 丢了 OTel。
- Claude Code：`/usage` 有 token/成本/计划归因/建议/prompt cache 统计，`/insights` 生成 HTML 报告；代价是额度不可预测（社区最大痛点之一）。

**判断**：三家都有"成本感知不足"的问题，但对手在"可观测面广度"上更强。我们不必追 `/usage` 的深度，只需把已有数字**说人话、默认可见、标注未配置价格**。

### 2.10 作者自己记录的 4 条问题，现在怎么样了

> 来源：`docs/个人记录的实际使用问题.md`（这 4 条是"用户体验如何"最直接的验收标准）。

| # | 当时的反馈 | 当前状态 | 判断 |
|---|---|---|---|
| 1 | 频繁审批：希望预设审批权限、一次开放当前项目、或更细粒度 | 机制已做（T0–T4 + 作用域矩阵 + 用户/项目策略 + ask/edits/workspace/full 档位）；**但 Ink 入口不可达、档位不可切换/无徽标、规则不可视** | **机制已解、体验未闭环**（P0-2） |
| 2 | 审批等待超时，希望无限时间 | 已实现：人工审批等待不计入 `max_run_seconds`，无截止时间；Ctrl+C 可取消 | ✅ 已解决（交互式）；**无人值守/长任务仍需另设超时或降级策略**——竞品的"审批无超时"问题提醒我们，"无限等待"不是万能解 |
| 3 | TUI 展示差；超时导致页面无法输入；换 TS 是否会更好 | Textual 已退役，Ink 成为唯一入口；全屏/多行/阅读/审批/恢复齐备；**超时不再阻塞输入**（停止后可继续输入） | 部分解决；视觉质量与信息优先级仍是 P1（§5.2） |
| 4 | 每轮预算用完反复提示输入 `/resume`，应当自动压缩 | 已实现：交互默认 `max_run_tokens=0`（不限）+ 200k 周期 / 80% 自动压缩 + 原文 offload/recall | ✅ 已解决 |

**这张表说明**：4 条里 2 条已解决、1 条部分解决、1 条"机制已解但用户用不上"。而最后这条恰好是最近投入最大的审批体系——**能力做在正确方向，但产品化没走完**，这也是本报告 P0 排序的直接依据。

---

## 3. 竞品口碑里对我们最有用的信号

> 这一节回答"用户到底痛什么"，决定我们该打哪里。

1. **行业主矛盾是"监督税"**：Claude Code 2026 年社区最强抱怨从"啰嗦"转向"Agent 说完之后，用户还得负责翻译、核验、约束它"；opencode 的审批疲劳/无超时/插件绕过也是同一类问题。**"更快的生成"已经不稀缺，"更省心的核对"才稀缺**——这正是我们的牌。
2. **两家的可核对都有硬缺口**：Claude Code `/rewind` 不覆盖 bash-only 改动、子 Agent 编辑、外部改动、软硬链；opencode snapshot 不覆盖 git-ignored/大文件/目录外/异步副作用，官方自己说"是便利不是事务/备份"。**"诚实的核对与回滚边界"是可以直接占位的差异化。**
3. **两家的自动化能力都在加，但信任成本在上升**：Claude Code 甚至把默认权限模式切成 `auto` 分类器审查，社区随即出现"更不透明"的反弹；opencode 的 `--auto` 只对当前 tab 生效。**默认保守 + 显式可调 + 每一步可解释**，对"想看懂过程"的个人用户是合理选择（也符合我们现有定位）。
4. **破坏性升级的代价**：opencode V2 让插件/Server API/配置全断，LSP/share 被移除，社区"迁移不动"；Claude Code 2026-03~04 的质量回归被官方 postmortem 承认。**小团队的护城河是"升级不打碎用户"**，别做大爆炸重写。
5. **成本/额度不可预测是最伤生产力的痛点**：Claude Code Max 1.5 小时耗尽、共享池、tokenizer 变化的争议；opencode Go 定价展示被吐槽。我们没有订阅制包袱，**"本地可控 + 成本透明 + 不自动烧钱"可以做成卖点**。

---

## 4. 评分卡（10 维 × 权重）

> 分数含义：1 缺失/不可用；2 可用但明显摩擦；3 够用但有短板；4 成熟好用；5 同类最佳。权重按"单 Agent 本地编码工作台"的产品目标设定；分数是判断，证据见 §2 与三份笔记。
> 第 5/6 维是把原"过程透明/可验收"拆成**机制**与**用户可见呈现**两半：我们机制强、呈现弱，合成一维会掩盖真实差异。

| # | 维度（权重） | mini-harness | opencode | Claude Code | 关键依据（一句话） |
|---|---|---|---|---|---|
| 1 | 上手/安装/配置（12%） | **2.0** | 4.0 | 4.5 | 我们 6–8 步 + 3 变量 + 无 doctor；对手一行安装 + 登录/`/connect` + 引导 |
| 2 | 界面与信息架构（12%） | **3.0** | 4.0 | 4.5 | 我们中文全屏、响应式、六视图齐；缺视觉质量、搜索、语法高亮、信息优先级 |
| 3 | 日常交互效率/可发现性（12%） | **3.0** | 4.0 | 4.5 | 我们多行/命令菜单/复制/读焦点可用；`/help` 单段 972 字符、无会话搜索、无命令面板 |
| 4 | 审批与权限体验（12%） | **2.5** | 4.0 | 4.5 | 我们模型最细但界面最弱：默认逐次、档位不可达、层级屏外、规则不可视 |
| 5 | 过程/上下文可核对（机制，6%） | **4.5** | 3.0 | 3.8 | 我们有 rounds/verified/audit/context 快照与完成判定；对手是 usage/context/transcript 或 diff review，公开资料未见同类完成判定 |
| 6 | 验收与证据呈现（6%） | **2.5** | 3.0 | 3.3 | 我们证据只在收尾 notice/JSON、无导出/无审阅面板；opencode 有 diff review + export；CC 有 /usage + /export + OTel |
| 7 | 长任务与恢复（10%） | **3.5** | 4.0 | 4.5 | 我们恢复语义最稳、`/goal` 概念好；缺撤销、后台任务、会话选择器、分支 |
| 8 | 扩展与生态（12%） | **1.5** | 4.5 | 5.0 | 我们 skills + stdio MCP + 早期浏览器动作；对手 providers/hooks/plugins/subagents/IDE 全套 |
| 9 | 可观测与成本（8%） | **3.5** | 3.0 | 4.0 | 我们 trace/cost/context/rounds 齐但分散、价格默认 0；CC `/usage` 最深 |
| 10 | 稳定性/安全/信任（10%） | **3.0** | 2.5 | 3.0 | 我们本地 fail-closed、边界诚实但未验证真实模型、平台面窄；opencode 有 V2 回归与安全事件；CC 有质量/供应链事件但有沙箱与企业治理 |
| | **加权总分** | **2.79** | **3.71** | **4.26** | |

> 功能覆盖/体验成熟度/差异化三项是对上表的**判断性再归组**，不是独立测量：功能覆盖看对手有而我们缺多少；体验成熟度看默认路径是否顺手；差异化看是否有对手难以替代的机制。

**分数解读**

- **不是"差一点"，而是"原型 vs 产品"的差距（约 1.5/5）**：差距集中在 1/4/6/8（上手、审批体验、验收呈现、生态），每一项都对应"产品化"而不是"再写一个功能"。
- **机制 vs 呈现要拆开看**：第 5 维（机制）我们 4.5，高于 opencode 3.0 / Claude Code 3.8；但第 6 维（呈现）我们 2.5，低于两家——"可核对"目前是**代码里的能力**，还不是**界面上的体验**。
- **如果按"更看重可核对与审批、更不看生态"的权重试算**，我们约 2.9、opencode 3.7、Claude Code 4.2——差距缩小但仍明显，因为**上手和日常顺手程度**是硬门槛。
- **换个视角看**：我们的功能覆盖 2.4、差异化 4.0——这是典型的"**窄而深的小工具**"画像，不是"平台"画像。产品策略应该顺着这个画像走。

---

## 5. 我们真实的位置：优势、短板、最大风险

### 5.1 可以写进产品叙事的优势（有证据）

1. **交付证据链**：每轮 `rounds/round_XXX.json`（文件 before/after hash、验证记录、审计结论、receipt，不复制正文），`verified / completion.codes / audit.integrity` 可区分；动作任务没有覆盖最后改动的成功验证，就不会被标为"已验证完成"（第二次仍会收尾，但 `verified=false`）。
2. **审批模型的可组合性**：T0–T4 × once/session/workspace × 用户/项目策略 × 整档位，且项目策略只能收紧、硬边界永远优先——比"一个 approval mode"更细。
3. **上下文可回放**：请求前快照 + 分区 + 块来源/hash + 压缩前/后，模型看到的输入是可核对的，而不是只报一个用量数字。
4. **恢复语义诚实**：不自动重放未知结果、崩溃不假装成功、恢复先核对再续做；`/goal` 把"资源停止"和"完成判定失败"分开处理。
5. **中文优先 + 本地模型 + 免费 web_search + skills 跨工具目录发现**：对中文用户和本地模型用户的开箱成本低。
6. **浏览器观察与受控动作（B2）已真实验收**：本地页面 click/type/select 逐次审批、拒绝后保持草稿、零提交；范围窄，但它是"可审核的 GUI 动作"的种子，也是"功能覆盖"里容易被忽略的一块。

### 5.2 必须承认的短板（按用户感知排序）

1. **首用门槛**：安装/配置/引导三件事都缺，直接决定"能不能被第二个人用起来"。
2. **审批体验**：默认逐次 + 档位不可达/不可调 + 层级屏外——用户最痛的第 1 条反馈机制上已解、体验上未解。
3. **三大交互范式缺失**：plan mode（改前先看计划）、undo/checkpoint（改后可回退）、diff review（按轮审阅）——这是成熟产品与原型的分水岭。
4. **证据呈现弱**：`verified` 只在收尾 notice 的一段文字里（无徽标/警示色），rounds/audit 只有文件路径、TUI 无入口；"可核对"最后一步仍要离开终端。
5. **生态薄**：无 hooks、无插件、无子 Agent、无 LSP、MCP 仅 stdio；不能复用社区生态，也没有让别人扩展的接口。
6. **产品细节债**：11 条文档-代码不一致（`--autonomy`、旧审批键 `a/d`、README 测试数过期、MILESTONES 未记录 10-31 切片、README 截图早于新功能）、`a/d` 键静默无响应、`/diff` 审批缺层级、价格默认 0 无提示。
7. **平台面窄**：侧栏仅 macOS Ghostty/Terminal；沙箱可选（macOS Seatbelt / Linux bwrap）但非默认；非 TTY 不可用（CI/管道受限）。

### 5.3 最大风险（比"功能少"更危险）

**产品承诺与用户感知断裂**：
- README 说"过程可核对、结果可验收"，但 `verified=false` 时只有收尾 notice 的文字提示（无徽标/警示色），页脚仍显示"任务完成"，第一次守卫拒绝也不给用户通知；
- 最近投入最大的审批 tiers/autonomy，只接上了一半：预览尾行有层级、`/permissions` 有一行档位；但档位不可切换、规则不可视、窄屏看不到层级、自动放行无标记；
- 这会让"可核对"变成一句 README 口号，而不是用户能感知的能力。

**反向风险**：不要用"可核对"给还没验证的真实模型能力背书。`docs/specs/verified-completion.md` 自己写着"真实模型、Ink `/goal` 体验与 benchmark 未验收"。在真实验收前，对外叙事要写"机制已验证、真实任务待验收"。

---

## 6. 竞品可学 / 不可学清单

### 6.1 值得学（按投入产出）

| 来源 | 机制 | 我们的轻量版 |
|---|---|---|
| Claude Code | 状态/模式持久可见（statusline 显示模型、上下文%、cost、模式） | 状态栏固定：模型 · 授权档位 · 上下文% · 本轮 verified · 是否可取消 |
| Claude Code | `/context` 按类别展示 + 压缩保留清单 | 已有 `/context`；补"本轮注入了哪些项目背景/偏好/规则/技能"和"压缩保留/丢弃了什么" |
| Claude Code | `/export`、transcript 展开/折叠、剪贴板失败写文件兜底 | 补人读版导出（Markdown）+ 证据卡片复制 |
| Claude Code | 运行中排队 + 立即发送 + 取回；审批答案可带注释 | `/followup` 可视化 + 排队列表 + 拒绝/允许备注进入下一轮 |
| Claude Code | plan mode 的三选一批准（自动/手动/继续规划） | 先做"只读探索 → 计划展示 → 批准执行"最小闭环 |
| opencode | diff viewer（文件树 + hunk + 审阅标记 + All/Last turn 范围） | Ink 内 `/diff` 升级为按轮/按文件的审阅面板，标记已核对 |
| opencode | `/undo` `/redo` + snapshot 边界的诚实说明 | 用 `.vibe/backups/` + rounds + git 做"回滚到某轮前"，并列出不可撤销副作用 |
| opencode | 结构化压缩交接单（Objective/decisions/completed/blockers/next/files） | 压缩摘要按这个 schema 生成，并在 UI 可展开 |
| opencode | 有序权限规则、last-match-wins、`always` 持久但不能覆盖 deny | 我们覆盖 tier/前缀/正则/项目收紧，但不是有序 last-match；补"命中规则可见 + 规则持久化 + 撤销 UI" |
| 两家 | 可发现性兜底（`?` 帮助面板、命令面板、which-key、highlights） | 分组帮助页 + 命令面板 + 首用 checklist；不抄 200 键位 |

### 6.2 不该跟（写进"不做"）

- **auto mode 分类器 / 服务端安全裁决**：需要第二个模型与合规团队，个人项目做不好也解释不清。
- **provider 数量军备竞赛 / 插件 marketplace / 多 surface / 企业治理**：生态与组织能力，不是单机产品优先级。
- **共享 daemon / 插件热重载 / 自改插件**：opencode 2026 年有社区报告称 V2 daemon 可绕过用户自建沙箱、插件工具不遵守 permissions（修复状态未核实），也出现过子代理生命周期 bug；这类能力会放大攻击面与生命周期复杂度，单 Agent 单进程更稳。
- **公开 share 链接 / 200 键位 / tabs 侧栏**：信息架构成本大于收益，等真实反馈。
- **大爆炸重写**：opencode V2 的教训；我们任何 breaking 都要有兼容期与自动迁移。
- **个性化扩张**：W6 v2 主假设未获支持，先不投入（与现有 roadmap 一致）。

---

## 7. 建议（按优先级，含验收标准）

### P0：把"已经做出来的能力"变成"用户能感知的体验"（1–2 周）

**P0-1 让 `verified` 成为一等 UI 状态**
- `done` 分三态：`已完成·已验证` / `已完成·待验证` / `已停止`；状态徽标、页脚、receipt 卡片标题同步（`verified=false` 用 warning 色 + "验收未通过"）；
- 完成守卫第一次拒绝时给用户可见 notice（"检测到有改动但未验证，已要求补验证"）；
- 新增 `/evidence`：最近一轮 `verified / completion.codes / audit(integrity, unattributed) / 改动文件 hash / 验证命令`，并给完整 JSON 路径；
- 验收：有文件改动的任务 100% 显示验收状态；`/evidence` 一屏可回答"改了什么、验证了吗、有没有未归因改动"；fake 场景与单元测试覆盖。

**P0-2 审批体验闭环**
- Ink 透传 `--autonomy`，新增 `/autonomy`（或 `/permissions set`），状态栏/首屏显示档位徽标；
- 审批卡重构：标题 `审批 · T1 工作区写入 · edit_file`；首屏显示"命中规则/来源（默认矩阵 / 用户 permissions.json#2）"；`总是允许` 时预览"将写入什么规则、作用于哪个项目/会话"；自动放行动作在工具卡标"自动（edits/规则）"；
- `/permissions` 列出 user/project 规则、来源、最近命中、可撤销；session/workspace 授权可选持久化（默认关闭）；
- 实现 spec 里已规划的 destructive 层（T5：`rm -rf`、`git reset --hard`、`git clean -fd`、强制 push 逐次确认）；
- 修复文档键位 `a/d` 与实际 `y/s/p/n` 不一致；
- 验收：样例任务"改 2 文件 + 跑 1 条测试"在默认 `ask` 下约 3 次审批（按矩阵/帧推断，非真实用户计数）→ 用一次预设/策略后 ≤1 次；自动放行 100% 可见；权限回归测试全绿。

**P0-3 首用可启动**
- `./run_ink.sh --doctor`：检查 Node/Python/uv/venv/`tui/node_modules`/API key/TTY/workspace/git，缺项给**可执行的下一步命令**；
- `.env.example` + README 顶部"三段式快速开始"（先看 fake 演示 → 配 key → 跑真实任务）；
- fake 增加一个确定性"写文件 → 审批 → 验证 → 回执"演示场景（无需 key 体验核心流程）；
- 验收：在装好 Node+uv 的机器上 ≤3 条命令到首屏；缺 key 时给出"配哪里"的指引；新用户 3 分钟内跑完 fake 演示。

**P0-4 文档-代码一致性 + 重截首屏**
- 用代码常量生成 CLI/命令/键位清单，CI 比对；更新 USAGE/README/MILESTONES/COMPARISON；补 verified/rounds/autonomy 说明；重截 README 首屏（当前图早于 10-28 新功能且是侧栏打开态）；
- 验收：self-audit §9 列出的不一致（11 条，另 1 条为已核对项）清零；发布前 checklist 通过。

### P1：补齐成熟产品的三大交互范式（1–2 个月）

1. **plan mode**：`Shift+Tab`/`/plan` 进入只读研究；计划展示（可编辑、可持久化、压缩后重注入）；批准三选一（自动执行 / 逐步审批 / 继续规划）。这是当前最缺的交互范式之一（建议优先做）。
2. **undo / checkpoint**：基于 backups + rounds + git 实现"回滚到某轮之前"；明确区分"对话回滚"与"文件回滚"；回滚结果列出不可撤销副作用（已跑命令、网络请求、外部写入）；绝不静默覆盖。
3. **diff review 面板**：按轮/按文件的 diff 视图、hunk 跳转、标记已核对、从对话卡片直达；复用现有 `git_diff` 与 apply_patch 预览数据。
4. **会话选择器 + 会话搜索**：TUI 内 `/sessions`（复用 `--list-sessions` 数据，打开先展示进度、仍需 `/resume`），transcript 文本搜索（opencode 至今没有，低成本差异化）。
5. **帮助体系重构**：分组帮助页 + `/help <命令>` + 命令面板分组 + starter 可 Tab 填入；命令/键位从同一常量生成，杜绝文档漂移。
6. **状态栏重构**：模型 · 授权档位 · 上下文% · 本轮 verified · 运行中/等待审批 · 可取消提示；去掉重复空状态，加"上一轮任务/证据状态"位。
7. **只读审计 hooks**：SessionStart/PreToolUse/PostToolUse/SessionEnd 先只写 trace、不改行为；后续再评估用户可配置 hooks。
8. **skills 菜单治理**：按来源/项目分组 + 关键词搜索（本机已发现 103 个技能，菜单负担明显）。

### P2：产品化与差异化（3–6 个月，按需要做）

1. **分发与安装**：`brew`/`uvx`/`npx` 一键入口、版本化 release、可选自动更新、跨平台检查。如果只自己用，可后置；如果要给第二个人用，必须做。
2. **"外部 Agent 验收层"（战略方向，值得单独评估）**：把 opencode/Claude Code/Codex 当作 executor（`opencode run` / `claude -p`），我们的 harness 负责审批、记录、验证、审计、出 receipt——从"又一个小 coding agent"变成"**Agent 的验收/审计层**"。这与项目名（harness）、现有 rounds/audit 资产和 LongHorizon 研究的方向一致，也是两家竞品都不会做的位置。
3. **只读 auditor 子 Agent（M1）**：完成判定交给独立只读上下文（看不到执行轨迹、有快照比对、只能提议不能写），把"verified"从确定性守卫升级为"独立审计"。前提：P0/P1 先被真实任务验证。
4. **生态兼容桥**：已读 `AGENTS.md` 与多工具 skills 目录（含 `.claude/skills`）；再补 `CLAUDE.md` 只读兼容、`.mcp.json` 导入（只读兼容，不建市场）。
5. **只读 Web 观察面板**：用现有 `--stdio` JSONL 事件流做只读观察/导出，为"离开终端也能核对"铺路（不引入 daemon）。

### 7.1 量化验收指标（建议直接进 MILESTONES）

| 指标 | 当前 | 目标 |
|---|---|---|
| 干净机器到首次任务 | 6–8 步 / 10–20 分钟（估） | ≤3 步 / ≤3 分钟 |
| 启动到首屏 | 构建+后端 ready ≈0.35 s（PTY 首屏未测） | 保持 ≤1.5 s |
| 样例任务审批次数（改 2 文件 + 测试） | 默认 ask 约 3 次（推断） | 预设/策略后 ≤1 次 |
| 自动放行可见率 | 0% | 100% |
| 动作任务 `verified` 可见率 | 仅 receipt 文本 | 100% 一等状态 |
| 回答"改了什么/验证了吗" | 需翻 `.vibe/` JSON | 一屏 ≤10 s |
| 会话恢复入口 | 仅 CLI | TUI 选择器 |
| 文档-代码不一致 | 11 条（self-audit §9） | 0 条 |
| 真实模型验收 | verified-completion 未验收 | 3 道保留题 ×3 次，记录误完成率/误杀率/额外轮次 |
| 回归 | Python 1127 collected / TUI 338（337 pass，1 PTY 环境项） | 保持全绿，新增用例随修随加 |

### 7.2 30 / 60 / 90 天节奏（建议）

- **0–30 天**：P0-1/2/3/4。只做"让已有能力可见 + 首用可启动 + 文档一致"，不新增大功能；完成后请 2–3 个真实用户走一遍 3 分钟流程。
- **31–60 天**：P1 的 plan mode + undo + diff review 三件套；补会话选择器/搜索/帮助/状态栏；每项先写验收场景再实现。
- **61–90 天**：真实模型长任务验收（含误完成/误杀率）；决定"外部 Agent 验收层"是否立项；再评估 auditor 子 Agent 与分发。

---

## 8. 定位与叙事建议

1. **统一产品名**：现在 README 叫 mini-harness、包名 `vibe`、界面叫"个人编码工作台"、环境变量 `VIBE_*`。建议：**引擎/仓库名 mini-harness，对外产品名 vibe（中文"vibe 工作台"）**，CLI/文档/截图统一；否则用户记不住。
2. **一句话定位**（建议）：**"单 Agent、本地、中文、每一步都能对账的编码工作台。"** 三个承诺：
   - 每个动作可解释（谁发起、命中什么规则、作用域多大）；
   - 每次中断可对账（改了什么、验证了没有、哪些结果未知）；
   - 每条记忆有来源（项目背景/个人偏好可查看、可纠正、可停用）。
3. **README 首页改版**（从"工程验收报告"改成"用户产品页"）：顶部 3 分钟快速开始 → 30 秒 demo（gif/截图）→ "为什么可信"三条 → "当前边界"（默认不启用沙箱、非容器级隔离、单 Agent、真实模型验收状态）→ 详细文档。当前 README 更像给评审者看的工程证据页，新用户会被 941/337 测试数字和 W0–W7 表格劝退。
4. **诚实边界要写进卖点**：明确"默认是审批 + 黑名单 + 超时；可选 Seatbelt/bwrap，但不是容器级隔离"；明确 `verified=false` 的含义；明确"哪些不可撤销"。对手的 trust 问题恰好是我们的机会，但**诚实必须是可验证的**，不能自己也用"看起来能跑"来背书。
5. **对谁做**：如果只作者自用，P0-1/2 足够，P0-3/分发可以缓；如果要给"中文开发者/本地模型用户/需要交付审计的独立开发者"用，P0 全做 + P1 三件套；如果要做成产品/项目名片，再考虑 P2 的"外部 Agent 验收层"。

---

## 9. 风险与不确定性

1. **我方能力未在真实模型下验收**：完成守卫是否可执行、是否增加太多轮次、`/goal` 跨段体验，spec 都标未验收；本报告不把 fake 证据当真实能力。
2. **本仓证据限制**：PTY 被沙箱禁止，全屏交互/按键/终端恢复未真机验证；干净 clone 未实测；首用耗时是推断。
3. **竞品信息时效**：opencode 处于 V2 重构期（10-30 还在发 v2.2.5），Claude Code 约 1.2 天一个版本；本报告只代表 2026-10-31 的公开文档与社区状态，部分社区数据置信度中/低（见两份笔记的附录 C）。
4. **评分主观性**：10 维权重与 1–5 分是产品判断，不是测量；不同权重会改变总分（但三家排序稳定）。
5. **范围风险**：P1 的 plan/undo/diff 三件套都不小，容易把"单 Agent 可靠性"优先级挤掉；建议按 §7.2 节奏，每项先定验收场景，做完再下一项。

---

## 附录 A：关键事实与来源（摘要）

> 完整来源表见 [opencode 笔记](../../.vibe/ux-review/opencode.md)（57 个 URL，去重）与 [Claude Code 笔记](../../.vibe/ux-review/claude-code.md)（51 个来源）；我方事实见 [self-audit](../../.vibe/ux-review/self-audit.md) 与 artifacts。

| 事实 | 来源 | 置信度 |
|---|---|---|
| opencode V2 主线 v2.2.5（2026-10-30，`@opencode/cli`）；V1 v1.19.5（10-28）；仓库迁移到 anomalyco/opencode，223k stars | opencode 官方 docs/changelog/GitHub | 高 |
| opencode 无系统级 sandbox（官方承认）；2026 年 web/serve RCE（Datadog）与 Console 密钥泄漏（官方邮件）；"V2 daemon 绕过用户自建沙箱、插件不遵守 permissions"为社区报告、修复状态未核实 | 官方 permissions 文档、Datadog、官方邮件、HN | RCE/泄漏高；daemon/插件中（未核实） |
| opencode V2 移除 LSP、CLAUDE.md fallback、share；插件/Server API 破坏性变更 | 官方 v2 迁移文档、changelog、HN | 高 |
| Claude Code v2.1.316（2026-10-31）；约 1.2 天/版；多种安装方式（脚本/brew/winget/系统包/npm/二进制）；Free 不含 Claude Code | GitHub Release、npm、官方 setup、定价页 | 高 |
| Claude Code 2026-09-25 起默认权限模式 `auto`；6 种模式；`/rewind` 不覆盖 bash-only/子 Agent/外部改动/软硬链 | 官方 permission-modes / checkpointing 文档、changelog | 高 |
| Claude Code 扩展：skills（命令并入）、subagents、hooks、plugins/marketplace、mods、MCP、IDE/Desktop/Web/Mobile；Agent Teams 为 experimental 默认关闭 | 官方 docs | 高 |
| Claude Code 2026-04 质量 postmortem（3 个 bug，重置限额）；额度争议；2026-10-30 供应链事件 | 官方 blog、媒体、HN | 质量高 / 事件中 |
| 本仓：T0–T4 + 作用域矩阵 + 策略 + autonomy 已实现；`--autonomy` 被 Ink 参数白名单拒绝；`verified` 只在收尾 notice 与 rounds JSON 中，无徽标/警示色，done 页脚仍显示"任务完成"；无会话选择器/撤销/plan mode | 代码 + 实测日志 + 渲染帧（self-audit §5–§9） | 高（fake/协议层）；真实模型/PTY 未验证 |
| 本仓：Python 1127 collected；TUI 338（337 pass，1 项因无 PTY 失败）；启动 0.24 s 构建 + 0.109 s 后端 ready | 实测日志（self-audit §2/§9） | 高 |

## 附录 B：证据索引

- 竞品：`.vibe/ux-review/opencode.md`、`.vibe/ux-review/claude-code.md`
- 我方：`.vibe/ux-review/self-audit.md`、`.vibe/ux-review/artifacts/`（协议 JSONL、rounds/audit 样例、31 张 UI 帧、视觉 fixture、驱动脚本、测试/耗时日志）
- 仓库内：[README](../../README.md)、[USAGE](../USAGE.md)、[MILESTONES](../MILESTONES.md)、[roadmap](../roadmap.md)、[审批层级 spec](../specs/approval-tiers.md)、[审批策略 spec](../specs/permission-policy.md)、[完成守卫 spec](../specs/verified-completion.md)、[COMPARISON（已过期）](../COMPARISON.md)
- 用户反馈：[个人记录的实际使用问题](../个人记录的实际使用问题.md)（4 条：审批频繁/审批超时/TUI 观感/预算 resume；现状见 self-audit §13.3）

---

*本报告为产品/UX 评估，不含代码改动。所有"竞品行为"均来自 2026-10-31 的公开资料，未实机运行竞品；所有"我方行为"均来自代码、实测日志与渲染帧，真实模型与 PTY 交互标注为未验证。*
