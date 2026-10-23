# 使用指南

安装、模型配置、命令和运行边界。项目概览见 [README](../README.md)。以下命令在项目根目录执行。

## 怎么跑

首次安装（Python 3.11+，建议 3.12）：

```bash
uv sync --extra test
```

启动和测试脚本优先使用项目 `.venv`，也可以通过 `PYTHON=/path/to/python3` 指定解释器。
`core/` 保持标准库边界；FastMCP 与 HTTP 依赖只用于 `app/`，界面代码在 `tui/`（Ink）。

### 1) 不联网，先看 Ink 工作台

需要 Node.js 22+ 与上述 Python 环境；当前固定 Ink 8 / React 19.3，依赖版本见 `tui/package-lock.json`。

```bash
npm --prefix tui ci
./run_ink.sh --fake

# 使用已有 VIBE_* 模型配置，启动一个新会话
./run_ink.sh --workspace /path/to/your/repo

# 恢复最近有对话的会话；只展示进度，输入 /resume 才继续
./run_ink.sh --workspace /path/to/your/repo --continue

# 列出用户会话 ID、用途、原始消息数、更新时间和标题；无需 TTY 或 API key
./run_ink.sh --workspace /path/to/your/repo --list-sessions

# 打开更早的指定会话；不要同时加 --continue
./run_ink.sh --workspace /path/to/your/repo --session SESSION_ID

# 单独查看测试会话范围；--fake 会自动使用此范围
./run_ink.sh --workspace /path/to/your/repo --test-session --list-sessions

# 编译并运行 Node 通信和 Ink 界面测试
npm --prefix tui test
```

`run_ink.sh` 每次先编译 TypeScript，再启动界面；Node 通过 JSON Lines 调用同一套 Python Agent，继续遵循 `PYTHON` 解释器选择与 `VIBE_*` 配置。界面需要在交互式终端运行；`--help`、`--list-sessions` 可在普通管道中使用。

当前采用全屏布局，上方显示项目、模型与会话，中间阅读对话和工具，下方编辑多行草稿并显示真实用量。退出恢复原终端内容。消息、输入与状态区采用底色深浅和留白分层，普通回复不画外框；失败、审批与选中工具用轻染色强调。

`/theme light` 使用浅色底板，`/theme dark` 使用深色底板，文字与背景配套。默认 `system` 根据终端提供的 `COLORFGBG` 背景提示选择；未提供或无法识别时回退深色，浅色终端可手动切换。`/theme` 查看当前主题及实际颜色能力。真彩、256 色、16 色分别使用配套颜色；256 色采用明确灰阶，避免浅灰退化成白色、浅蓝变成亮青。`NO_COLOR` 非空时关闭颜色；显式 `FORCE_COLOR` 优先。

界面按终端字符格布局，字体、字号和行距由终端设置。大窗口里的短会话会保留空白、输入区位于底部；恢复后的空闲会话不会显示构造预览中的工具活动或预算数值。

| 操作 | 键位 / 命令 |
|---|---|
| 发送 / 换行 | `Enter` / `Ctrl+J`；终端支持时也可 `Shift+Enter` |
| 历史与草稿 | 首末行用 `↑↓` 浏览历史；多行内用上下键移动，粘贴不会自动发送 |
| 回看对话 | 阅读区内滚轮 / 触控板、`PgUp/PgDn` 翻页；`Ctrl+P/N` 或 `Ctrl+↑/↓` 逐行 |
| 阅读焦点 / 返回输入 | `Ctrl+R` 切换；阅读时 `↑↓` 滚动、空格下一页、`Home/End` 到首尾；`Esc` 返回输入 |
| 返回最新 | `Ctrl+End`；回看时新文本不抢位置，滚动条和行号显示当前位置 |
| 工具详情 | `Tab` 选本会话已发生工具，`Ctrl+O` 查看完整参数和输出，`Esc` 返回 |
| 复制输出 | 鼠标在对话区拖选，松开即复制所选文本；`Ctrl+Y` 或 `/copy` 复制工具详情 / 审批预览 / 最近回复；本机剪贴板命令优先，失败回退 OSC 52 |
| 审批 | `y` 单次、`s` 精确规则、`p` 工作区、`n` 拒绝；按后端允许范围显示，粘贴不批准 |
| 取消 / 退出 | `Ctrl+C` 取消当前任务，空闲时退出；`Ctrl+Q` 或 `/quit` 退出并清理 |
| 运行中纠偏 / 排队 | 运行中 `Enter` 提交纠偏（下一次模型请求生效）；`/followup <内容>` 排到本轮结束后自动继续 |
| 续做与权限 | `/resume` 显式续做、`/permissions` 查看、`/permissions reset` 撤销 |
| 帮助与信息 | `/help`、`/cost` 会话用量、`/tools` 已注册工具、`/mcp` 服务、`/session` 会话路径 |
| 查看改动 | `/diff` 经审批执行 git diff，全文可翻页 |
| 查看技能 | `/skills`（或 `/skill`）重新扫描 `.vibe/skills` 并列出摘要，不调用模型 |

