# vibe — 个人助手工作台

长期目标是一个越用越了解我的工作台：通过持续使用，逐步理解我的目标、偏好和工作方式。
当前建设重点是 vibe coding 能力，同时通过开发这个项目理解 coding agent 的工作原理。

现在的形态是本地 TUI coding agent，已有工具、审批、上下文管理、会话恢复和评估。
TUI 是第一个入口，长期界面随实际使用需求决定；跨任务积累个人上下文仍是后续方向。
`core/` 是可复用的 mini-harness，`app/` 负责模型接入和 TUI 应用。
唯一运行循环是 `app/agent.py` 的 `Agent.run()`；TUI 和 eval 都使用它。
建设顺序与取舍见 [`docs/roadmap.md`](docs/roadmap.md)。

## 怎么跑

### 1) 不联网，先看 TUI

```bash
cd Agent精进/产出/mini-harness
./run_vibe.sh --fake
```

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

> provider 默认 `trust_env=False`：本机代理会让 `127.0.0.1` 返回 502。远程 API 需要代理时设 `VIBE_TRUST_ENV=1`。

Ollama 也可以走同一入口：

```bash
VIBE_BASE_URL=http://localhost:11434/v1 \
VIBE_API_KEY=ollama \
VIBE_MODEL=qwen2.5-coder:7b \
./run_vibe.sh --workspace /path/to/your/repo
```

### 3) 会话 / 成本 / eval

```bash
# 继续 workspace 最新 session；未完成的轮次会自动续跑
./run_vibe.sh --workspace /path/to/repo --continue

# 跑 10 个 eval 任务（先 limit 3 控制成本）
./run_vibe.sh --workspace . --eval --eval-limit 3
```

TUI 里：

- `/cost`：本轮输入/输出 tokens，以及本 session 的 tokens / cost / tools；
- `/resume`：明确继续暂停任务，开启一轮新预算；
- `/permissions`：查看当前工作区授权；`/permissions reset` 撤销本进程授权；
- 写文件或执行命令会弹审批：`y/a` once、`s` session、`n/d` deny。

3 分钟 demo：`./scripts/demo.sh`，脚本见 [`docs/DEMO.md`](docs/DEMO.md)。

### 4) 测试

```bash
./run_tests.sh -q
```

`run_tests.sh` / `run_vibe.sh` 都用项目里的 `.pylibs` 和 DSH 自带 Python，不依赖全局 `pytest`。

## 当前状态

已有模型接入、工具、审批、上下文管理、会话恢复和 eval 基础。最近一次工程验证为 **299 个测试通过**，包含无工具次数上限、结果驱动纠偏与降级、自动压缩继续、工作区授权与撤销、取消、输入及会话恢复；fake CLI 启动、对话和退出通过。这些记录不等于当前版本已通过真实编码任务验收。

当前按 [工作台里程碑 W0–W7](docs/MILESTONES.md) 推进，先完成可复现基线与一次真实小改动。里程碑状态和验收条件统一记录在该文件中。
旧 M1–M8 模块建设情况保留在 [历史计划](docs/history/MILESTONES-M1-M8.md)。

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
- **TaskState**：harness 记录 `goal / plan / done / open / files_changed / tests_run`，作为 `task_state` system 消息注入；不进 stable prefix hash。
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
- 超时用进程组 kill；输出只保留最后 10k 字符。
- 危险命令黑名单：`rm -rf /`、`mkfs`、`dd if=`、fork bomb、`sudo`、`git push --force`、`git reset --hard`、`curl|sh` 等。
- 运行预算：任务累计 tokens、费用与 `max_run_seconds=600`（排除人工审批等待）仍生效。工具次数只用于统计；无进展通过结果检测、纠偏和降级处理。命令执行超时与模型超时仍独立生效。
- **这不是沙箱**：命令仍以你的用户权限运行，`cwd` 只是起点，命令可以 `cd` 到别处。审批 + 黑名单 + 超时是当前边界；VM/seatbelt/landlock 是后续。

可调：`VIBE_MAX_RUN_SECONDS`。旧 `VIBE_MAX_TOOL_CALLS`、`VIBE_MAX_REPEATS` 已不再读取。

## MCP 外部工具

`app/mcp.py` 是 stdio 版 MCP 客户端（只用标准库、换行分隔的 JSON-RPC 2.0）。启动时按配置拉起外部 server，`initialize` → `tools/list`，把远端工具注册成 `mcp_<server>_<tool>` 交给 Agent；退出时关掉所有子进程。

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
      "risk": "exec"        // exec 需审批；可设 read
    }
  }
}
```

```bash
VIBE_MCP_CONFIG=/path/to/mcp.json ./run_vibe.sh   # 文件优先
VIBE_MCP_SERVERS='{"fs":{"command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","."]}}' ./run_vibe.sh
```

- 配置错误直接报错退出；单个 server 起不来只 warning，其余照常加载（`mcp tools: N from M server(s)` 打到 stderr）。
- 远端工具默认 `risk=exec`，走 `ApprovalGate`，工具名是审批粒度；`p` 不覆盖 MCP 工具。
- 失败语义：`isError=true` → `McpToolError`，回灌模型作为工具错误；进程退出 / 超时 / 写管道失败会把该 server 标记为不可用，不重试。
- 输出只保留 20k 字符，server stderr 保留最后 50 行用于报错诊断。

## Session 与恢复

- 每次启动新建 `.vibe/sessions/<id>/`：`events.jsonl`、`state.json`、`meta.json`、`audit.jsonl`。
- **Agent 现在保留 history**：TUI 多轮真正把上文带给模型（之前每轮都是 stateless，这是个 bug）。
- `--continue` 加载 workspace 中最近有对话的 session，跳过启动后未发消息的空会话，并显示最近消息；明确停止的 run 等待 `/resume`，没有终态记录的中断轮次保留自动恢复流程。
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

## 最近一次 eval

2026-10-20，`deepseek-flash`：**10/10**，每个任务 3.8k–10.3k tokens，3–7 步；报告里 cost 为 0 是因为当时没配价格。

## Agent Loop 硬化 v2（vibe coding）

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

## 目录

```
core/        # 可复用组件：models / budget / context / state / summarize
app/         # TUI product：providers / tools / agent / tui
evals/       # tasks.json（10 个 eval 任务）
examples/    # demo-repo
scripts/     # demo.sh
tests/       # 镜像 core 和 app
docs/        # VIBE-PROTOCOL / MILESTONES / COMPARISON / DEMO / STRUCTURE / specs
AGENTS.md    # 项目规则（自举：vibe 会读它）
archive/     # 历史草稿，不被 import
```

- 文件规划与依赖规则：[`docs/STRUCTURE.md`](docs/STRUCTURE.md)
- 协作规则：[`docs/VIBE-PROTOCOL.md`](docs/VIBE-PROTOCOL.md)
- 里程碑：[`docs/MILESTONES.md`](docs/MILESTONES.md)
- 和 opencode / Codex 的对照：[`docs/COMPARISON.md`](docs/COMPARISON.md)
- 3 分钟 demo：[`docs/DEMO.md`](docs/DEMO.md)
- 建设方向与阶段：[`docs/roadmap.md`](docs/roadmap.md)

## 规则

- `core/` 不 import `app/`，也不 import `textual` / `httpx`。
- 一个文件一个职责；不建空占位。
- 每个新模块配测试：成功 + 一个失败路径。
- 运行时产物（offload / backups / sessions）放 `.vibe/`，已 gitignore。
