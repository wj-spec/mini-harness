# Design

## 启动状态机

```
./start.sh
  ├─ --configure / --install / --doctor / --acceptance / --fake / --demo：沿用现有分支
  ├─ 非交互且缺必要配置：exit 2，给出 ./start.sh（交互）/ --configure / --demo
  └─ 其余：准备依赖后启动 Ink（不再进入终端向导）

Ink → python -m app --stdio
  ├─ 模型配置可用：现有 Agent 运行态（ready 含 run/tasks/...）
  └─ 缺配置/占位 Key：仅配置状态（ready 含 config-bootstrap，无 run）
        ├─ command model list：概览（来源、路径、revision）
        ├─ command model save：ApprovalGate 后原子写 .env
        ├─ command model test：审批网络请求，最小 Hi 调用
        ├─ command bootstrap activate：校验本地配置 → 重新 exec 自身
        └─ run/resume/cancel/steer/其他命令：明确错误，不排队
```

“首次”按必要配置是否完整判断，不使用 onboarding_done。“已配置且本地格式有效”不自动检测、不阻塞发送。

## 仅配置状态（A 批）

- 新模块 `app/runtime/bootstrap.py`，只依赖 Config、ModelSettings、ApprovalGate；不创建 Agent、TaskRuntime、SessionStore，不启动 MCP。
- 信封复用 `v=1`、`id` 去重、1 MiB 帧上限、`approval` 应答与 `shutdown/closed`；错误码 `bootstrap_unavailable`。
- 审批事件沿用 `approval_request`（`id="bootstrap"`，TUI 在 bootstrap 状态下接受该 id）；不降低审批边界。
- `activate` 只做本地校验（.env 可解析、Key 非空且非占位、模型/地址有效），随后用 `sys.orig_argv` 重新 exec 自身；新进程按既有逻辑进入真实运行态并再次发送 `ready`。切换失败留在仅配置状态并返回错误。

## 配置解耦

`ModelSettings` 不再要求 Agent：保存/检测只需要 `ApprovalGate`；`overview/snapshot` 的 provider 在无 Agent 时取 `Config.provider`；`apply` 明确拒绝并要求进入项目后生效。占位 Key 在 `key_present`、候选校验和启动分流中都视为未配置。

## TUI 契约（A 批）

- `ready` 增加可选 `bootstrap` 摘要；能力 `config-bootstrap` 出现时渲染首次配置页，隐藏任务侧栏与技术仪表。
- `model_settings` 在 bootstrap 下 `session_id` 为空串，身份核对改为按 workspace 与 bootstrap 断言。
- 首次配置页：服务（默认/自定义）、隐藏 Key、地址/模型（可展开）、保存并检测、仅保存、进入项目、退出；审批用现有按钮与 `y/n`。
- 进入项目收到 `command_result(bootstrap)` 后显示“正在初始化项目”，等待新的 `ready`；新 `ready` 无 `config-bootstrap` 即进入工作台首页。

## 不做

B–E 之外的页面重设计；不新增 Web/桌面入口；不改权限引擎、任务快照、后台 worker、eval 合同；不自动安装 MCP；不在 bootstrap 创建会话或伪造任务记录。