运行中输入框保持可用：`Enter` 提交纠偏，`/followup <内容>` 排队后续消息；阅读、审批和工具详情聚焦时暂停编辑，滚动不会改写草稿。对话区支持鼠标拖选：按住左键拖动看高亮，松开即复制所选文本，`Esc` 取消；要整段复制用 `Ctrl+Y` / `/copy`。没有鼠标事件的终端可以用上述键盘操作。工具结果默认摘要，全文在详情页查看；原始消息和结果仍保存在 `.vibe/sessions/`。取消后需核对已发生的副作用。

状态与消息按块展示：等待模型、回复、调用工具、审批、暂停和错误分别标注；宽屏右侧显示当前任务、工具记录和真实预算，窄屏使用紧凑状态条。命令失败、超时、取消、未知结果显示实际状态，长工具名不会遮住结果。界面与验证见 [历史阅读与状态分区](specs/ink-reading-state.md)。

Ink 的 `--continue` 只选择最近有对话的用户会话，普通列表和按 ID 打开也排除已标记的测试会话。`--fake`、`--provider fake` 或配置 `VIBE_PROVIDER=fake` 自动使用测试范围；真实模型烟测用 `--test-session` 明确标记。两个范围各自列出、继续和按 ID 打开，测试模式也只加载测试会话；界面标题显示“测试会话”。历史区从原始消息日志恢复，压缩前的正文和工具记录仍可回看；模型续做读取压缩上下文，两者独立。恢复后不会自动调用模型或重放工具；`/resume` 才开启新一轮预算。已有 `VIBE_MCP_*` 配置与本进程权限管理继续可用，MCP 初始化期间也能取消，服务启动与工具调用分别审批。新命令结果显示真实退出码、超时或取消，旧记录没有执行元数据时显示未记录；零退出本身不证明需求验收通过。

默认入口为 Ink；Textual 已于 2026-10-23 退役（见 [里程碑](MILESTONES.md)）。运行中纠偏与后续消息已接通确定性回归，见 [运行中纠偏](specs/run-time-correction.md)；会话选择器与真实模型联合任务仍待后续验收，eval 继续使用 `./run_vibe.sh --eval`。

### 2) 真模型（OpenAI-compatible）

```bash
export VIBE_API_KEY=sk-...
export VIBE_BASE_URL=https://api.deepseek.com/v1
export VIBE_MODEL=deepseek-chat
./run_ink.sh --workspace /path/to/your/repo
```

只验证模型，不挂工具：

```bash
./run_ink.sh --chat-only
```

> provider 默认 `trust_env=False`，不自动读取代理环境变量。远程 API 需要代理时设 `VIBE_TRUST_ENV=1`。

Ollama 也可以走同一入口：

```bash
VIBE_BASE_URL=http://localhost:11434/v1 \
VIBE_API_KEY=ollama \
VIBE_MODEL=qwen2.5-coder:7b \
./run_ink.sh --workspace /path/to/your/repo
```

### 3) 会话 / 成本 / eval

```bash
# 打开 workspace 最新 session；明确暂停的任务输入 /resume 继续
./run_ink.sh --workspace /path/to/repo --continue

# 先离线验证预算，再逐步运行内部真实任务
./run_eval.sh check-budget
./run_vibe.sh --workspace . --eval --eval-limit 1
# 首题通过后再用 --eval-limit 3
```

工作台里：

- `/cost`：本轮输入/输出 tokens，以及本 session 的 tokens / cost / tools；
- `/context`：查看估算分区、内容块来源和本轮快照；按 `Ctrl+K` 打开，运行中也能查看；
- `/resume`：明确继续暂停任务，开启一轮新预算；
- `/followup <内容>`：运行中排队为后续消息，本轮结束后自动继续；空闲时等同直接发送；
- `/copy`：复制当前可见内容（工具详情优先，其次审批预览，否则最近一条 Agent 回复）；拖选后的文本也会在松开时自动复制；
- `/project`：查看当前项目的背景条目与来源；`/project add <文本>` 添加，`/project remove <序号>` 删除；
- `/permissions`：查看当前工作区授权；`/permissions reset` 撤销本进程授权；
- 写文件或执行命令会弹审批：`y/a` once、`s` session、`n/d` deny。

3 分钟 demo：`./scripts/demo.sh`，脚本见 [`docs/DEMO.md`](DEMO.md)。

### 4) 测试

```bash
./run_tests.sh -q
```

`run_tests.sh` / `run_vibe.sh` 默认使用项目 `.venv` 中的依赖，不依赖全局 `pytest`；显式指定其他 Python 时保留 `.pylibs` 兼容路径。

### 5) 本地编码基准

```bash
python3 scripts/bench_local.py --prepare   # 只注入 bug、确认初始失败，不需要 API key
python3 scripts/bench_local.py             # 真实模型跑全部任务，沿用当前 VIBE_* 配置
python3 scripts/bench_local.py --tasks recall-guard --timeout 600
```

