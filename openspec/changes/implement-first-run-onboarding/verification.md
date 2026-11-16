# 验证记录

## A 批：启动状态与入口基础（2026-11-14）

### 实现

- `scripts/start.py`：正常交互启动缺模型配置时不再进入终端问答，直接启动 Ink；非交互环境仍以 exit 2 给出 `./start.sh`、`--configure`、`--demo` 三条可执行路径。`--configure` / `--install` / `--doctor` / `--acceptance` / `--fake` / `--demo` 分支不变。
- `app/runtime/bootstrap.py`（新）：`--stdio` 且无可用模型 Key 时进入仅配置状态。只处理 `model list/save/test` 与 `bootstrap activate`，发出 `ready`（`config-bootstrap` 能力、无 `run`）与 `status idle`；不创建 Agent、TaskRuntime、SessionStore，不启动 MCP。`run/cancel/steer` 与其它命令返回 `bootstrap_unavailable`，不排队；重复 id、帧上限、审批应答与 `shutdown/closed` 沿用既有信封语义。
- 审批：保存与检测分别经 `ApprovalGate`，TUI 复用现有审批面板与 `y/n`、Tab/Enter、鼠标；审批 id 固定 `bootstrap`，事件仍按 workspace 核对。
- 进入项目：校验 `.env` 可解析、Key 非空且非占位、地址/模型有效后，用保存后的环境 `exec` 自身；新进程走既有启动路径，第二次 `ready` 带完整能力。失败留在配置页并返回错误。
- `app/runtime/model_settings.py`：构造不再要求 Agent；保存/检测只需 `ApprovalGate`，无 Agent 时 `apply` 明确拒绝。`provider_name()` 统一来源展示。空/占位 Key 不再作为可用 Key 或“已配置”。
- `app/runtime/model_config.py`：新增 `is_placeholder_key/usable_key/configured_key`；与 `scripts/doctor.py` 占位列表由测试保持同步。
- `scripts/start.py` 使用同一 `configured_key`：占位环境 Key 视为未配置；进入项目时占位/空环境值让位给刚保存的文件值。
- TUI：`ready.bootstrap` 与 `config-bootstrap` 能力；`bootstrap_activation` 事件；`use-bootstrap-settings.ts` 管理请求身份，`bootstrap-panel.tsx` 为首次配置页（起始两行信息层级、字段来源、Key 隐藏、保存并检测、仅保存、跳过检测确认、进入项目、退出）。32×12 / 80×24 / 120×40 均保持字段、动作与退出可见。

### 证据

- Python：`./run_tests.sh -q` 退出 0，**2084 collected / 2072 passed / 12 skipped**。跳过为环境限制：opt-in 真实浏览器 3、嵌套 Seatbelt 7、PTY 2。
- Ink：`npm --prefix tui test`（低并发全量）**814 tests / 812 pass / 2 fail**；两项失败均为既有真实 PTY 用例，本沙箱 `openpty: out of pty devices`，与改动前基线一致。
- 真实后端冒烟（无 Key 环境变量、临时工作区）：`run_vibe.sh --stdio` → `ready(config-bootstrap)` → model list → save 审批 → 写 `.env` → `bootstrap activate` → 同 PID 新 `ready` 含 `run` → shutdown；`.vibe/sessions` 在进入项目前未创建。
- 新增测试：`tests/test_bootstrap.py`（13）、`tests/test_start.py`（首启直接进 Ink、占位 Key）、`tests/test_model_settings.py`（无 Agent 保存、占位 Key、常量同步）、`tests/test_main.py`（stdio 分流）、`tui/tests/bootstrap-panel.test.tsx`（8）、`tui/tests/bootstrap-app.test.tsx`（4）。

### 未做 / 已知边界

- 项目确认页、标准尺寸线框细化、稍后配置与 Demo 分流属于 B 批；当前“进入项目”直接进入工作台首页。
- `.env` 语法损坏仍在启动器 exit 2，配置问题页属于 B 批。
- 检测通过只在当前界面进程内有效；编辑或再次保存会失效，重启后不把历史检测显示为可用。
- 进入项目后的新进程若在既有启动路径失败（例如无效 MCP 配置），会表现为后端退出；尚无自动退回配置页。
- 未进行真实模型/真实终端/真人体验验收；不把本批自动证据当作方案整体通过。

