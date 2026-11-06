# 任务模式 P0（chat/act，TaskRecord.mode）

状态：2026-11-06 P0 实现；plan/goal 转换、mode 记忆/切换仍待后续。
设计来源：[10 模式状态机](../design/long-horizon/10-mode-state-machine.md)。

## 已实现

- `app/runtime/modes.py`：`MODE_NAMES=(chat, act, plan, goal)`、`parse_mode`、`kind_for_mode`、`mode_for_kind`；未知 mode fail closed。
- `ModeProfile` 注册表（11 P0 起步）：
  - chat：无工具、`chat_only`、shared_readonly；
  - act：`*` 工具、`default`、shared_readonly；
  - goal：`*` 工具、worktree_write；
  - plan：只读工具 + `plan_only`、plan.review capability；
  - `resolve_profile`/`validate_profile_tools` 校验未知 mode/缺失工具。
- `TaskRuntime`：
  - `run.task` 支持 `kind=run`（chat/act）与 `kind=goal`（goal），`mode` 写入 `TaskRecord`；
  - 未指定 mode 时：goal 默认 `goal`，run 默认 `act`；
  - 未知 mode / 不匹配的 attach mode 拒绝；
  - task payload 增加 `mode`。
- stdio：
  - `ready.capabilities` 在任务运行时开启时增加 `modes`；
  - `mode=chat` 的 run 临时关闭 agent tools（provider 不收到 tools），run 结束后恢复；
  - `mode=act` 保持正常工具路径。
- TUI：
  - `/chat <任务>`、`/act <任务>` 本地命令，发送 `{kind:"run", mode, objective}`；已加入命令补全与说明；
  - 需要后端 `tasks` capability；运行中拒绝切换。
- stdio 启动 run 前解析 mode profile 并校验工具名，写 `mode_profile` 事件；
  `plan` 模式把 agent tools 临时替换为只读子集（`read_file/list_files/grep/git_status/git_diff`），run 结束后恢复。
- 测试：
  - modes：profile 完整性、chat/act/plan 工具与 capability、未知 mode/缺失工具；
  - TaskRuntime：run/chat、未知 mode、attach mode mismatch；
  - stdio：chat 的 provider `seen_tools=[]`、task meta mode；
  - TUI：`/chat` `/act` 提交的 task metadata。

## 未完成

- budget/approval/isolation profile 的实际执行层强制（当前已强制 chat 无工具、plan 只读子集；budget/approval 仍只声明）；
- `/plan`、plan→act 批准转换（12/13）；
- goal 段内 plan mode；
- 入口记忆上次 mode（项目级/会话级）；
- mode 跨段切换策略与 UI；
- `/plan` 及 plan 产物/审批门；
- 非 TUI 入口的显式 mode flag（当前仅 run.task）。
