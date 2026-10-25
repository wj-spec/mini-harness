# 执行隔离评估

状态：评估（2026-10-25）。当前边界是**审批 + 危险命令黑名单 + 超时 + 进程组回收**，不是操作系统级沙箱。本文记录现状、可绕过点、选项与验收口径，供后续按真实风险决定实现顺序。

## 当前机制

| 机制 | 位置 | 覆盖 |
|---|---|---|
| 写工具审批 | `app/policy.py` `ApprovalGate` | `write_file` / `edit_file` / `bash`；无处理器时 fail closed；范围 once / session / workspace；会话级撤销与审计 |
| 危险命令黑名单 | `app/sandbox.py` `DANGEROUS_PATTERNS` | 12 条正则：`rm -rf /`、`mkfs`、`dd if=`、fork bomb、关机、`sudo`、`chmod 777 /`、force push、hard reset、`curl\|sh`、`wget\|sh` |
| 命令执行 | `app/sandbox.py` `run_command` | `/bin/bash -lc`，cwd=工作区，`start_new_session=True`，超时 kill 进程组，输出截断 |
| 运行上限 | `app/config.py` | 单命令默认 30s；run 级 `max_run_seconds` 600s、token/费用上限 |
| 文件工具边界 | `app/tools.py` | `read_file` / `grep` / `write_file` 解析后校验在工作区内；恢复会话强制重读 |

## 已知可绕过点（按风险排序）

1. **bash 写/删工作区外**：审批通过后 bash 可 `echo > ~/x`、`rm ~/file`；黑名单只挡少数模式，普通路径操作不拦。
2. **bash 读工作区外**：`cat ~/.ssh/id_rsa`、`cat /etc/passwd` 不受文件工具边界限制；内容可能进入上下文、`.vibe/` 会话与评测报告。
3. **正则黑名单易绕过**：变量拼接（`rm -rf "$HOME"`）、`find -delete`、`python -c`、base64 管道等均可绕过；黑名单只能当护栏，不能当边界。
4. **网络外发**：除 `curl|sh` 外，任意 `curl`/`wget`/`nc` 可外发数据。
5. **审批范围副作用**：workspace 级授权让后续同类调用不再询问，风险窗口大；审计只记录不阻断。
6. **子进程逃逸**：`start_new_session` + killpg 覆盖常规子进程，但自行 `setsid`/daemon 化的进程可能逃出回收。

## 选项

| 选项 | 做法 | 成本 | 收益 | 风险 |
|---|---|---|---|---|
| A 收紧审批语义 | bash 不提供 workspace 永久授权，只保留 once/session 精确规则 | 低 | 缩小风险窗口，不改变执行能力 | 便利性下降，频繁确认 |
| B 黑名单护栏增强 | 增加"写/删指向工作区外的明显命令"模式（`rm/mv/tee/cp` + 绝对或 `~` 路径） | 低 | 提高误操作门槛 | 仍可绕过，不能当边界；误报需维护 |
| C 环境收敛 | 子进程 `HOME`/`TMPDIR` 指向工作区内（如 `workspace/.home`） | 中 | 降低误写用户目录概率 | 可能破坏 npm/uv 缓存与工具链；需实测 |
| D OS 级沙箱 | macOS `sandbox-exec` 模板：只允许写工作区 + 临时目录，网络按需开关；或 Linux bubblewrap/firejail；或复用评测 Docker | 高 | 唯一不依赖正则的边界 | 平台差异、维护成本；沙箱内工具链兼容性需验证 |

## 建议顺序与验收

1. **先做 B + A**（护栏与授权语义，低成本）：新增绕过型回归（`rm ~/x`、`echo > ~/x` 被拒或需显式确认）；workspace 授权对 bash 的可用性由用户决定。
2. **再做 C 的实测**（在副本里跑 `npm --prefix tui test`、`uv`、`git`，确认环境变量收敛不破坏工具链）。
3. **D 作为可选开关**（`--sandbox`/`VIBE_SANDBOX=1`，默认关闭）：验收标准——工作区外写失败、读敏感路径按配置拒绝、网络默认拒绝、正常编码任务回归通过；失败时明确报"沙箱拒绝"而不是模型错误。
4. 沙箱开启后仍保留审批与审计；沙箱不是审批的替代，而是兜底。

## 不在本轮范围

- 多用户/远程部署的鉴权与资源隔离；
- Windows 支持；
- 容器内嵌套沙箱。

## 参考

- 风险背景见 [roadmap](../roadmap.md) 与 [README](../../README.md)「核心设计」。
- 评测侧：本地基准只验证任务与证据链，不测试真实危险命令。