## B 批：首次配置 UI（B1–B4，2026-11-14）

### 实现

- `ModelSettingsError` 携带 `field`；模型名、服务地址、Key、代理的本地校验分别定位到字段，bootstrap 的 `model_settings_error` 事件带 `field`。TUI 收到后展开高级字段、把焦点移到该字段，并在字段下方就地显示错误。
- `ready.bootstrap` 增加 `defaults`（默认服务地址与模型）；首次配置页据此区分“默认 DeepSeek”和“自定义 OpenAI 兼容”，默认只显示 Key，地址/模型/代理折叠为自定义字段，切换服务仍要求重新填写 Key。
- 首次配置页重做：[bootstrap-panel.tsx](../../../tui/src/bootstrap-panel.tsx) 显示步骤、项目名/路径、服务字段与说明；保存并检测仍是一条连续路径（保存审批 → 保存回执 → 检测审批），检测失败显示“已保存；检测未通过：…”，编辑或再次保存会撤销旧的检测通过状态。
- 检测/保存进行中按钮变为“取消操作”，Esc 也可取消；bootstrap 接受 `cancel` 目标为当前配置操作，取消进行中的 httpx 请求，已在磁盘上的配置不撤回，重复提交被 pending 守卫拒绝。
- 检测通过（或明确确认跳过检测）后进入项目确认页：显示项目名、完整路径与边界说明，提供“进入项目/返回模型配置”；进入项目仍走既有激活流程，不扫描仓库、不改授权。
- 32×12 使用单字段紧凑模式，80×24/120×40 展开字段与来源说明；Key 始终不进入画面或审批预览。

### 证据

- Python：`./run_tests.sh -q` 退出 0，**2089 collected / 2077 passed / 12 skipped**（B4 增加 demo 标记测试；跳过同 A 批的环境项）。
- Ink：串行全量 **829 tests / 827 pass / 2 fail**，两项均为既有真实 PTY 用例（`openpty` 不可用）。
- 新增/更新测试：bootstrap 默认服务/字段错误/取消（Python），`tests/test_model_settings.py` 字段定位与默认值同步；`tui/tests/bootstrap-panel.test.tsx` 覆盖 32×12/80×24/120×40、服务折叠、保存并检测链、缺 Key 焦点、已保存未检测、项目确认、取消与退出；`tui/tests/bootstrap-app.test.tsx` 覆盖进入项目后等待新 ready。

### B4 稍后配置与 Demo

- 首次配置页新增次要操作“稍后配置”和“先体验 Demo”（可点击，Tab 可选中）。稍后配置进入 [deferred-home.tsx](../../../tui/src/deferred-home.tsx)：显示项目与“尚未连接模型”，可输入并保留草稿，Enter 只提示不能提交真实任务，Esc 返回配置页；返回后草稿仍在。
- Demo 由 TUI 启动独立 fake 后端（`--fake --test-session`，`VIBE_FAKE_DEMO=1`），经 `onDemo` 交接后替换客户端并重挂载；[demo-launch.ts](../../../tui/src/demo-launch.ts) 移除旧会话、真实模型参数并去重 `--fake`。ready 增加 `demo`；显式 Demo 的 header 持续显示“Demo · 不调用真实模型”，普通 fake/测试会话不显示。Demo 与真实会话隔离，转回真实使用需退出重开或新会话。
- 测试：DeferredHome 在 32×12/80×24 的尺寸、草稿保留与禁止提交；App 的稍后配置往返、Demo 入口交接、demo header；demo 参数隔离；后端 ready.demo 冒烟（fake + VIBE_FAKE_DEMO=1）。

### 未做 / 已知边界

- “稍后配置”页是首次启动用的轻量页面，不是已配置用户的完整首页；C 批会用正式首页替换。
- Demo 启动失败只显示在首次配置页/稍后配置页，不回退到其他状态。
- 项目确认页不显示授权范围细节；确认目录不等于授权读取全部文件或执行命令的说明已保留。
- 取消与保存完成的竞争情形只保证“已保存的配置保留”，不对已完成的保存回滚。
- 未进行真实模型与真实终端验收。

