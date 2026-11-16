# 配置指南

首次使用运行 `./start.sh` 即可，缺少配置时会直接打开 Ink 首次配置页。默认 DeepSeek 通常只需填写 API Key；自定义服务填写地址、模型名和 Key。保存与检测分别经过审批，选择“进入项目”后才初始化真实模型。想先体验可以运行 `./start.sh --demo`，无需创建 .env。

## 什么时候配置什么

| 需要调整的内容 | 现在可用的入口 | 何时生效 |
| --- | --- | --- |
| 模型、服务地址、Key | 首次配置页；工作台 /model；终端 `./start.sh --configure`；不同模型选“自定义 OpenAI 兼容接口” | 保存供下次启动使用；普通 OpenAI 兼容连接可空闲应用到后续新任务 |
| 临时换模型/地址 | `./start.sh --model NAME --base-url URL` | 本次启动 |
| 模型代理 | 用户配置设置 `trust_env = true`，在 shell 中设置 `HTTPS_PROXY` | 下次启动 |
| 审批档位 | 工作台 `/autonomy` 查看或切换；`/permissions` 查看策略与授权 | 当前进程；持久策略另由策略文件管理 |
| 项目背景、个人偏好 | 工作台 `/project`、`/about` | 后续任务读取 |
| 默认计划模式 | 工作台 `/plan on`、`/plan off`、`/plan status` | 下一条普通任务；运行中任务不改变 |
| 单段步数、时长、token、估算费用 | 工作台 /settings | 保存供重启使用；空闲应用到后续新任务，旧任务续做沿用快照 |
| goal 总预算 | 工作台 /settings 后按 g 切组 | 保存供重启使用；空闲应用到未来新目标，已有目标和后台续做保留预算快照 |
| 上下文、验收超时、沙箱、通知 | 按下文在用户配置 `[environment]` 中添加所需项 | 修改后重启相关进程；后台 worker 需停止并显式重新启动 |

