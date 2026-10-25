# apply_patch 多 hunk 编辑工具

日期：2026-10-25。状态：工程实现与回归完成；真实编码任务中的使用效果待验收。

## 背景与目标

`edit_file` 一次只能替换一个唯一子串；跨文件、多 hunk 的任务需要多次调用，步数与审批次数都高，且大范围改动容易在精确匹配上失败。新增 `apply_patch`：模型提供 unified diff，一次性应用到工作区。

`risk=write`，**仍经过 ApprovalGate**；审批预览展示文件列表、增删行数与 diff 原文，批准后才写盘。写文件/命令的审批边界不变。

## 行为

- 输入：`patch`（git diff 风格的 unified diff 文本），最多 200k 字符、20 个文件；
- 支持：修改已有文件、多 hunk、多文件；新建文件（`--- /dev/null`）；删除文件（`+++ /dev/null`）；
- 拒绝：重命名、二进制 patch、仅 mode 变更、绝对路径、`..` 逃逸、`.git` 路径、符号链接目标；
- 应用：
  - 先完整解析并校验所有 hunk 行数，再读取/校验所有目标文件，最后才写盘；
  - hunk 上下文必须精确匹配；允许行号偏移（在 ±200 行内查找唯一匹配），找不到或目标重叠即报错，不猜测；
  - 修改已有文件前校验读取哈希（`_assert_fresh`），写前再次确认内容未变化；
  - 写盘使用既有 `_backup` + `_atomic_write`，更新读取哈希；
- 返回值：`applied patch: N file(s)` + 每个文件的 `created/modified/deleted path +A -D`。

## 安全边界

- 纯 Python 文本处理，不经 shell；
- 目标路径经 `ToolRegistry._resolve` 解析并限制在工作区内；拒绝 `.git` 与 symlink；
- patch 预览阶段即解析校验，非法 patch 在审批前报错；
- 多文件写盘不是事务：先全部校验，但写盘阶段若后一个文件失败，前面文件不会回滚；已通过 `_backup` 保留备份，需人工核对。此限制写入文档。

## 测试与证据

`tests/test_patch_tool.py`（9 项）：

- 多 hunk 修改、多文件、创建、删除；
- 行号偏移容忍、上下文不匹配拒绝且文件不变；
- 路径逃逸 / `.git` / 重命名 / 二进制 / 行数不匹配拒绝；
- 预览含文件列表与 `write` 风险标记；
- 新鲜度守卫与“新增行以 +++ 开头”的解析歧义。

`tests/test_state.py` 增加 apply_patch 文件跟踪；`tests/test_agent.py` 覆盖系统提示中的工具说明。全量 `./run_tests.sh -q` 退出 0（收集 913 项）；TUI 全量 300 项、299 通过（唯一失败为沙箱无 PTY 的既有项）。

## 已知限制与后续

- 不支持二进制文件、重命名、mode 变更与 `\ No newline at end of file` 的精确语义（按普通行处理）；
- 跨文件写盘非事务，失败时需依据 `.vibe/backups` 人工恢复；
- 后续可考虑：从 `git diff` 直接生成 patch、按文件回滚、与侧栏信号联动（已完成侧栏信号从 patch 头提取路径）。