## C 批：首页与设置中心（C1–C4，2026-11-14）

### 实现

- 已配置用户首页重做（[workbench.tsx welcomeLines](../../../tui/src/workbench.tsx)）：标题“Mini · 今天准备做点什么？”，只显示项目名与模型/检测状态；三条示例（了解这个项目 / 定位一个问题 / 实现一个修改）用 `1/2/3` 填入可编辑草稿，不自动发送、不覆盖已有草稿；`/project 查看`、`/model 更改` 作为按需入口提示。
- 信息降噪：无任务时 header 收为 1 行（不显示路径、provider、会话 id、授权档位），隐藏右侧状态栏和状态行里的上下文/token/费用；显式切换授权档位后恢复正常 header 以便核对。
- 模型状态在本进程内区分“已配置 · 尚未检测 / 连接检测通过 / 检测未通过 / Demo · 不调用真实模型”；保存或应用后回到未检测，迟到回执不覆盖。
- 设置中心改为按目的分类的首页（[settings-panel.tsx](../../../tui/src/settings-panel.tsx)）：模型连接、任务预算、外部工具、权限与安全、更多设置；预算分类保留原数字编辑器与 g 分组；模型/MCP/权限进入原面板，返回后仍在设置中心；更多设置只读展示沙箱、通知与计价状态。
- 分类摘要直接来自后端 overview（单段限制、目标段数、MCP 配置/连接数、沙箱/通知/计价）；MCP 有配置但未连接、未配置计价时用 warning 色提示。
- 设置返回与关闭：分类与预算编辑草稿在返回中心后保留；有未保存编辑时关闭需要二次确认；设置关闭后工作台草稿、光标位置与对话阅读位置保留；窗口尺寸变化（80×24 → 100×30）后草稿不丢（App 测试覆盖）。

### 证据

- Python：未改后端行为，沿用 **2089 collected / 2077 passed / 12 skipped**。
- Ink：串行全量 **837 tests / 835 pass / 2 fail**（两项既有 PTY 用例）。
- 新增/更新测试：`home-app.test.tsx`（首页信息层次、1/2/3 填草稿不发送、设置关闭恢复草稿/光标/阅读位置、窗口尺寸变化）、`view-state.test.ts`（检测状态流转）、`settings.test.tsx`（分类返回保留预算草稿）、`settings-app.test.tsx`（分类内容、更多设置只读）、`workbench.test.ts`（新首页文案与宽度）。

### 未做 / 已知边界

- 首页“查看项目/更改”是命令提示，不是可点击按钮；设置分类行目前键盘操作，未加鼠标点击。
- 窗口尺寸变化只验证首页草稿；设置面板内部的滚动位置随尺寸变化的重排未单独覆盖。
- 首页示例不改变 `/guide` 的示例语义；两处入口共用同一批提示文案。
- 未做真实模型/终端真人验收。

## D 批：MCP 主动连接与审批（D1–D4，2026-11-14）

### 实现

- 前台 stdio 后端默认 `auto_mcp=False`：[app/__main__.py](../../../app/__main__.py) 的交互入口显式传入，`serve/run_stdio` 默认仍为 True 以保持既有 API/测试合同。启动只发送 `ready` 与 `status idle`，不再有 `startup` 状态与开场 `mcp_start` 审批；后台 worker 不经此路径，eval 继续禁用 MCP，合同不变。
- `ready.mcp = {configured, connected}`，连接生命周期变化后发送 `mcp_state`；TUI 在首页轻量显示“外部工具 0/2 已连接 · /mcp 选择服务后连接”，连接后更新，且不抢占对话。
- 连接仍由显式动作触发：`/mcp` 或“设置 → 外部工具”对选中服务/全部选择“重载”；审批预览新增“范围：仅此服务；其他服务与后台进程不变。”，连接审批与后续工具调用审批分离。
- 审批拒绝返回明确结果“连接未获允许；所选服务保持未连接，其他服务与后台进程不变。”，面板刷新后保持未连接，不再落入模糊失败。断开拒绝同样保留原连接。
- 设置中心从模型/MCP/权限子面板返回时自动刷新 overview，外部工具摘要不会显示过期的连接数。
- `--acceptance` 文案与 [WORKBENCH-ACCEPTANCE.md](../../../docs/WORKBENCH-ACCEPTANCE.md) 更新：启动无 MCP 审批，进入后按 /mcp 显式连接 alpha/beta。

