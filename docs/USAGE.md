# 使用指南

安装、模型配置、命令和运行边界。项目概览见 [README](../README.md)。以下命令在项目根目录执行。

## 怎么跑

首次安装（Python 3.11+，建议 3.12）：

```bash
uv sync --extra test
```

启动和测试脚本优先使用项目 `.venv`，也可以通过 `PYTHON=/path/to/python3` 指定解释器。
`core/` 保持标准库边界；FastMCP、HTTP 和 TUI 依赖只用于 `app/`。

### 1) 不联网，先看 TUI

```bash
./run_vibe.sh --fake
```

### Ink 工作台（迁移中）

需要 Node.js 22+ 与上述 Python 环境；当前固定 Ink 8 / React 19.3，依赖版本见 `tui/package-lock.json`。

```bash
npm --prefix tui ci
./run_ink.sh --fake

# 使用已有 VIBE_* 模型配置，启动一个新会话
./run_ink.sh --workspace /path/to/your/repo

# 恢复最近有对话的会话；只展示进度，输入 /resume 才继续
./run_ink.sh --workspace /path/to/your/repo --continue

# 编译并运行 Node 通信和 Ink 界面测试
npm --prefix tui test
```

`run_ink.sh` 每次先编译 TypeScript，再启动界面；Node 通过 JSON Lines 调用同一套 Python Agent，继续遵循 `PYTHON` 解释器选择与 `VIBE_*` 配置。需要在交互式终端运行，`--help` 可在普通管道中查看。

当前采用全屏布局，上方显示项目、模型与会话，中间阅读对话和工具，下方编辑多行草稿并显示真实用量。退出恢复原终端内容。消息、输入与状态区采用底色深浅和留白分层，普通回复不画外框；失败、审批与选中工具用轻染色强调。

`/theme light` 使用浅色底板，`/theme dark` 使用深色底板，文字与背景配套。默认 `system` 根据终端提供的 `COLORFGBG` 背景提示选择；未提供或无法识别时回退深色，浅色终端可手动切换。低色终端会按能力降级显示，状态仍用文字标明。

| 操作 | 键位 / 命令 |
|---|---|
| 发送 / 换行 | `Enter` / `Ctrl+J`；终端支持时也可 `Shift+Enter` |
| 历史与草稿 | 首末行用 `↑↓` 浏览历史；多行内用上下键移动，粘贴不会自动发送 |
| 回看对话 | 阅读区内滚轮 / 触控板、`PgUp/PgDn` 翻页；`Ctrl+P/N` 或 `Ctrl+↑/↓` 逐行 |
| 阅读焦点 / 返回输入 | `Ctrl+R` 切换；阅读时 `↑↓` 滚动、空格下一页、`Home/End` 到首尾；`Esc` 返回输入 |
| 返回最新 | `Ctrl+End`；回看时新文本不抢位置，滚动条和行号显示当前位置 |
| 工具详情 | `Tab` 选本会话已发生工具，`Ctrl+O` 查看完整参数和输出，`Esc` 返回 |
| 审批 | `y` 单次、`s` 精确规则、`p` 工作区、`n` 拒绝；按后端允许范围显示，粘贴不批准 |
| 取消 / 退出 | `Ctrl+C` 取消当前任务，空闲时退出；`Ctrl+Q` 或 `/quit` 退出并清理 |
| 续做与权限 | `/resume` 显式续做、`/permissions` 查看、`/permissions reset` 撤销 |
| 帮助与信息 | `/help`、`/cost` 会话用量、`/tools` 已注册工具、`/mcp` 服务、`/session` 会话路径 |
| 查看改动 | `/diff` 经审批执行 git diff，全文可翻页 |
| 查看技能 | `/skills`（或 `/skill`）重新扫描 `.vibe/skills` 并列出摘要，不调用模型 |

运行时允许编辑下一条草稿，结束后再发送；阅读、审批和工具详情聚焦时暂停编辑，滚动不会改写草稿。终端支持时可按住 Shift 拖选文字；没有鼠标事件的终端可以用上述键盘操作。工具结果默认摘要，全文在详情页查看；原始消息和结果仍保存在 `.vibe/sessions/`。取消后需核对已发生的副作用。

状态与消息按块展示：等待模型、回复、调用工具、审批、暂停和错误分别标注；宽屏右侧显示当前任务、工具记录和真实预算，窄屏使用紧凑状态条。命令失败、超时、取消、未知结果显示实际状态，长工具名不会遮住结果。界面与验证见 [历史阅读与状态分区](specs/ink-reading-state.md)。