任务与评分见 [本地编码基准](specs/local-coding-benchmark.md)；报告写入 `.vibe/bench-local/<时间戳>/summary.json`，每个任务另有 `report.json` 与 Agent 输出日志。

## 项目背景（W4）

新任务默认读取当前工作区的 `.vibe/project-profile.json`，把用户维护的项目摘要作为 `[PROJECT BACKGROUND]` 注入（逐条带来源，最多 4000 字符）。每次任务前重新读取，因此 `/project add`、`/project remove` 或直接编辑文件后，下一次任务立即使用新内容。换工作区不会串用；损坏的文件按空背景处理，不阻塞任务。

```bash
/project                                  # 查看条目与来源
/project add 只改 calc.py，不改测试       # 来源记为 user
/project remove 2                         # 删除第 2 条
```

profile 是摘要和来源指针，不替代 `AGENTS.md` 与项目文档；与文件冲突时以文件为准。

## 上下文预算（默认配置 1M）

默认分配覆盖全部窗口，history 可以使用其他输入分区空闲的额度。窗口是容量上限，每次请求仍只发送当前需要的上下文。

| 分区 | 默认额度 | 用途 |
|---|---:|---|
| S system | 8,000 | 系统指令、项目约束、任务状态 |
| M memory | 64,000 | 给后续检索、项目资料保留的容量；目前未接入长期用户记忆 |
| T tools | 16,000 | 工具定义；工具返回内容计入 history |
| H history | 872,000 | 对话、工具调用和结果、分层摘要 |
| R output | 32,000 | 输出预留 |
| 安全余量 | 8,000 | token 估算和协议开销余量 |
| 合计 | 1,000,000 | |

history 实际额度 = 窗口 − 输出预留 − 安全余量 − 实际 system/memory/tools 用量；空闲时最高 960k。显式设置 `VIBE_HISTORY_CAP` 会再取较小值，未分配额度可在 policy 中查看。小窗口下默认预留同比缩放，预算默认值统一由 `core/budget.py` 提供。

可调：`VIBE_CONTEXT_WINDOW`、`VIBE_RESERVE_OUTPUT`、`VIBE_SCRATCH_CAP`、`VIBE_SYSTEM_CAP`、`VIBE_MEMORY_CAP`、`VIBE_TOOLS_CAP`、`VIBE_HISTORY_CAP`、`VIBE_KEEP_RECENT_GROUPS`、`VIBE_SUMMARY_MODEL`、`VIBE_SUMMARY_MAX_CHARS`。旧 `VIBE_MAX_TOKENS` 作为 context window 后备别名，优先使用 `VIBE_CONTEXT_WINDOW`。窗口配置需要与提供方模型能力对应；输出预留用于本地容量计算。

### 分区快照与回放

新会话打开后即显示基础上下文的估算占用和四区分布，无须先发送消息。`/context` 可查看已装载的基础指令、项目规则、技能目录和工具定义；界面标记“尚未请求模型”，累计模型消耗为 0。MCP 初始化结束后会按实际注册的工具刷新。恢复会话则统计当前装载的任务状态与工作历史；首次发送任务后切换到本轮请求统计。

`/context` 展示当前占用条、四区用量与额度、输出和安全预留、逐步骤变化及块来源。`←/→` 切换快照，`↑/↓` 或滚轮翻阅，`Esc` 返回；`l` 恢复跟随最新。界面最多保留本轮最近 100 张快照，新任务或 `/resume` 开启新一轮；重开会话时也能查看上轮已有记录。

分区用连续彩色细线堆积展示：青色 system、紫色 memory、琥珀色 tools、绿色 history，灰色为剩余输入容量。标签和数值随线条显示，当前为 0 的分区不占线段。容量条保持真实比例；新会话占用很小时，另用标明“已用构成”的放大条看清分区。紧凑状态同样保留实际用量/容量，输出与安全预留单列。

快照区分请求前、主动压缩前和压缩后；最终请求的纠偏指令、工具定义及无工具收尾按实际内容计数，重试有独立请求 ID。分区使用当前字符启发式估算，不是供应商逐区精确计数；当前占用与 `/cost` 累计消耗分别记录，压缩不会清零累计消耗。

完整快照保存在 `.vibe/sessions/<session>/trace.jsonl` 的 `context_snapshot` 事件，块只记录来源、hash 和用量，不重复保存正文。块数很多时，界面保留主要块并按分区合并其余块，最多 256 项，总量不变；完整明细仍在 trace。接口与验证范围见 [分区快照规格](specs/context-observability.md)。

## 单次任务预算与暂停

`VIBE_MAX_RUN_TOKENS` 默认 **1,000,000**，累计一次 run 中各轮请求的输入和输出；重复发送的历史也再次计入。一次 run 从输入指令开始，直到任务完成或停止。它与单请求上下文容量是独立限制，即使默认都为 1M。

