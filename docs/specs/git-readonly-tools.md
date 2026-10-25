# 只读 Git 检查工具（git_status / git_diff）

日期：2026-10-25。状态：工程实现与回归完成；真实编码任务中的使用效果待验收。

## 背景与目标

Agent 在报告工作区状态（未提交改动、文件是否存在、diff 内容）时，过去只能调用 `bash`，每次都要经过审批；这与用户记录的“频繁审批”摩擦一致，也让自检被不必要地打断。

本切片新增两个**只读**工具：

| 工具 | 作用 |
|---|---|
| `git_status` | 当前分支、ahead/behind、未提交文件（porcelain v2 XY 状态）与每个文件相对 HEAD 的增删行数 |
| `git_diff` | 未提交改动的 unified diff（默认 `HEAD` 基线，含 staged + unstaged；可限定 path 或只看 staged；未跟踪文件用 `--no-index` 展示内容） |

两者 `risk=read`，不经过 `ApprovalGate`；写文件、执行命令、MCP、web 的审批语义不变。

## 行为

- `git_status`：
  - `git status --porcelain=v2 -z --branch --untracked-files=all`；
  - 输出 branch / ahead / behind、文件数、每行 `XY path +N -M`；重命名显示原路径；未跟踪为 `??`；
  - 干净时输出 `working tree clean`；非 Git 仓库明确报错。
- `git_diff(path=None, cached=False)`：
  - 默认 `git diff HEAD`，同时覆盖已暂存与未暂存；`cached=True` 只看 index；`path` 限制到工作区内路径；
  - 未跟踪文件：`git diff --no-index -- /dev/null <path>`；
  - 输出上限 1200 行 / 40,000 字符，超出附截断标记；空 diff 输出 `(no differences)`。

## 安全与边界

- 固定参数数组、`subprocess.run`，**不经过 shell**；
- 清理所有 `GIT_*` 环境变量，设置 `GIT_OPTIONAL_LOCKS=0`、`LC_ALL=C`、`GIT_PAGER=cat`；
- 禁用外部 diff / textconv / fsmonitor：`--no-ext-diff`、`--no-textconv`、`-c core.fsmonitor=false`，避免仓库配置借只读命令执行外部程序；
- `-c core.quotepath=false` 保留中文路径；路径只允许在工作区内（`path outside workspace`）；
- 15s 超时；只读、不联网、不写文件；不替代 `/diff` 的审批语义（`/diff` 仍是用户主动查看入口，只是 Agent 自检不再需要审批）。

## 测试与证据

`tests/test_git_tools.py`（7 项）：

- 分支/状态/增删行数、未跟踪文件、干净树；
- `HEAD` diff 与未跟踪 `--no-index` diff、path 过滤、staged-only；
- 非 Git 仓库与工作区外路径拒绝；
- 大 diff 截断；
- `risk=read`、工具注册与直接 `execute` 不需要审批。

工程验证：

- `./run_tests.sh -q`：全量 Python 回归通过（收集 902 项，新增 7 项）；
- TUI 测试不受影响（未改 TUI）。

## 已知限制与后续

- 只覆盖当前工作区 Git；非 Git 工作区需要回退到 `bash` 或说明不可用；
- 不展示 submodule 内部状态、Git LFS 细节与 blame；
- 后续可用于交付回执的“实际工作区差异”核对（目前回执使用 TaskState 的工具记录），以及减少 `bash` 复核审批；是否让 Agent 在每轮结束自动调用 `git_status` 仍需真实任务收益评估。