Ink 支持 `--continue`、已有 `VIBE_MCP_*` 配置与本进程权限管理。恢复后不会自动调用模型或重放工具；`/resume` 才开启新一轮预算。MCP 初始化期间也能取消，服务启动与工具调用分别审批。新命令结果显示真实退出码、超时或取消，旧记录没有执行元数据时显示未记录；零退出本身不证明需求验收通过。

默认入口继续保留 Textual，直到 Ink 的开发与实际体验超越 Textual。运行中纠偏、会话选择器与真实模型联合任务仍待后续验收，eval 继续使用 `./run_vibe.sh --eval`。当前补齐范围见 [迁移规格](specs/ink-parity.md)。

### 2) 真模型（OpenAI-compatible）

```bash
export VIBE_API_KEY=sk-...
export VIBE_BASE_URL=https://api.deepseek.com/v1
export VIBE_MODEL=deepseek-chat
./run_vibe.sh --workspace /path/to/your/repo
```

只验证模型，不挂工具：

```bash
./run_vibe.sh --chat-only
```

> provider 默认 `trust_env=False`，不自动读取代理环境变量。远程 API 需要代理时设 `VIBE_TRUST_ENV=1`。

Ollama 也可以走同一入口：

```bash
VIBE_BASE_URL=http://localhost:11434/v1 \
VIBE_API_KEY=ollama \
VIBE_MODEL=qwen2.5-coder:7b \
./run_vibe.sh --workspace /path/to/your/repo
```

### 3) 会话 / 成本 / eval

```bash
# 打开 workspace 最新 session；明确暂停的任务输入 /resume 继续
./run_vibe.sh --workspace /path/to/repo --continue

# 跑 10 个 eval 任务（先 limit 3 控制成本）
./run_vibe.sh --workspace . --eval --eval-limit 3
```

TUI 里：

- `/cost`：本轮输入/输出 tokens，以及本 session 的 tokens / cost / tools；
- `/context`：查看估算分区、内容块来源和本轮快照；Textual 可按 `Ctrl+O`，Ink 可按 `Ctrl+K`，运行中也能打开；
- `/resume`：明确继续暂停任务，开启一轮新预算；
- `/permissions`：查看当前工作区授权；`/permissions reset` 撤销本进程授权；
- 写文件或执行命令会弹审批：`y/a` once、`s` session、`n/d` deny。

3 分钟 demo：`./scripts/demo.sh`，脚本见 [`docs/DEMO.md`](DEMO.md)。

### 4) 测试

```bash
./run_tests.sh -q
```

`run_tests.sh` / `run_vibe.sh` 默认使用项目 `.venv` 中的依赖，不依赖全局 `pytest`；显式指定其他 Python 时保留 `.pylibs` 兼容路径。

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

`/context` 展示当前占用条、四区用量与额度、输出和安全预留、逐步骤变化及块来源。`←/→` 切换快照，`↑/↓` 或滚轮翻阅，`Esc` 返回；Ink 的 `l` 恢复跟随最新。界面最多保留本轮最近 100 张快照，新任务或 `/resume` 开启新一轮；Textual 重开会话时也能查看上轮已有记录。

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
- **TUI**：状态栏显示 `todo done/total`。

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