### 证据

- 真实后端冒烟（本地 mcp-demo 服务、临时工作区）：ready `mcp={configured:1,connected:0}`；0.8s 内无 `startup` 状态或无 `approval_request`；显式 `mcp_config reload alpha` 才出现审批且包含范围行；拒绝后命令结果显示“连接未获允许”，`mcp_state` 保持 connected=0。
- Python：**2094 collected / 2082 passed / 12 skipped**（新增 test_mcp_foreground 3、test_main 1、test_mcp 预览 1；跳过同前）。
- Ink：串行全量 **839 tests / 837 pass / 2 fail**（两项既有 PTY 用例）。
- 新增/更新测试：`tests/test_mcp_foreground.py`、`tests/test_mcp.py`（审批范围行）、`tests/test_main.py`（前台 auto_mcp=False）、`tui/tests/backend.test.ts`（真实 FastMCP：启动无审批，显式 reload 才审批、工具出现、进程随 shutdown 退出）、`tui/tests/protocol.test.ts` + `view-state.test.ts`（mcp 计数与事件）、`workbench.test.ts`（首页未连接提示）。

### 未做 / 已知边界

- 普通 `--stdio` 被其他脚本当作 API 使用时也不再自动连接；这是前台入口合同，独立后台 worker/eval 不受影响。
- 服务用途仍只展示可靠来源（配置名、命令摘要、本地回显说明）；不根据名称推断能力。
- 未做真实远程 MCP、真实终端与真人验收。

## E 批：完整旅程与交接（E1、E3 自动部分，2026-11-14）

### 实现

- 新增真实进程首次启动旅程 `tests/test_first_run_journey.py`：无 Key 启动进入 bootstrap（ready 无 `run`、无会话目录）→ 未刷新版本保存被拒（不写文件）→ 刷新后保存经审批写入 `.env`（事件与 stderr 无 Key）→ `bootstrap activate` 在同一进程 exec 后得到带 `run` 的新 ready → shutdown 退出 0。全程不联网、不使用真实 Key。
- 验收矩阵落到 [acceptance.md](acceptance.md)：自动项逐条给证据，真实/真人项保持未勾选；配置文件损坏、鼠标点击分类等已知未做项明确标出。
- 交接文档 [handoff.md](handoff.md)：批次状态、可复跑命令、已知边界、用户终端执行的真实模型/终端旅程步骤与反馈清单、`.env` 备份/恢复步骤。
- 文档状态更新：方案头标记 A–D 完成、E 自动部分完成；roadmap/MILESTONES 记录本轮证据；WORKBENCH-ACCEPTANCE 指向交接文档。

### 证据

- Python：`./run_tests.sh -q` 退出 0，**2095 collected / 2083 passed / 12 skipped**（新增真实进程旅程 1；跳过为嵌套 Seatbelt 7、PTY 2、opt-in 浏览器 3）。
- Ink：串行全量 **839 tests / 837 pass / 2 fail**；两项均为既有真实 PTY 用例（本环境 `openpty` 不可用），与改动前基线一致。
- 真实进程旅程与 FastMCP 按需连接用例均通过；未使用用户 `.env` 中的真实 Key。

### 未完成 / 需用户

- E2 的全部真实场景（真实模型回复、真实鉴权/网络失败恢复、用户常用终端、完整真人旅程）需要用户按 handoff 执行；**E2 不勾选**。
- 配置文件语法损坏仍为启动器 exit 2，未做配置问题页（后续增量）。
- 真实 PTY 交互旅程因本环境无 pty 未运行。

## 收口补充：配置文件问题页与假模型标识（2026-11-14）

