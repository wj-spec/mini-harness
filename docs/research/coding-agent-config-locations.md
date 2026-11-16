# Coding Agent 模型配置位置调查

调查日期：2026-11-16。范围：官方现行文档与 mini-harness 启动代码；没有读取真实 API Key 或认证文件，没有修改启动配置或实现。

## 结论

三家工具都提供独立于安装目录的用户级配置，并允许项目覆盖。模型偏好与登录凭据有不同的存储职责。mini-harness 当前将模型配置默认保存到安装仓库 `.env`，不适合作为长期的全局命令配置位置。截图还暴露了启动加载与配置来源展示不一致的问题，不能只改保存路径。

## 官方事实

| 工具 | 用户级配置 | 项目级配置 | 凭据 |
| --- | --- | --- | --- |
| OpenCode | `~/.config/opencode/opencode.json(c)` | 项目 `opencode.json(c)` 或 `.opencode/opencode.json(c)` | 当前 V2 存服务器 SQLite 数据库，本地通常是 `~/.local/share/opencode/opencode.db`；旧 `auth.json` 是迁移来源 |
| Claude Code | `~/.claude/settings.json` | `.claude/settings.json`（共享）及 `.claude/settings.local.json`（个人项目覆盖） | macOS 默认 Keychain；写入失败时登录可回退 `~/.claude/.credentials.json`；Linux 使用该文件 |
| Codex CLI | `~/.codex/config.toml` | 受信任项目的 `.codex/config.toml` | `~/.codex/auth.json` 或系统凭据库，由 `cli_auth_credentials_store` 控制 |

OpenCode 的模型配置使用 `provider/model` 形式。普通项目配置覆盖用户默认值；系统管理配置优先于它们。它会向上查找配置，直接文件与 `.opencode` 文件的合并顺序不同，不应照搬为“只看当前目录”。来源：[Config](https://opencode.ai/docs/config/)。

OpenCode 当前官方文档明确区分 V2 与旧版凭据存储。可以用 `opencode debug paths db` 查询实际数据库路径；`XDG_DATA_HOME`、发行通道和 `OPENCODE_DB` 可影响位置。凭据通过 `/connect` 或 `auth` 命令管理。同一 integration 的已保存账户优先于环境连接。来源：[Providers](https://opencode.ai/docs/cli/providers/)。

Claude Code 的普通设置优先级为：管理设置 → 命令行 `--settings` → 项目个人 → 项目共享 → 用户。环境变量不是统一的一层，按具体字段解释；例如 `ANTHROPIC_MODEL` 优先于文件的 `model`，`--model` 又优先于二者。来源：[Settings files and precedence](https://code.claude.com/docs/en/settings)。

Claude 的 settings 文件不是专用凭据文件。认证可以来自登录、`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`apiKeyHelper` 或云平台。macOS 回退登录文件权限是 `0600`；创建 API Key 的 Console 登录仍要求 Keychain 可写。`CLAUDE_CONFIG_DIR` 会改变凭据目录及对应 Keychain 条目。来源：[Authentication](https://code.claude.com/docs/en/authentication)。

Codex 用户配置设置默认模型和 provider。配置优先级为：CLI → 受信任项目配置（离工作目录近者优先）→ 所选 profile → 用户配置 → 云端管理默认配置 → 系统配置 → 内建默认值；强制 requirements 是另一层约束。来源：[Config basics](https://developers.openai.com/codex/config-basic/)（目前重定向至官方 ChatGPT Learn）。

Codex 的凭据存储可选 `file`、`keyring`、`auto` 或仅进程内存 `ephemeral`。自定义 provider 可通过 `env_key` 指定环境变量；`requires_openai_auth=true` 时使用 OpenAI 认证并忽略 `env_key`。来源：[Authentication](https://developers.openai.com/codex/auth/)（目前重定向至官方 ChatGPT Learn）。查询前已检查本机 npm 安装的 Codex README，该文件仅链接上述官方文档，未提供完整配置合同。

## 本项目代码事实与截图解释

- `app/runtime/model_settings.py:44`：默认保存路径是安装根目录 `.env`，可由显式 path 或 `VIBE_CONFIG_FILE` 改写。`app/runtime/bootstrap.py:103` 使用同样回退位置。
- `scripts/start.py:210`：正式启动流程将安装目录 `.env` 加载进环境；`:265` 将相同路径作为 `VIBE_CONFIG_FILE` 传给后端。
- `run_ink.sh` 和 `run_vibe.sh` 不加载 `.env`；`app/config.py` 的 `Config.from_env()` 读取进程环境。
- 当前快捷函数直接调用 `run_ink.sh --workspace "$PWD"`，因此跳过了 `start.sh` 的配置加载步骤。工作区正确并不代表模型配置已加载。
- `ModelSettings.sources()` 独立读取磁盘 `.env` 判断来源，`overview()` 独立读取已保存模型，而当前生效值来自 `self.config`。因此“来源 .env / 文件中 deepseek-flash / 当前 deepseek-chat / Key 尚未配置”的组合在代码上可能出现。截图不能证明磁盘中没有 Key，也不能证明 Key 无效。

这些结论基于代码和用户提供的截图；没有核验真实服务连接。

## 针对 mini-harness 的建议（尚未实施）

1. 用户级默认配置使用 `~/.config/mini-harness/config.toml`，支持 `XDG_CONFIG_HOME`。从任何目录运行 `mini-harness` 都复用这份模型配置。
2. 项目只覆盖非敏感选项，例如模型或已命名 provider profile；可用 `.mini-harness/config.toml`。会话、日志、运行产物仍留在工作区 `.vibe/`。
3. API Key 使用用户级专用凭据存储，优先系统凭据库；若先做文件方案，使用独立用户文件并限制为 `0600`。凭据应绑定 provider 与 endpoint，项目覆盖 `base_url` 时不得自动将原全局 Key 发往新地址。
4. 明确定义普通模型选项的优先级，例如 CLI → 显式环境变量 → 项目配置 → 用户配置 → 默认值。不要将此规则泛化到审批策略或所有认证机制。
5. 统一命令入口、启动加载、设置保存和来源说明，来源必须记录实际生效的解析结果。界面默认显示“用户配置，所有项目通用”，项目覆盖存在时再说明覆盖来源；不把安装仓库路径作为普通用户的保存位置。
6. 旧安装 `.env` 仅从已知安装位置迁移；显示待迁移项和目标，经既有 ApprovalGate 写入。保留旧文件，不自动删除，不扫描或导入当前工作区任意 `.env`。
7. 实施时先补测试：跨目录复用配置、保存后重启一致、显式覆盖与来源、旧配置迁移、缺失/损坏配置、endpoint 改变不复用凭据。优先修复快捷命令绕过配置加载的问题，再统一存储位置。

这轮只形成调查结果，没有改动 `~/.zshrc`、真实配置、凭据或项目运行行为。

## 实施跟进

2026-11-16：本轮调查后的实现已落地，现行行为以[配置指南](../CONFIGURATION.md#用户配置与项目覆盖)为准。旧安装 `.env` 采用低优先级兼容读取，已有值在用户配置覆盖后让位；不自动删除或整份搬迁。凭据采用独立 `0600` 文件，尚未接入系统 Keychain。