安装依赖后，Textual 和 Ink 入口都默认向模型提供内置 `web_search`；`--chat-only` 仍不挂工具。使用 [DDGS 9.16.0](https://pypi.org/project/ddgs/9.16.0/)，无需注册或配置搜索 API Key。模型本身仍按原来的配置运行。

例如直接输入：“搜索 Python 官方文档中 asyncio.TaskGroup 的用法，给我来源链接。”模型可调用：

```json
{"query": "site:docs.python.org asyncio TaskGroup", "max_results": 5, "region": "us-en", "timelimit": "y"}
```

- `query` 必填，1–500 字符；`max_results` 默认 5，最多 10；`region` 默认 `us-en`，中文可用 `cn-zh`；`timelimit` 可省略，或用 `d/w/m/y` 限制最近一天/周/月/年，过滤效果取决于上游引擎。
- 调用使用 `risk=network`，先由 `ApprovalGate` 展示搜索词并审批。Textual 与 Ink 都支持单次或同参数精确规则；工作区写入/命令授权不覆盖搜索。
- DDGS 使用 `backend=auto` 聚合搜索，查询可能发往多个第三方搜索引擎。预览会展示完整查询，请勿放入凭据或私密代码。网络需要代理时，可使用库支持的 `DDGS_PROXY=http://127.0.0.1:7890`，与模型的 `VIBE_TRUST_ENV` 分开。
- 返回有界的 JSON：`query/source/notice/count/results`，每项包含 `title/url/snippet`。这只是搜索摘要，不能视为已读取和核实网页全文；外部内容按不可信资料处理，回答应附来源链接。
- 单请求超时 5 秒，整次检索最多 20 秒；取消或超时会终止并回收搜索进程。限流、网络错误和后端无结果异常如实回灌，不自动重试，也不以空列表伪装失败。后端正常返回空列表时显式显示 `count=0`。

DDGS 是第三方网页检索库，并非搜索引擎提供的稳定 API；验证码、网络限制和页面改版均可能使检索失败。它无需付费 API 凭据，但不承诺无限次数或可用性。选型与免费额度比较见 [调研记录](research/web-search-2026-10-21.md)。

## MCP 外部工具

`app/mcp.py` 使用 [FastMCP 4.1.1 Client](https://gofastmcp.com/clients/client) 接入 stdio MCP 服务，由库负责版本协商、消息分发和工具结果解析。TUI 准备好后先审批服务启动，再发现工具并交给 Agent；连接、调用和关闭都在同一事件循环中运行。

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
VIBE_MCP_CONFIG=/path/to/mcp.json ./run_vibe.sh   # 文件优先
VIBE_MCP_SERVERS='{"fs":{"command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","."]}}' ./run_vibe.sh
```

- 支持原有 `servers`、标准 `mcpServers` 外层和直接的 server 字典；目前开放 stdio 配置。
- 配置错误直接报错退出；单个服务被拒绝或启动失败会在 TUI 提示，其余服务照常加载。`--chat-only` 不启动服务。
- 启动命令以 `mcp_start` 经过 `ApprovalGate` 并审计；预览显示命令、cwd 和环境变量名，环境变量值使用摘要区分授权。子进程仅继承 SDK 的基础环境及配置中的 `env`，需要的凭据请显式配置。
- 远端工具默认 `risk=exec`，每次调用单独审批；`s` 授权本会话同工具同参数，`p` 不覆盖 MCP 启动或工具。启动获批不代表工具调用获批。
- 工具名通常为 `mcp_<server>_<tool>`；非法字符、下划线造成的边界歧义、超过 64 字符或命名碰撞时添加固定来源摘要。其他服务被拒绝或启动失败也不会改变这个映射。
- 工具执行错误回灌模型；超时包含发送和等待，并取消本次调用、停止对应进程组。连接不自动重建，工具不自动重试，避免重复执行有副作用的动作。
- 正常退出、启动取消和失败都清理连接；POSIX 下同时清理服务进程组中残留的后代进程。
- `Ctrl+C` 会取消正在进行的 MCP 调用并停止对应服务。已开始的外部操作记录为结果未知，不承诺回滚；后续任务可继续使用内置工具。取消终态在重启后等待 `/resume` 或新指令。
- 结果保留 20k 字符；stderr 日志写入 `.vibe/mcp/`，启动失败展示末尾 4k 字节用于诊断。

## Session 与恢复

- 每次启动新建 `.vibe/sessions/<id>/`：`events.jsonl`、`state.json`、`meta.json`、`audit.jsonl`。
- Agent 保留 history，TUI 多轮对话会将已有上下文带给模型。
- `--continue` 加载 workspace 中最近有对话的 session，跳过空会话，模型读取最近有效压缩快照与后续消息。Ink 仅恢复展示，所有续做都等 `/resume` 或新指令；Textual 对明确停止的 run 等待 `/resume`，没有终态记录的中断轮次保留原自动恢复流程。
- 崩溃后如果 `assistant(tool_calls)` 没有对应 `tool` 结果，恢复时补一条 `ERROR interrupted: tool result unknown after crash`，并记 `recovered_tool` 事件——**不假装成功**。
- 事件日志 append-only；`state.json` 是 TaskState 投影；审批审计和 session 放在同一目录。

试崩溃恢复：

```bash
./run_vibe.sh --workspace /tmp/demo-repo
# 跑到一半 kill -9
./run_vibe.sh --workspace /tmp/demo-repo --continue
```

## Trace / 成本 / Eval

- 每次 run 写 `session/trace.jsonl`：`run_start`、`llm_start`、`llm_end`、`budget_update`、`run_end` 等；停止记录包含原因、预算及已进入执行流程的工具数，预算阻止的工具不计入已执行数量。
- TUI 输入 `/cost`：显示本轮输入/输出，以及本 session 的 runs、tokens、cost、tool calls、最后状态；不会发给模型。
- 价格按 token 计：`VIBE_INPUT_PRICE_PER_1M`、`VIBE_OUTPUT_PRICE_PER_1M`（默认 0）。
- 优先使用 provider 的 `stream_options.include_usage`；不支持可设 `VIBE_STREAM_USAGE=0`，此时按请求与输出估算 token 并明确标注，预算保护仍生效。
- `--eval` 跑 10 个任务，自动审批，每个任务独立 workspace，输出：
  `.vibe/evals/<timestamp>/report.json` + `report.md`，并与上一轮比 `delta`。
- `--eval-limit N` 只跑前 N 个；`--eval-tasks path` 用自己的任务文件。

```bash
# 真实模型跑分（示例价格，按你的 provider 改）
VIBE_INPUT_PRICE_PER_1M=0.27 \
VIBE_OUTPUT_PRICE_PER_1M=1.10 \
./run_vibe.sh --eval --eval-limit 3
```

> eval 是**模型能力跑分**，不是单元测试；`--fake` 只验证 harness 能跑通，不代表模型会做。

## 项目规则（AGENTS.md）

启动时读取项目根 `AGENTS.md`；没有就退回 `.vibe/AGENTS.md`：

- 最多 12k 字符，超出截断；
- 作为 `[PROJECT INSTRUCTIONS]` 追加进 system prompt；
- TUI 启动日志显示 `AGENTS.md loaded`；
- 示例：`examples/demo-repo/AGENTS.md`。

## Skill（可复用操作手册）

把重复做法写进 workspace，agent 按需读取，不用每次贴长提示。

目录（同名时目录版优先）：

```text
.vibe/skills/<name>/SKILL.md
.vibe/skills/<name>.md
```

文件格式：开头几行 `key: value`（`name`、`description`），空行后是正文步骤；正文为空则忽略该文件。

```markdown
description: 给新模块补单元测试

1. 先读 tests/ 里同类模块的测试写法
2. 只测行为，不测实现细节
3. 跑 PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_xxx.py -q
```

渐进式披露：

- 启动时只把 `- name: 一句话描述` 注入 system prompt（`[SKILLS]` 块，每个摘要 ≤600 字符）；
- agent 需要时调用 `list_skills` / `load_skill`，正文上限 8k 字符，超出截断；
- TUI 启动日志显示 `skills: a, b`；随时输入 `/skills` 重新扫描并列出（新增 skill 不用重启）。

> `.vibe/` 是运行时目录，skill 默认不提交；要团队共用就放进仓库并保留 `.vibe/skills/`（自行调整 `.gitignore`）。

## 运行控制

- LLM 流超时 + 瞬时错误重试：429/5xx/网络错误，指数退避；无输出才重试。
- `finish_reason=length` 且无工具调用：自动注入 continuation，最多 `max_continuations` 次。
- 无进展检测：比较实际返回证据，区分新结果、重复结果、连续失败和仅更新计划。默认连续 2 次停滞先提示换方法，再连续 2 次停滞才停止工具，进行一次无工具收尾并保存 `no_progress` 原因。新有效结果会解除纠偏阶段；相同参数但结果变化可以继续。
- 单次 run 总预算：`max_run_tokens`（默认 1M）、`max_run_cost_usd`；自动压缩周期 `compact_interval_tokens`（默认 200k）独立计算。
- 只读工具 timeout 重试；写/执行不重试。
- 写前新鲜度：读过之后文件被改，`edit_file/write_file` 拒绝，要求 re-read。
- vibe prompt：先读后改、优先 `edit_file`、改完跑测试、不谎报测试通过。
- TUI `/diff`：看当前 git diff，不发给模型。

可调：`VIBE_LLM_TIMEOUT`、`VIBE_LLM_RETRIES`、`VIBE_LLM_RETRY_BACKOFF`、`VIBE_MAX_CONTINUATIONS`、`VIBE_MAX_NO_PROGRESS`、`VIBE_MAX_RUN_TOKENS`、`VIBE_COMPACT_INTERVAL_TOKENS`、`VIBE_MAX_RUN_COST_USD`、`VIBE_TOOL_RETRIES`。

## 调试

```bash
VIBE_DEBUG=1 ./run_vibe.sh --workspace /path/to/repo
# 日志：.vibe/debug/provider.jsonl
```

- `{"type":"request","tools":N}`：`N=0` 说明没挂工具（比如 `--chat-only`）。
- `{"type":"chunk","content":"..."}`：如果 content 里出现 `<tool_call>`，说明模型走的是**文本工具协议**，不是 OpenAI 的 `tool_calls`。
- `{"type":"calls","count":N}`：结构化工具调用数。

**模型输出 `<tool_call>` 文本时**：先确认没开 `--chat-only`；现在 Agent 会自动解析并执行。流式过程中可能短暂看到 tag，结束后会执行工具；这是 fallback 的已知瑕疵。