- 状态栏分行显示任务累计用量、上限和剩余、上下文容量及项目；`/cost` 查看输入和输出，估算值标“约”。
- 独立的 `VIBE_COMPACT_INTERVAL_TOKENS` 默认 200,000：上次压缩后累计消耗达到其 80%（160k）时，自动压缩并继续当前任务；设为 0 关闭周期触发。history 达到实际容量的 80% 也会触发。
- 压缩保留同一 run、累计用量、步骤、工具与时间限制；不清零任务预算。下一次请求预估付不起时，先压缩再重算，仍付不起才进行一次无工具收尾，余额不足则仅保存本地暂停说明。
- 超限响应仍保存到会话；其中未执行的工具请求记录为 `not_executed`，不能当成已经完成。
- `--continue` 打开明确暂停的会话时先展示进度；输入 `/resume` 后才开启新一轮预算，并要求先核对保存进度和未执行操作。也可以输入新的具体要求。
- 用量在完整模型响应后更新，因此一次响应仍可能越过上限。这是累计停止保护，不是提供方计费的精确硬限额。

## 上下文与状态

- **`recall(ref)`**：`[offloaded: ref]` 不再只读；模型可以取回原文。
- **80% 主动压缩**：保留原始目标、最新用户要求和近期完整工具组；旧历史分块生成 L1 摘要，再聚合为 L2。默认近期 8 组，余额紧张时逐组缩短。摘要复用，原文保存在 `.vibe/offload/`，可用 `recall(ref)` 取回。
- **成本与恢复**：自动压缩使用本地确定性摘要，继续执行时保留工具；真正接近任务总预算才调用一次模型收尾。压缩结果追加到会话日志，重启及 `/resume` 使用该工作上下文；原始消息日志保留。
- **窗口容量压缩**：常规请求超 history 容量时，原有 `LLMSummarizer` 仍可注入（`VIBE_SUMMARY_MODEL` 可指定摘要模型，失败退回本地摘要）。这条旧路径的摘要请求尚未纳入 run 用量；80% 主动压缩不走它。
- **TaskState**：harness 记录目标、计划、文件变化与 `command_results`（命令、用途、状态、退出码和输出），作为 `task_state` system 消息注入；不进 stable prefix hash。`tests_run` 兼容旧记录，仅新显式标记验证的命令会追加，不能单凭命令名作为通过证明。
- **工作台**：状态栏显示 `todo done/total`。

## 写 + 审批

- `write_file` / `edit_file` 是 `risk=write`，必须过审批。
- 审批弹窗：`y/a` 仅本次；`s` 本会话此工具与此文件、此目录下相同命令或远端工具相同参数；`p` 批准本进程工作区发起的内置文件写入和命令；`n/d` 拒绝。
- `p` 不覆盖 MCP 工具，换工作区或重启不继承。授权后的每个动作仍经过 `ApprovalGate` 并审计；`/permissions` 查看，`/permissions reset` 撤销。
- 等待人工审批不设截止时间，也不计入 `max_run_seconds`。`Ctrl+C` 可取消等待、关闭弹窗并恢复输入；取消之后不会再执行待批动作。
- 预览：`difflib.unified_diff`，最多 200 行 / 20k 字符。
- 写前备份到 `.vibe/backups/`；审计写 `.vibe/sessions/*.jsonl`（参数/预览只存哈希）。
- 没有审批 gate 时 fail closed：`ERROR approval_required`；拒绝回灌 `ERROR denied_by_user`。

默认不按工具次数或模型轮数停止。需要人为限定轮数时可用 `--max-steps N` 或 `VIBE_MAX_STEPS`；0 表示不限。eval 保留各测试任务显式设置的轮数。

## Bash + 安全

- `bash` 是 `risk=exec`，必须过审批；预览显示命令、cwd、timeout。
- 命令输出和结构化执行状态同时回灌并保存：零退出、非零退出、超时、取消、未知；非零退出和超时令工具 `ok=False`。模型用 `verification:true` 明确测试、构建或检查用途，普通命令不自动计作测试。
- 超时用进程组 kill；输出只保留最后 10k 字符。
- 危险命令黑名单：`rm -rf /`、`mkfs`、`dd if=`、fork bomb、`sudo`、`git push --force`、`git reset --hard`、`curl|sh` 等。
- 运行预算：任务累计 tokens、费用与 `max_run_seconds=600`（排除人工审批等待）仍生效。工具次数只用于统计；无进展通过结果检测、纠偏和降级处理。命令执行超时与模型超时仍独立生效。
- **这不是沙箱**：命令仍以你的用户权限运行，`cwd` 只是起点，命令可以 `cd` 到别处。审批 + 黑名单 + 超时是当前边界；VM/seatbelt/landlock 是后续。

可调：`VIBE_MAX_RUN_SECONDS`。旧 `VIBE_MAX_TOOL_CALLS`、`VIBE_MAX_REPEATS` 已不再读取。

## 免费 Web 搜索

