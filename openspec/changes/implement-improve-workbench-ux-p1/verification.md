# P1 实施、评审与验证

日期：2026-11-08。状态：**代码、自动验证与真人核心交互验收通过；原生缩放扩展场景、Owner 学习记录待补**。
范围：[proposal](proposal.md) · [行为约定](design.md) · [路线图 P1](../../roadmaps/improve-workbench-ux/roadmap.md#p1--输入与审批控制)。

## 已改变的行为

- 审批出现后继续编辑草稿；`Ctrl+R` 进入审批，范围键或方向键只选择，`Enter` 确认，`Esc` 回到草稿。
- 选择绑定请求；新请求清空旧选择，已回答请求在同轮内去重，取消后不恢复旧审批。
- 审批异步响应与发送请求关联；发送中、拒绝、断线未知有可见反馈，完成后不残留等待提示。
- 32×12、80×24、120×40 中审批提示优先于 Plan/Goal。尺寸不足时不允许授权；小屏退出选项仍可见。
- 忙碌命令、不可提交状态和同步发送异常保留草稿，说明未执行原因。授权徽标继续等后端结果更新。
- `/permissions reset` 只撤销当前工作区的本进程授权，明确显示档位与持久策略仍然有效。未改 Gate 决策。

## 增量归属

本次开始时 HEAD 为 `37951d8`，工作树已有大量 Plan 修改。保存了 559 份跟踪/非忽略文件、哈希清单与原始 diff：

- `.vibe/ux-p1-2026-11-08/baseline/`
- `.vibe/ux-p1-2026-11-08/baseline-manifest.json`
- `.vibe/ux-p1-2026-11-08/baseline.patch`
- `.vibe/ux-p1-2026-11-08/p1-increment.patch`（相对此基线，不是相对 HEAD）

未提交、暂存或重置已有工作。Plan 会话模式、计划审阅 API 等已有能力不计为本次成果。

## 自动验证

| 验证 | 结果 | 证据 |
|---|---|---|
| Python 全量 | **1659 passed，4 skipped**；1 条既有 tarfile 弃用警告 | `python-full-unrestricted.log` |
| TUI 最终全量（含 TypeScript 编译、真实 Python 集成、PTY、AppleScript 编译） | **452/452 passed** | `tui-final.log` |
| 真实 PTY + `run_ink.sh --fake` | 启动 → 单独 y 不执行 → 聚焦/选择仍不执行 → Enter 写文件 → 第二次审批 → 验证 exit=0 → Ctrl+Q 退出=0，恢复 alternate screen | `pty-observed.jsonl`；`smoke-workspace/.vibe/sessions/20261108-173923-938666/` |
| 额外浏览器 Ink smoke | **未通过，已有配置前置故障**：MCP 模板仍启动 `app.browser_server`，实际模块是 `app.browser.server`；初始化 0 工具，超时等 MCP ready | `browser-ink-smoke.log`；`.vibe/browser/ink-smoke/564391e2602b/` |
| 通用 fake eval，limit=3 | **0/3**，默认 fake provider 没有任务工具脚本，三个用例均 `no_action`；不能作为真实模型能力验收 | `eval-fake.log`；`.vibe/evals/fake/20261108-094346-240295/` |

表中短日志名均相对 `.vibe/ux-p1-2026-11-08/`。PTY 记录是工具驱动的终端交互，不能代替真人输入法或视觉验收。
浏览器脚本已同步焦点/选择/确认键路，但完整浏览器链路未验证；旧模块路径列为独立后续项，不扩大 P1 范围。

首次沙箱内全量遇到本地 HTTP/Unix socket `PermissionError` 和 macOS scripting service 限制；沙箱外复跑后对应项目通过。
首次 TUI 全量还发现两个沿用旧约定的测试：自动按 y 的集成测试、假设空反馈提交后草稿被清空的 Plan 测试；已改为新约定并验证。

### 失败回归与修复证据

均先观察行为断言失败，再实现；编译失败不作为有效 red。

| 行为 | 红 / 绿日志前缀或最终覆盖 |
|---|---|
| 审批到达时继续输入 y/s/p/n | `approval-focus-red` / `approval-focus-green` |
| 请求绑定、重复请求 | `approval-duplicate-red` / `approval-binding-green` |
| 方向选择、Esc 返回、请求过期 | `approval-navigation-red` / `approval-navigation-green` |
| 窄屏提示与不足尺寸禁用授权 | `approval-layout-red` / `approval-layout-green` |
| 命令忙碌、启动/停止/审批/关闭、发送失败 | `command-feedback-red` / `command-feedback-green`（15 项） |
| reset 权限范围说明 | `permissions-scope-red` / `permissions-scope-green` |
| 旧选择提示、异步审批拒绝 | `review-boundaries-red` / `review-boundaries-green` |
| 更早已回答审批的重放 | `approval-replay-red` / `approval-replay-green` |
| 完成/断线提示、小屏退出 | `terminal-feedback-red` / `terminal-feedback-green` |
| Plan + Goal 长目标覆盖审批占位提示 | `goal-placeholder-red` / `tui-final.log` |

## 增量评审

使用 `ponytail-review`，由主代理检查实际增量、输入调用者、BackendClient、stdio Gate 与回归。假设为单用户、单活动 run 的本地工作台；不是独立模型冷审。

| 发现 | 可复现场景 | 处置 |
|---|---|---|
| 新请求残留旧“已选择”提示 | 在 A 选择 session 后到达 B | 新请求、焦点切换与 Esc 清理反馈；选择继续绑定 id |
| 异步审批拒绝被过滤 | approve 发出后，后端以响应请求 id 返回 `approval_mismatch` | approve 返回请求 id，复用控制响应关联，显示错误和停止入口 |
| 只记住最后一个已回答审批 | 回答 A、B 后重放 A | 当前 run 内保存已回答集合，切换 run 清理 |
| 完成后仍显示等待；断线被当成继续等待 | PTY 完成任务，或响应未确认就断线 | ack 清除等待反馈；断线明确“审批结果未知” |
| 小屏退出选项不可见 | 24×11 审批中 Ctrl+Q | 显示紧凑退出选项，保留原退出生命周期 |
| Goal 占位提示覆盖审批引导 | Goal + Plan + 工具审批 | 审批占位提示优先，三种尺寸回归覆盖 |

以上发现已修复，最终回归通过。工程评审结论：可交由用户试用。用户随后反馈“已试用，交互符合预期”，核心交互真人验收通过；不外推为全部缩放场景或学习 Review 已完成。

## 真人 Ghostty / Owner 验收

建议运行 `VIBE_FAKE_DEMO=1 ./run_ink.sh --fake`，只操作测试会话：

- [x] 核心旅程：审批出现后继续输入中文与 y/s/p/n，Ctrl+R → 选择 → Enter；草稿、焦点、拒绝/停止提示符合预期。用户在本次对话确认“已试用，交互符合预期”。
- [ ] 额外核对粘贴、长按/按键重复与中文候选框交错的终端特性；自动回归已覆盖相关基础路径。
- [ ] Ctrl+R → 选择 → Enter；Esc 返回编辑；Ctrl+C 停止；拒绝不会发生预览中的写入。
- [ ] 缩放到 32×12 / 80×24 / 120×40，观察选项、焦点、拒绝和停止；更小窗口仍能安全退出。
- [ ] Owner 阅读增量、做一次 mutation、写五行证据笔记并能解释“文字何时变成动作”。

沿用 [协作协议](../../../docs/VIBE-PROTOCOL.md)：“没过 review，不进下一个切片。”
用户已验收 P1 核心交互；结合持续开发的明确指令，继续 P2 测量与实施。Owner mutation / 五行学习笔记单列待补，不冒充完成；P2 准备工作不记为产品交付。

> 2026-11-12 收尾：审批空闲草稿时支持 `y` 允许本次 / `n` 拒绝单键决定（有草稿时仍属草稿输入，不误审批）；Tab+Enter 与点击路径保留。

## 审批按钮增量（2026-11-08）

**触发与范围**：用户确认 P3 核心演示“没有问题”，但指出“当前审批是使用快捷键，这体验很差”。恢复持续开发后，优先改善工具审批。保留已有草稿、Gate、请求绑定、拒绝和停止语义；Plan 审阅卡本身仍使用原导航，P4 反馈编辑另行推进。

**已实现**：预览下方显示可点击的「允许本次写入/命令/操作」「拒绝」；较大授权范围收进「更多授权」，按后端 decisions 显示。Tab/Shift+Tab 在草稿与按钮间移动，方向键选择、Enter 确认，Esc 返回原草稿/光标。旧 Ctrl+R 字母选择仍兼容。按下有可见焦点，松开同一按钮才提交；拖动、松到外部、旧请求、缩放后的旧点击、粘贴里的鼠标序列不会提交。按钮位置依据实际 Ink 布局测量，正文滚动/拖选继续使用既有鼠标通道。

**红绿与自审**：沿用 App 终端输入→后端请求、实际 PTY→fake Python 的已约定公开 seam；新增 **9 项 TUI 回归**，未新增 Python 测试。主代理按 ponytail-review 检查本轮基线增量、输入调用者、测量坐标和后端收据路径；这是自审，不是独立模型评审。

| 发现 | 修复与证据 |
|---|---|
| 只能快捷键提交；鼠标没有审批入口 | 点击允许仅发送一次，保留中文草稿中间的光标；`click-red.log` → `click-green.log` |
| Tab 被斜杠补全消耗 | 审批期间 Tab 转入可见按钮，草稿不补全；同时去除此时错误的“Tab 补全”提示；`tab-red.log` → `tab-green.log` |
| 32×12 展开授权后只能看到标题 | 压缩小屏顶部与编辑器留白，先显示本进程范围说明，按钮/停止入口始终可见；`app-first.log` → `approval-green.log` |
| 鼠标按下缺少视觉反馈 | 同时显示选择标记；尺寸变化后旧按下不触发，须重新点击；`pressed-red.log` → `pressed-green.log` |

早期 App 测试的其余失败是旧快捷键文案断言，已改为新按钮/范围的可见性检查。PTY 脚本两次未通过源于等待不存在的首页文案及漏写回执方括号；都在 finally 中关闭进程，保留 `pty-initial-wait.log`、`pty-receipt-label.log`，未把它们记作产品失败或通过证据。

**最终验证**（对应 `verified-source.json`）：

- `npm --prefix tui test`：**509/509**，31.23 秒；相关审批/导航回归 **32/32**。
- `./run_tests.sh -q --junitxml=…`：**1877 passed / 4 skipped**，80.40 秒，0 failure/error。比前次多出的 1 项 Python 属于暂停前尚未接线的 P4 后台准备测试，已包含在本轮基线，不归属按钮增量。
- 实际 `./run_ink.sh --fake --autonomy ask`、`VIBE_TASK_RUNTIME=1 VIBE_FAKE_DEMO=plan`：80×24 和 120×40 鼠标允许写入/验收命令，文件与 completed 回执一致；32×12 鼠标拒绝，文件未创建且 paused。三条均保留中文草稿、只有一个 act Task/运行段、退出码 0。工作区为隔离的 `/private/tmp/mini-harness-approval-buttons-*`，不调用真实模型。
- 实际组件的暗/亮主题、32×12、80×24、120×40 构造帧已渲染并目视检查；按钮与提示未越界。PNG 是组件构造帧，不是 Ghostty 截图，不证明原生鼠标体验。

**证据与归属**：`.vibe/ux-approval-buttons-2026-11-08/` 保存 586 文件基线、红绿日志、全量结果、PTY 原始输出/磁盘核对、构造帧、源码冻结与 `approval-manifest.json`。本轮仅改审批呈现/输入、相关测试及进度文档，暂停前的 P4 两处未完成改动、core、侧栏和实验台规划均按基线保留，未提交。

**仍需真人确认**：新按钮在 Ghostty 中的点击、草稿/中文输入法交错和缩放体验。P1 旧键盘交互、P3 核心功能的既有真人通过不等于新按钮体验已验收；P2 真人、P3 原生重开/恢复与 Owner 学习 Review 继续单列待补。