/settings 是统一入口，可按 g 切换编辑单段限制和目标总预算、进入模型和权限面板、查看高级状态；MCP 可按 m 或 /mcp edit 添加、修改与删除服务；按 a 编辑高级项、s 选择当前 / 全部范围，保存后单独重载连接，详情见 [MCP 配置与连接](specs/mcp-lifecycle.md)。/model 支持代理开关，其操作和续做边界见[模型说明](USAGE.md#应用内模型设置)与[设置中心](USAGE.md#应用内任务限制与设置中心)。

## 用户配置与项目覆盖

在任意项目目录运行 `mini-harness`，当前目录就是工作区。模型连接默认在所有项目间复用：

| 内容 | 默认位置 |
| --- | --- |
| 用户配置 | `~/.config/mini-harness/config.toml` |
| 独立凭据 | 同目录 `credentials.json`，权限 `0600`，不要提交 |
| 项目覆盖（可选） | 当前工作区 `.mini-harness/config.toml` |
| 会话与日志 | 当前工作区 `.vibe/` |

支持绝对路径 `XDG_CONFIG_HOME`。`VIBE_CONFIG_FILE` 可显式指定隔离配置；`.toml` 使用上述格式，其他路径兼容旧 dotenv 格式。显式指定时不加载安装目录旧配置。

普通模型选项优先级：**命令行 > shell 环境 > 项目覆盖 > 用户配置 > 旧安装目录 .env（兼容）> 默认值**。这不改变审批策略的优先级。已有安装目录 `.env` 保留为缺省项来源，不读取工作区任意 `.env`，不自动删除或搬动旧文件。首次通过 `/model` 保存即可将模型连接写到新位置；旧高级选项继续兼容，直至在用户配置中覆盖。

用户配置示例（Key 通过首次配置页或 `/model` 保存，不写在 TOML 里）：

~~~toml
model = "deepseek-chat"
provider = "openai"
base_url = "https://api.deepseek.com/v1"
trust_env = false
~~~

项目配置只允许 `model`、`provider`、`base_url`、`trust_env`；不能设置 Key、外部工具或权限。通常只需一行 `model = "项目使用的模型名"`。凭据绑定 provider 和服务地址：项目、环境或 CLI 改变服务地址时，不自动带上旧服务的文件凭据，须显式提供新凭据。CLI/环境显式提供的 Key 由调用者选择。

`credentials.json` 使用配置中的不透明引用关联凭据；保存时先写凭据，最后原子提交配置。提交失败时原配置仍引用原凭据，可能留下未引用条目。配置和凭据都限制为当前用户可读写；凭据以本机文件存储，本轮未接入系统 Keychain。配置与凭据分别经过 ApprovalGate，全部获准后才开始写入；任一拒绝均不写入。审批预览包含两个目标文件，不显示 Key。

`mini-harness`、`start.sh`、`run_ink.sh` 统一经过配置加载。直接运行 `run_vibe.sh` / Python 后端与评测仍只使用进程环境，避免后台任务被磁盘配置悄悄改变。设置页显示实际生效来源与文件已保存模型；保存不等于应用，已运行进程不自动热加载。

## 按需添加高级配置

这些项都不是首次启动必填项。完整默认值、解析位置和范围见[配置契约](CONFIGURATION-CONTRACT.md)，操作说明见[使用指南](USAGE.md)。

| 用途 | 参数 | 使用建议 |
| --- | --- | --- |
| 单次运行与上下文 | VIBE_CONTEXT_WINDOW、VIBE_MAX_STEPS、VIBE_MAX_RUN_SECONDS | 窗口按模型实际容量设置；max_steps=0 表示不限轮数，默认运行时限 1800 秒 |
| 长任务 | VIBE_GOAL_MAX_SEGMENTS、VIBE_GOAL_SECONDS、VIBE_GOAL_MAX_TOKENS、VIBE_GOAL_MAX_COST_USD | 段数用正整数，时长至少 60 秒；只有 token/费用的 0 表示不限 |
| 权限与隔离 | VIBE_AUTONOMY、VIBE_PERMISSIONS、VIBE_SANDBOX、VIBE_SANDBOX_NETWORK、VIBE_SANDBOX_NETWORK_ALLOW | 默认策略 ask；优先用工作台管理权限。沙箱当前是 macOS Seatbelt，网络默认拒绝 |
| 验收与审计 | VIBE_COMPLETION_GUARD、VIBE_WORKSPACE_AUDIT、VIBE_LLM_AUDITOR、VIBE_CONTRACT_BACKCHECK | 保持完成守卫和环境审计默认开启；LLM 审计默认关闭，开启会额外调用模型 |
| 计划验收 | VIBE_ACCEPTANCE_REPAIR_ATTEMPTS、VIBE_ACCEPTANCE_RETRIES、VIBE_ACCEPTANCE_TIMEOUT_SECONDS、VIBE_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS | 修复次数与命令重试分别控制；总超时 0 表示自动计算，非无限等待 |
| 外部工具 | VIBE_MCP_CONFIG、VIBE_MCP_SERVERS | 配置文件或内联 JSON；格式见[外部工具说明](USAGE.md#mcp-外部工具)，前台启动不自动连接，显式连接与调用分别审批 |
| 无人值守 | VIBE_UNATTENDED_REQUIRE_APPROVAL | 用于要求已放宽的 unattended profile 经过 hash 绑定批准；不等于全局自动授权 |
| 通知与日志 | VIBE_NOTIFY_WEBHOOK、VIBE_NOTIFY_WEBHOOK_KINDS、VIBE_NOTIFY_WEBHOOK_TIMEOUT_SECONDS、VIBE_EVENTS_MAX_BYTES | webhook 仅显式设置才外发；events 上限仅告警，不自动删除恢复日志 |
| 只读探索 | VIBE_EXPLORE_MAX_STEPS、VIBE_EXPLORE_MAX_TOKENS、VIBE_EXPLORE_MAX_SECONDS | 仅影响 /explore 或 CLI explore；不改主任务预算 |

例如，只希望单次任务最多运行 15 分钟：

~~~toml
[environment]
VIBE_MAX_RUN_SECONDS = "900"
~~~

启动后环境变量不会从文件实时重读。即便某项在每次 run/验收时读取进程环境，编辑磁盘配置 也不会改变已运行进程。

## 已修正的旧说明

`VIBE_LLM_MAX_OUTPUT_TOKENS` 当前未被 Config.from_env 读取，也未由标准启动入口传给 provider，设置它不会限制输出。示例已移除；不要把它当作费用保护。provider 构造参数支持 max_output_tokens 是底层接口能力，不等于该环境变量已接通。

费用预算依赖模型使用量与单价配置，不能当作服务商账单的精确硬限制。其余预算说明见[使用指南](USAGE.md#单次任务预算与暂停)。

## 开发与诊断

Fake、故障注入、调试日志和委派实验见[开发配置](CONFIGURATION-DEV.md)。`./start.sh --doctor` 只做本地检查，不证明 Key/模型权限有效；验证真实服务需实际请求。
