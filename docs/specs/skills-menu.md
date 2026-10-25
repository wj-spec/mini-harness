# 技能选择菜单（/skills）

日期：2026-10-25。状态：实现与回归完成；真实交互体验待验收。

## 背景与目标

`/skills` 原来只能把全部技能摘要和路径作为长文本回灌到对话，用户需要手动抄写技能名再执行 `/skills activate NAME`。目标：输入 `/skills` 后出现可滚动菜单，`Tab` 补全完整命令，`Enter` 执行；不改变技能生命周期与权限。

## 数据来源

- 后端 `ToolRegistry.skill_entries()` 返回结构化目录：`{name, summary, active, source}`；
- `ready` 事件和 `command_result(command="skills")` 事件增加可选 `skills` 字段；
- TUI 在 `ViewState.skills` 保存最新目录；旧后端没有该字段时保持静态命令补全。

## 交互

- 输入 `/skills`：每个技能一行；未激活显示 `/skills activate NAME`，已激活显示 `/skills unload NAME`；说明列显示摘要与状态；
- 输入 `/skills activate `：列出全部技能并可按前缀过滤；`/skills unload ` 只列已激活技能；
- `↑↓` 滚动选择，`Tab` 填入完整命令，`Enter` 执行（仍走原有 `command` 请求与后端权限）；
- 菜单复用 `CommandMenu`：最多 5 行、可滚动、无框线视觉；超过宽度按现有规则截断。

## 边界

- 菜单只负责补全/执行命令，不绕过 `skills` 命令参数校验（action/name 白名单）；
- 技能目录来自后端只读发现；刷新仍用 `/skills refresh`；
- 不改变 `list_skills` / `load_skill` / `unload_skill` 工具与模型侧技能生命周期。

## 测试

- `tui/tests/skills-menu.test.ts`：激活/卸载行、前缀过滤、空目录与无关命令；
- `tui/tests/protocol.test.ts`：`ready` / `command_result` 的 `skills` 字段校验；
- `tui/tests/app.test.tsx`：输入 `/skills` 出现菜单、`↑↓` 切换、`Tab` 补全、`Enter` 发送 `skills` 命令与参数；
- `tests/test_skills.py` / `tests/test_stdio.py`：结构化目录与 `ready`/`command_result` 事件。

工程验证：TUI **305 项、304 通过**（唯一失败为沙箱无 PTY 的既有项）；Python `./run_tests.sh -q` 退出 0（收集 915 项）。
