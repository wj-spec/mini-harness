# 交接：首次使用与真实任务入口（A–E）

> 2026-11-14。A–D 完成，E 的自动化部分完成；真实模型/真实终端/真人体验仍需用户按本文执行。
> 不把 Demo、测试替身或自动化证据当作真人验收。

## 1. 已完成

| 批次 | 状态 | 内容 |
| --- | --- | --- |
| A 启动状态 | 完成 | 缺配置直接进 Ink；`app/runtime/bootstrap.py` 仅配置状态（不建 Agent/会话）；保存/检测经审批；进入项目 re-exec 切换真实能力；`ModelSettings` 与 Agent 解耦。 |
| B 首次配置 UI | 完成 | 默认 DeepSeek / 自定义两态、折叠高级字段、字段级错误与焦点、保存并检测、检测中取消、“已保存；检测未通过”、项目确认页、稍后配置页、Demo（独立 fake 后端与测试会话，持续标识）。 |
| C 首页与设置 | 完成 | 首页只显示项目/模型状态与三条可填草稿的示例；无任务时不常驻路径/provider/会话/上下文/token；设置按目的分类；返回恢复草稿/光标/阅读位置。 |
| D MCP 主动连接 | 完成 | 前台启动不自动连接、无开场审批；ready/mcp_state 计数；/mcp 显式连接；审批带范围；拒绝明确保持未连接；验收清单已更新。 |
| E 验收自动化 | 完成 | 全量回归、真实进程首次启动旅程（保存→进入项目→新 ready）、验收矩阵与本文；配置文件损坏进入 Ink 修复页；`--acceptance` 独立标注。 |
| E 真人/真实模型 | **待用户** | 见第 4 节。 |

## 2. 自动化证据（可复跑）

```bash
./run_tests.sh -q          # 2103 collected / 2091 passed / 12 skipped, exit 0
cd tui && npm test         # 850 tests / 848 pass / 2 fail（2 项因本环境无 PTY）
```

- 真实进程旅程：`tests/test_first_run_journey.py`。无配置启动 → bootstrap ready（无 `run`）→ 保存审批 → 进入项目 → 新 ready 含 `run` → shutdown；不联网、不使用真实 Key，也不在进入项目前创建会话。
- 真实 FastMCP 集成：`tui/tests/backend.test.ts`。启动无审批；显式 `mcp_config reload` 后才审批；工具可用；shutdown 退出干净。
- 本环境无 `/dev/pty`，`interactive CLI enters and restores the alternate screen` 与 `sidebar CLI shares its startup reservation ...` 两项既有 PTY 用例失败；与改动前基线一致。
- 验收矩阵逐项证据见 [acceptance.md](acceptance.md)。

## 3. 已知边界（用户可能看到）

- `.env` 语法损坏时进入 Ink 修复页（不保存、不覆盖原文件）；非交互环境仍 exit 2 并给出修复命令。
- Demo 是单向的：退出重开或新会话回到真实配置，不在 Demo 内切回。
- 进入项目后若既有启动路径失败（例如无效 MCP 配置），表现为后端退出，没有自动退回配置页。
- 模型“检测通过”只代表当前进程内这一次最小请求成功；编辑/重新保存会失效。
- 后台 worker、非交互 CLI、eval 的 MCP 合同未改变；普通 `--stdio` 也不再自动连接，这是前台入口合同。

## 4. 请用户执行的真人旅程（E2）

建议在常用终端、用户自己的项目目录、用户选择的真实模型上执行。

### 准备（保留现有配置）

```bash
cp .env .env.handoff-backup     # 如已有 .env；完成后按需恢复
mv .env .env.saved              # 模拟首次使用
./start.sh
```

> `./start.sh` 会读取仓库根目录 `.env`；没有 `.env` 就是新用户路径。若不想移动现有配置，可改用一份干净 clone。

### 步骤与预期

1. **首次进入**：终端出现 `Mini · 陪你完成第一个编码任务`，没有终端问答。
2. **连接模型**：默认 DeepSeek 只需填 API Key；自定义服务展开地址/模型。选“保存并检测”。
   - 保存审批：确认路径/模型/服务，Key 不显示。
   - 检测审批：允许后显示检测结果；失败应显示“已保存；检测未通过”，页面保留。
3. **确认项目**：显示项目名与路径；“进入项目”后出现真实工作台。
4. **首个只读任务**：输入
   `读一下这个仓库的 README 和主要入口，解释它如何运行，并列出建议先看的三个文件。先不要修改代码。`
   - 确认是真实模型回复；无写入审批也应正常完成。
5. **小改动任务**：给一个用户认可的范围明确修改；检查文件/命令审批范围、实际 diff 与验证结果。
6. **失败恢复**：把网络断开或临时填入不可用 Key（`/model` 保存一个错误地址/Key），发送任务；确认错误可理解、没有额外任务自动发出，修正后恢复。
7. **MCP（如项目有配置）**：启动不应有开场审批；首页显示“外部工具 N 个…未连接”；进入 `/mcp` 选择服务连接，出现范围审批；拒绝后回到未连接。
8. **返回与尺寸**：中途把窗口缩到 80×24、32×12 再恢复；确认草稿、焦点、返回入口不丢；打开/关闭设置后草稿与阅读位置保留。

### 完成后请反馈

- 使用的模型与终端（如 `iTerm2 80×24`），不要贴 Key。
- 每一步是否符合预期；哪一步最困惑或最慢。
- 是否出现：重复向导/检测、开场 MCP 审批、状态误导、返回丢内容、审批范围不清。
- 如某步失败，附当时画面文字（Key 打码）和 `./run_tests.sh` 是否通过。

### 恢复原配置

```bash
rm -f .env            # 仅当这是本轮新建的文件
mv .env.saved .env    # 恢复原配置
rm -f .env.handoff-backup
```

## 5. 复核入口

- 实施任务与勾选：[tasks.md](tasks.md)
- 分批实现与测试证据：[verification.md](verification.md)
- 验收矩阵：[acceptance.md](acceptance.md)
- 用户文档：`docs/USAGE.md`、`docs/CONFIGURATION.md`、`docs/WORKBENCH-ACCEPTANCE.md`
- 路线图/里程碑：`docs/roadmap.md`、`docs/MILESTONES.md`