- 启动器遇到 `.env` 语法/读取错误时不再直接退出：交互终端下把安全错误摘要放入 `VIBE_BOOTSTRAP` / `VIBE_CONFIG_PROBLEM` 启动 Ink 修复页；`--doctor`、`--configure` 与 `--install` 仍需明确处理，非交互环境 exit 2 给出修复命令。错误摘要不含文件内容，原文件不写入。
- 仅配置状态在 problem 下拒绝保存、检测与进入项目，提示“不会覆盖原文件”；TUI 新增 [config-problem-panel.tsx](../../../tui/src/config-problem-panel.tsx)：显示项目、配置路径、修复步骤与“原文件未修改”，不提供保存按钮，Esc/Ctrl+Q 退出。
- 首页对非 Demo 的 fake provider 显示“测试模型 · 不调用真实服务”，避免把测试入口当作真实模型。
- 证据：Python **2100 collected / 2088 passed / 12 skipped**（新增 start 3、bootstrap 1、main 1）；Ink **842 tests / 840 pass / 2 fail**（新增 config-problem 3，失败仍为无 PTY 用例）。
- 仍未做：首页示例与设置分类行的鼠标点击；真实模型/终端/真人旅程（E2，需用户执行）。

## 收口补充：可点击控件（2026-11-14）

- 首页在无任务时增加三条示例按钮（[app.tsx](../../../tui/src/app.tsx) 的 `homeActions`）：鼠标点击与 `1/2/3` 等价，只填草稿、不发送；已有草稿时提示保持原样。
- 首次配置页的模型服务行可点击切换默认/自定义（高级字段展开/折叠），字段行同样有点击目标；主按钮原本就可点击。
- 设置中心的分类行可点击直接进入对应分区，保留键盘 ↑↓/Enter 操作。
- 证据：Ink **845 tests / 843 pass / 2 fail**（新增首页点击、服务行点击、分类点击 3 项；失败仍为无 PTY 用例）。Python 本轮未改，沿用 **2100 collected / 2088 passed / 12 skipped**。
- 仍未做：首页“查看项目/更改”按钮化；真实模型/终端/真人旅程（E2，需用户）。

## 收口补充：验收环境、设置摘要与检测提示（2026-11-14）

- `--acceptance` 在 env 中新增 `VIBE_ACCEPTANCE=1`；ready 增加 `acceptance`，TUI header 持续显示“验收环境 · 隔离配置”（优先于 Demo 标识），启动时发送隔离说明 notice（Demo 模型、本地 MCP、不读取现有 .env、工作区位置）。
- 设置中心“模型连接”分类摘要改为当前模型 + 检测状态（已检测通过/未通过/尚未检测），不再只有一句通用说明。
- 连接检测的非 200 响应给出可操作原因：401 凭据、403 权限、404 地址/模型、429 稍后重试；上游响应正文不进入界面（保留既有脱敏断言）。
- 证据：Python **2103 collected / 2091 passed / 12 skipped**（新增 acceptance 标记 2、检测提示 1），exit 0；Ink **849 tests / 847 pass / 2 fail**（新增 protocol/view-state/header/设置摘要 4 项；失败仍为无 PTY 用例）。
- 旧验收脚本假设同步更新：`scripts/browser_ink_smoke.py` 不再等待开场 MCP 审批，改为进入后 `/mcp` → 重载 → 审批 → 关闭面板再发任务；`scripts/browser_smoke.py` 是直接调用 McpManager 的 smoke，保持原有启动审批语义。脚本需要 PTY/浏览器，本环境未执行。
- 剩余：E2 真实模型/真实终端/真人验收（需用户）；首页“查看项目/更改”仍为命令提示。

## 收口补充：首页项目/模型按钮（2026-11-14）

- 首页摘要改为可操作行：项目行“查看项目/收起路径”展开或收起完整工作区路径；模型行“更改”打开模型设置面板。首页总览仍只显示项目名与模型/检测状态，技术信息按需展开。
- 首页示例、项目/模型摘要与设置入口都支持鼠标点击；无任务时不再需要输入斜杠命令才能查看路径或进入模型设置。
- 证据：Ink **850 tests / 848 pass / 2 fail**（新增首页摘要按钮用例 1；失败仍为无 PTY 用例）；Python 未改，**2103 collected / 2091 passed / 12 skipped**，exit 0。
- 至此方案自动化范围收口；仅剩 E2 真实模型/真实终端/真人验收（需用户）。