安装依赖后，Ink 入口默认向模型提供内置 `web_search`；`--chat-only` 仍不挂工具。使用 [DDGS 9.16.0](https://pypi.org/project/ddgs/9.16.0/)，无需注册或配置搜索 API Key。模型本身仍按原来的配置运行。

例如直接输入：“搜索 Python 官方文档中 asyncio.TaskGroup 的用法，给我来源链接。”模型可调用：

```json
{"query": "site:docs.python.org asyncio TaskGroup", "max_results": 5, "region": "us-en", "timelimit": "y"}
```

- `query` 必填，1–500 字符；`max_results` 默认 5，最多 10；`region` 默认 `us-en`，中文可用 `cn-zh`；`timelimit` 可省略，或用 `d/w/m/y` 限制最近一天/周/月/年，过滤效果取决于上游引擎。
- 调用使用 `risk=network`，先由 `ApprovalGate` 展示搜索词并审批。Ink 支持单次或同参数精确规则；工作区写入/命令授权不覆盖搜索。
- DDGS 使用 `backend=auto` 聚合搜索，查询可能发往多个第三方搜索引擎。预览会展示完整查询，请勿放入凭据或私密代码。网络需要代理时，可使用库支持的 `DDGS_PROXY=http://127.0.0.1:7890`，与模型的 `VIBE_TRUST_ENV` 分开。
- 返回有界的 JSON：`query/source/notice/count/results`，每项包含 `title/url/snippet`。这只是搜索摘要，不能视为已读取和核实网页全文；外部内容按不可信资料处理，回答应附来源链接。
- 单请求超时 5 秒，整次检索最多 20 秒；取消或超时会终止并回收搜索进程。限流、网络错误和后端无结果异常如实回灌，不自动重试，也不以空列表伪装失败。后端正常返回空列表时显式显示 `count=0`。

DDGS 是第三方网页检索库，并非搜索引擎提供的稳定 API；验证码、网络限制和页面改版均可能使检索失败。它无需付费 API 凭据，但不承诺无限次数或可用性。选型与免费额度比较见 [调研记录](research/web-search-2026-10-21.md)。

## MCP 外部工具

`app/mcp.py` 使用 [FastMCP 4.1.1 Client](https://gofastmcp.com/clients/client) 接入 stdio MCP 服务，由库负责版本协商、消息分发和工具结果解析。工作台准备好后先审批服务启动，再发现工具并交给 Agent；连接、调用和关闭都在同一事件循环中运行。

```jsonc
// .vibe/mcp.json
{
  "servers": {
    "fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
      "env": {},
      "cwd": null,          // 相对 workspace
      "timeout": 60,        // 单次调用超时（秒）
      "risk": "exec"        // 工具调用需审批；只有明确只读的服务才设 read
    }
  }
}
```

```bash
VIBE_MCP_CONFIG=/path/to/mcp.json ./run_ink.sh   # 文件优先
VIBE_MCP_SERVERS='{"fs":{"command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","."]}}' ./run_ink.sh
```

- 支持原有 `servers`、标准 `mcpServers` 外层和直接的 server 字典；目前开放 stdio 配置。
- 配置错误直接报错退出；单个服务被拒绝或启动失败会在工作台提示，其余服务照常加载。`--chat-only` 不启动服务。
- 启动命令以 `mcp_start` 经过 `ApprovalGate` 并审计；预览显示命令、cwd 和环境变量名，环境变量值使用摘要区分授权。子进程仅继承 SDK 的基础环境及配置中的 `env`，需要的凭据请显式配置。
- 远端工具默认 `risk=exec`，每次调用单独审批；`s` 授权本会话同工具同参数，`p` 不覆盖 MCP 启动或工具。启动获批不代表工具调用获批。
- 工具名通常为 `mcp_<server>_<tool>`；非法字符、下划线造成的边界歧义、超过 64 字符或命名碰撞时添加固定来源摘要。其他服务被拒绝或启动失败也不会改变这个映射。
- 工具执行错误回灌模型；超时包含发送和等待，并取消本次调用、停止对应进程组。连接不自动重建，工具不自动重试，避免重复执行有副作用的动作。
- 正常退出、启动取消和失败都清理连接；POSIX 下同时清理服务进程组中残留的后代进程。
- `Ctrl+C` 会取消正在进行的 MCP 调用并停止对应服务。已开始的外部操作记录为结果未知，不承诺回滚；后续任务可继续使用内置工具。取消终态在重启后等待 `/resume` 或新指令。
- 结果保留 20k 字符；stderr 日志写入 `.vibe/mcp/`，启动失败展示末尾 4k 字节用于诊断。

## Session 与恢复

- 新对话创建 `.vibe/sessions/<id>/`：`events.jsonl`、`state.json`、`meta.json`、`audit.jsonl`；`--continue` / `--session ID` 恢复已有会话。
- `meta.json` 的 `purpose` 保存 `user` / `test`。普通模式只访问用户范围；`--fake`、fake provider 或 `--test-session` 只访问测试范围。列表显示用途，测试模式的标题有明确标识。
- 旧会话缺少 `purpose` 时兼容用户范围；只给来源已确认的旧烟测记录补测试标记，保留原始消息。损坏或未知标记的元数据不会被当成正常会话加载。
- Agent 保留 history，工作台多轮对话会将已有上下文带给模型。
- `--continue` 按有效消息事件时间选择当前工作区、当前用途范围内最近有对话的 session，跳过空会话，新增附件或仅打开会话不会影响排序。`--list-sessions` 只读列出用途、完整日志的消息数与标题；`--session ID` 精确打开同一范围内的指定会话，与 `--continue` 互斥。
- 模型读取最近有效压缩快照与后续消息；Ink 阅读区读取完整原始消息日志，旧正文和工具结果可回看，过大的单条消息会明确标注显示截断。Ink 所有续做都等 `/resume` 或新指令；对没有终态记录的中断轮次，启动恢复只展示进度，不会自动重放。
- 崩溃后如果 `assistant(tool_calls)` 没有对应 `tool` 结果，恢复时补一条 `ERROR interrupted: tool result unknown after crash`，并记 `recovered_tool` 事件——**不假装成功**。
- 事件日志 append-only；`state.json` 是 TaskState 投影；审批审计和 session 放在同一目录。
- 自动化烟测使用临时工作区，并在调用 `SessionStore.new(..., purpose="test")` 时明确用途；工具仍按所选工作区和审批规则执行。eval 的运行记录位于 `.vibe/evals/`。

试崩溃恢复：

```bash
./run_ink.sh --workspace /tmp/demo-repo
# 跑到一半 kill -9
./run_ink.sh --workspace /tmp/demo-repo --continue
```

## Trace / 成本 / Eval

- 每次 run 写 `session/trace.jsonl`：`run_start`、`llm_start`、`llm_end`、`budget_update`、`run_end` 等；停止记录包含原因、预算及已进入执行流程的工具数，预算阻止的工具不计入已执行数量。
- 工作台输入 `/cost`：显示本轮输入/输出，以及本 session 的 runs、tokens、cost、tool calls、最后状态；不会发给模型。
- 价格按 token 计：`VIBE_INPUT_PRICE_PER_1M`、`VIBE_OUTPUT_PRICE_PER_1M`（默认 0）；0 表示未配置价格，不能据此认定模型调用免费。
- 优先使用 provider 的 `stream_options.include_usage`；不支持可设 `VIBE_STREAM_USAGE=0`，此时按请求与输出估算 token 并明确标注，预算保护仍生效。
- `./run_vibe.sh --eval` 跑 10 个内部任务，经过 `ApprovalGate` 自动审批，每个任务独立 workspace，输出：
  `.vibe/evals/<fake|model>/<timestamp>/report.json` + `report.md`。只有相同任务、模型与配置 fingerprint 的记录才比较 `delta`。
- `--eval-limit N` 只跑前 N 个；`--eval-tasks path` 用自己的任务文件。
- 内部 eval 使用真实 `Config` 预算；只有 Agent 正常结束且评分检查全部通过才计为通过，命令评分任务的测试文件会受保护。任务在宿主执行，面向受信本地用例，没有操作系统沙箱或 shell 断网隔离。

```bash
# 真实模型跑分（示例价格，按你的 provider 改）
VIBE_INPUT_PRICE_PER_1M=0.27 \
VIBE_OUTPUT_PRICE_PER_1M=1.10 \
./run_vibe.sh --eval --eval-limit 3
```

真实模型 eval 用于观察编码效果；`--fake` 使用脚本响应，不能作为模型能力成绩。先按预算边界做离线检查：

```bash
./run_eval.sh check-budget                 # 默认 --level all
./run_eval.sh check-budget --level context
./run_eval.sh check-budget --level compaction
./run_eval.sh check-budget --level total
```

这些检查固定使用 fake 模型，无需 API Key 或 Docker；分别核对单请求容量、压缩后继续，以及累计预算耗尽后的暂停和工具副作用边界。默认依次执行全部层级，前层未通过时跳过后层。报告与逐层轨迹保存在 `.vibe/benchmarks/budget-checks/<timestamp>/`；已有一次实际 CLI 检查 3/3 层通过，见 [验证证据](specs/evaluation.md#已有验证证据2026-10-21)。预期暂停的用例通过表示预算机制正确，不表示编码任务完成。三种额度独立：`VIBE_CONTEXT_WINDOW` 默认 1M，`VIBE_COMPACT_INTERVAL_TOKENS` 默认 200k，`VIBE_MAX_RUN_TOKENS` 默认 1M；压缩不清零累计用量。

开发验收依次运行：离线预算自检 → 内部真实 eval `--eval-limit 1`、再 `3` → SWE 首题 → 固定 3 题。真实阶段仅在用户显式运行时调用模型，不因未解决自动提高预算或重新评测；已有模型请求重试仍受配置与当前 run 预算限制。每阶段分别保留成功、暂停与错误记录。

公开仓库任务使用独立入口 `./run_eval.sh run`，已接入固定版本的 SWE-bench Lite 开发集和官方 `swebench==4.1.0` 评分器。清单有 10 题，当前默认只跑首题，显式 `--limit 3` 才扩展到固定前三题。2026-10-21 用户用 `deepseek-flash` 运行首题，首轮 HTTP 400 协议错误已修复；第二轮 11 次请求成功，累计 100711/100000 tokens 后停止，尚无补丁，官方跳过测试。当时入口写死每题 50 步、100000 token、600 秒，覆盖本机配置；当前已改为继承配置。两轮原始记录保留于 `.vibe/benchmarks/swebench-lite-dev/model/20261021-164901-333616/` 与 `20261021-170835-513176/`；真实修复仍待完成。

```bash
uv sync --extra test --extra eval
./run_eval.sh doctor
./run_eval.sh prepare --limit 1
./run_eval.sh run --fake --limit 1 --skip-grade
./run_eval.sh validate --limit 1
# 内部任务通过后，用本机模型和预算运行首题，默认自动评分：
./run_eval.sh run --limit 1
# 首题完成后再运行固定 3 题：
./run_eval.sh run --limit 3
./run_eval.sh grade --run-dir .vibe/benchmarks/swebench-lite-dev/model/<timestamp>
```

公开评测需要可用的 Docker daemon，M 系列 Mac 需支持 amd64 模拟；首次拉取官方镜像可能较大。可用 `DOCKER_HOST` 或 `--docker-host` 指定 Colima 等实例的 socket，入口不会切换全局 context。

`run` 与 `doctor` 默认继承 `Config.from_env()`：先使用环境变量，再取代码默认值。未配置时每题累计 token 预算 1M、运行时限 600 秒、模型轮数不限；压缩周期 200k、单请求上下文容量 1M。只有显式 `--max-steps`、`--max-tokens`、`--max-seconds` 覆盖对应值；`--max-steps 0` 和 `--max-tokens 0` 分别表示不限轮数和累计 token，时间必须大于 0。`doctor` 的 `budget` 会展示实际配置，先核对再运行。此入口每次创建新工作区，暂不支持从旧运行 `/resume`。

token 上限累计每轮 prompt 与 completion，重复历史也计入；首轮累计 95,917 token 后为下一请求压缩，并非单次上下文长达 95,917 token。一次响应后才更新实际用量，因此最终响应仍可能越过余额。

`validate` 只验证第一题的空补丁与参考补丁，参考答案不提供给 Agent。公开评测的命令在无网容器运行，模型请求留在宿主，API Key 不进容器；文件工具操作宿主共享目录，仍有静态路径边界的限制。报告与轨迹位于 `.vibe/benchmarks/`。CLI 分开汇总解决、正常未解决、预算暂停与真实错误：全部解决或离线预算机制通过退出 0；正常未解决、预算暂停或未评分退出 1；接口、执行环境、评分、依赖与配置错误退出 2。`--skip-grade` 的 `ungraded` 不表示通过。完整协议、已有自检证据与隔离边界见 [编码 Agent 评测](specs/evaluation.md)。

## 项目规则（AGENTS.md）

启动时读取项目根 `AGENTS.md`；没有就退回 `.vibe/AGENTS.md`：

- 最多 12k 字符，超出截断；
- 作为 `[PROJECT INSTRUCTIONS]` 追加进 system prompt；
- 工作台启动日志显示 `AGENTS.md loaded`；
- 示例：`examples/demo-repo/AGENTS.md`。

## Skill（可复用操作手册）

技能分为**常驻注册目录**和**临时激活正文**：名称、用途和来源路径始终进入 Agent 的系统上下文；正文只在确实使用时加载，使用结束后清理。

默认发现当前项目与本机的标准技能目录：

```text
项目：.vibe/skills/  .agents/skills/  .claude/skills/  skills/
本机：~/.vibe/skills/  ~/.agents/skills/  ~/.codex/skills/  ~/.claude/skills/
```

每个根支持 `<name>/SKILL.md`、`<name>.md` 以及最多三级分类（包括 `.system`）；找到一个技能目录后，不把其中的 `references` / `scripts` / `assets` 当成其他技能。新增或修改文件可用 `/skills`、`list_skills` 或下一轮任务开始时重新发现。

文件格式：开头几行 `key: value`（`name`、`description`），空行后是正文步骤；正文为空则忽略该文件。

```markdown
description: 给新模块补单元测试

1. 先读 tests/ 里同类模块的测试写法
2. 只测行为，不测实现细节
3. 跑 PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_xxx.py -q
```

Agent 从常驻目录判断哪些技能与当前工作有关，调用 `load_skill(name)` 将匹配技能的正文临时装入 `[ACTIVE SKILLS]`，根据步骤调用普通工具。相关工作完成后应主动 `unload_skill(name)`；程序在每轮完成、暂停、取消或报错时统一清理剩余临时正文，保留常驻目录和磁盘文件。正文贯穿本轮多次模型和工具调用，也不会因历史压缩丢失。

工具历史只保存激活/卸载回执，防止正文留在历史里持续占用上下文。手动命令是查看和控制入口，正常流程由 Agent 按任务决定加载与卸载，无需用户逐项操作。

工作台支持以下管理命令，不调用模型：

| 命令 | 行为 |
|---|---|
| `/skills` 或 `/skill` | 重新发现并列出技能、来源、状态和错误 |
| `/skills refresh` | 重读配置和文件；更新已激活正文，移除已删除或禁用项 |
| `/skills activate NAME` | 激活或更新指定技能 |
| `/skills unload NAME` | 卸载正文，保留文件；重复卸载无副作用 |

静态配置负责声明 Agent 可用的技能来源，默认读取 `.vibe/skills.json`；不需要配置就能发现上述目录。额外注册一个技能目录可写：

```json
{
  "discover": true,
  "paths": ["/path/to/shared-skills"],
  "disabled": []
}
```

`paths` 注册额外的技能根目录或单个 Markdown 文件，相对路径以工作区为基准。优先级为显式 `paths` → 项目目录 → 本机目录；相同文件重复出现时去重，异源同名时显示保留来源与冲突诊断。`disabled` 排除指定名称。设 `discover: false` 可以只使用显式配置的来源。

扫描只在标准技能根及显式注册范围内进行；支持已知技能根之间的链接，拒绝跳到其他目录的符号链接。`VIBE_SKILLS_HOME` 可将本机目录基准改为指定目录，便于便携运行及隔离测试，不修改系统 `HOME`。

可用 `VIBE_SKILLS_CONFIG=examples/skills.json ./run_ink.sh` 选用另一份配置。可提交的技能放仓库 `skills/`，配置可参考 [examples/skills.json](../examples/skills.json)；本地技能与配置仍可放忽略提交的 `.vibe/`。

重启和 `--continue` 会重新构建常驻目录；临时正文不会因历史回执恢复。`/resume` 继续任务时，Agent 根据当前目录重新加载需要的技能。旧配置的 `activate` 仅兼容为启动时临时预装，不代表常驻正文；这类正文同样在首轮结束时清理，推荐配置仅保留注册来源。

正文连同元信息最多 64,000 字符，超出时明确拒绝，避免按截断步骤执行；每个文件读取最多 256 KiB。解析范围见 [技能生命周期](specs/skill-lifecycle.md)。

技能加载不会运行附带脚本或授予额外权限。资源相对路径以 `SKILL.md` 所在目录为基准，实际文件访问仍受工作区范围约束；写文件和执行命令继续经过 `ApprovalGate`。卸载不会撤销已经执行的动作，也不会删除原始对话或模型已经复述的内容。恢复旧会话时，旧版 `load_skill` 的正文仅在模型视图中替换成历史回执，原始记录保留供回看。

## 运行控制

- 运行中纠偏：任务未结束时提交普通文本（Ink 的 `Enter`）会作为 user 消息，在下一模型请求前注入；不打断当前请求或工具，审批与预算照常生效。
- 后续消息：`/followup <内容>` 在当前轮结束后按 FIFO 自动作为下一轮任务继续；队列是界面本地状态，退出后不恢复。
- LLM 流超时 + 瞬时错误重试：429/5xx/网络错误，指数退避；无输出才重试。
- `finish_reason=length` 且无工具调用：自动注入 continuation，最多 `max_continuations` 次。
- 无进展检测：比较实际返回证据，区分新结果、重复结果、连续失败和仅更新计划。默认连续 2 次停滞先提示换方法，再连续 2 次停滞才停止工具，进行一次无工具收尾并保存 `no_progress` 原因。新有效结果会解除纠偏阶段；相同参数但结果变化可以继续。
- 单次 run 总预算：`max_run_tokens`（默认 1M）、`max_run_cost_usd`；自动压缩周期 `compact_interval_tokens`（默认 200k）独立计算。
- 只读工具 timeout 重试；写/执行不重试。
- 写前新鲜度：读过之后文件被改，`edit_file/write_file` 拒绝，要求 re-read。
- vibe prompt：先读后改、优先 `edit_file`、改完跑测试、不谎报测试通过。
- 工作台 `/diff`：看当前 git diff，不发给模型。

可调：`VIBE_LLM_TIMEOUT`、`VIBE_LLM_RETRIES`、`VIBE_LLM_RETRY_BACKOFF`、`VIBE_MAX_CONTINUATIONS`、`VIBE_MAX_NO_PROGRESS`、`VIBE_MAX_RUN_TOKENS`、`VIBE_COMPACT_INTERVAL_TOKENS`、`VIBE_MAX_RUN_COST_USD`、`VIBE_TOOL_RETRIES`。

## 调试

```bash
VIBE_DEBUG=1 ./run_ink.sh --workspace /path/to/repo
# 日志：.vibe/debug/provider.jsonl
```

- `{"type":"request","tools":N}`：`N=0` 说明没挂工具（比如 `--chat-only`）。
- `{"type":"chunk","content":"..."}`：如果 content 里出现 `<tool_call>`，说明模型走的是**文本工具协议**，不是 OpenAI 的 `tool_calls`。
- `{"type":"calls","count":N}`：结构化工具调用数。

**模型输出 `<tool_call>` 文本时**：先确认没开 `--chat-only`；现在 Agent 会自动解析并执行。流式过程中可能短暂看到 tag，结束后会执行工具；这是 fallback 的已知瑕疵。
