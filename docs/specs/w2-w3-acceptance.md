# W2/W3 真实验收：掌控运行过程与隔天续做

日期：2026-10-23。状态：验收环境、步骤与取证脚本已准备；待真实 Ink 会话执行。

## 环境准备

```bash
cd /Users/endless/Desktop/self_project/mini-harness
.venv/bin/python3 scripts/w2w3_setup.py
```

生成 `.vibe/w2w3/workspace`：`examples/demo-repo` 副本（`calc.py` 的 `add` 故意写错，`test_calc.py` 失败）+ 慢测试 `slow_test.py`（约 20 秒，在默认 30 秒工具超时内失败）+ 长命令 `slow_command.py`（120 秒，用于命令停止边界）+ 独立 git 仓库。每次重置：

```bash
git -C .vibe/w2w3/workspace checkout -- .
rm -rf .vibe/w2w3/workspace/.vibe/sessions   # 仅在需要全新会话时
```

打开 Ink：

```bash
./run_ink.sh --workspace .vibe/w2w3/workspace          # 新会话
./run_ink.sh --workspace .vibe/w2w3/workspace --continue  # 恢复最近会话
```

每完成一个验收点运行取证（标签自取，证据写到 `.vibe/w2w3/evidence/`）：

```bash
.venv/bin/python3 scripts/w2w3_evidence.py --label w2-steer
```

取证内容包括：git head/status/diff、文件哈希、`slow_test.py` / `slow_command.py` 残留进程、最近 `run_end`（状态/步数/用量）、`steering_applied`、`command_result`（命令/状态/退出码）、审批记录、未知/未执行工具消息。只读采集，不修改会话。

## 执行记录

### W2-A 已通过（2026-10-23）

- session `20261023-171648-357460` / run `afe6a47b`；
- `steering_applied` 在 step 4，纠偏消息已持久化；
- 8 步、81,933 tokens、11 次工具调用、约 60 秒，`done`；
- diff 仅 `calc.py`，测试文件哈希不变；审批 5 条 workspace_allow；无残留进程；
- 证据 `.vibe/w2w3/evidence/w2-steer-verified-20261023-172000.json`。

### W2-B 三种停止边界已通过（2026-10-23）

- 模型流式中断：`w2-stop-model-20261023-174414.json`（`finish_reason=null`、正文截断、0 工具）；
- 编辑审批取消：`w2-stop-approval-20261023-174458.json`（审计 deny、`not executed`、文件不变）；
- 命令运行取消：`w2-stop-command-20261023-174610.json`（`exit_code=-9`、进程组回收、无残留）。
- 超时进程回收：`w2-stop-timeout-20261023-181452.json`（30s timed_out、exit -9、进程组回收、无残留、文件不变）；
- 取消后已写修改：`w2-cancel-after-write-20261023-181924.json`（编辑已写入后 bash 审批取消；后续任务如实报告 `M calc.py`，未撤销）；
- 取消阶段可见性：用户反馈只看到“已取消”；已增加三阶段 notice 并在真实会话确认可见（截图 `.vibe/w2w3/evidence/w2-approval-notices.png`），W2 五项通过。

## W2：掌控运行过程

### 推荐：每个边界用引导脚本（独立工作区，自动取证）

```bash
.venv/bin/python3 scripts/w2w3_run.py model     # 模型等待取消
.venv/bin/python3 scripts/w2w3_run.py approval  # 编辑审批等待取消
.venv/bin/python3 scripts/w2w3_run.py command   # 命令运行中取消
```

脚本会新建**该边界专用的隔离工作区**（`workspace-model/approval/command`）、打印要发送的提示词与按键步骤，按 Enter 打开 Ink；你按 `Ctrl+Q` 退出后自动调用取证脚本。这样不同边界不会混进同一个会话，也不需要手动 reset。

下面的手动步骤保留，用于复查或特殊情况。

### W2-A 运行中纠偏

1. 新会话，输入：

   ```text
   先运行 python3 slow_test.py 看它如何失败，再修 calc.py 的 add。要求保持 add(a, b) 公开签名不变，不要改 test_calc.py。
   ```

2. 等到 `slow_test.py` 正在运行（约 5–10 秒后，测试还在 sleep），在输入框输入并回车：

   ```text
   补充约束：只改 calc.py 的内部实现；修完用中文说明根因。不要修改任何测试文件。
   ```

3. 预期：先提示“纠偏已提交”，随后“已应用纠偏”；后续模型请求与工具动作遵守新约束；`test_calc.py` 未被修改；最终 diff 只改 `calc.py`。
4. 取证：`--label w2-steer`；核对 `steering` 事件与 `commands`。

**验收项**：运行中可以提出纠偏，后续模型输入和工具动作采用新约束。

### W2-B 三种停止边界

每个边界用**新会话**（保留同一工作区），分别记录。

| 边界 | 操作 | 预期 |
|---|---|---|
| 模型等待 | 新会话，发送“用至少 5000 字解释 ReAct，不要用工具”；看到第一段文字后 1–2 秒内按 `Ctrl+C` | 状态从“正在停止”到“已取消”；run_end 早于/打断 llm_end；没有工具调用、文件哈希不变 |
| 审批等待 | 新会话，发送“修复 calc.py 的 add 并运行测试”；出现编辑审批时直接 `Ctrl+C`（不要按 y/s/p） | 任务取消；`calc.py` 哈希不变；审批审计无 allow |
| 命令运行 | 新会话，发送“运行 python3 slow_command.py 并报告结果”；允许执行；约 3 秒后 `Ctrl+C` | 任务取消；`pgrep -f slow_command.py` 无残留；`test_calc.py` 仍失败；取消后不再有新的模型/工具请求 |

**停止后核对**：已有写入是否保留、命令是完成/终止/未知、界面不把“取消”写成“撤销”。用 `git -C .vibe/w2w3/workspace diff` 与证据 JSON 核对。

**验收项**：可观测边界停止不发起新请求/工具；区分正在停止与已停止；三种状态都验证；超时后核对进程与文件；停止交付能核对已发生修改。

## W3：隔天继续同一个任务

### 执行记录（2026-10-23）

- 读中断 + 恢复：`w3-interrupt-read-20261023-200446.json`、`w3-resume-20261023-200623.json`；`/resume` 先核对未知命令，再重跑测试。
- 外部改动：`w3-external-20261023-200844.json`、`w3-external-resume-20261023-201015.json`；识别 `# external change` 后保留并修复。
- 新旧任务：`w3-newtask-20261023-200751.json`；新会话目标为 README，无旧目标。
- 正常完成与恢复：`w3-normal-20261023-201057.json`、`w3-normal-resume-20261023-201210.json`；总结目标、改动、验证与未提交项。
- 上下文压缩：`w3-compaction-20261023-204314.json`；11 次 `history_compacted`，`summary_layers=2`、offload 非空，压缩后总结准确；W3 六项全部通过。


### 推荐：逐步引导脚本（每条独立取证；按顺序执行）

```bash
.venv/bin/python3 scripts/w2w3_run.py w3-interrupt-read    # 读工具附近中断
.venv/bin/python3 scripts/w2w3_run.py w3-resume            # --continue 恢复：先核对再 /resume
.venv/bin/python3 scripts/w2w3_run.py w3-newtask           # 新任务不沿用旧目标
.venv/bin/python3 scripts/w2w3_run.py w3-external          # 读中断后脚本外部修改 calc.py
.venv/bin/python3 scripts/w2w3_run.py w3-external-resume   # 恢复时先核对差异
.venv/bin/python3 scripts/w2w3_run.py w3-normal            # 第三次真实任务：正常修复
.venv/bin/python3 scripts/w2w3_run.py w3-normal-resume     # 正常退出后恢复核对
.venv/bin/python3 scripts/w2w3_run.py w3-compaction        # 低压缩阈值下的长任务总结
```

每条结束后自动取证；`w3-external` 会在 Ink 退出后向 `calc.py` 追加 `# external change`，模拟恢复前的外部改动。三次真实任务记录：读中断→恢复续做、外部改动→恢复核对、正常完成→恢复核对。

下面的手动步骤保留用于复查。

### W3-1 三个中断点（同一工作区，可分别用新会话）

1. **读工具附近**：新会话，发送“先读 calc.py 和 test_calc.py，解释问题，先不要改”；工具返回前/后立即 `Ctrl+C`。
2. **写工具附近**：新会话，发送“修复 calc.py 的 add”；在编辑审批弹窗按 `Ctrl+C`。
3. **命令执行附近**：新会话，发送“修复 calc.py 并运行测试”；允许编辑，在命令审批或运行中 `Ctrl+C`。

每个点取证一份；核对 `run_end`、`command_result`、`unknown/not executed` 工具消息与文件状态。

### W3-2 恢复并续做

1. 关闭 Ink（`Ctrl+Q`）后重新打开：

   ```bash
   ./run_ink.sh --workspace .vibe/w2w3/workspace --continue
   ```

2. 先发“先核对当前文件与上次结果，再继续”，**不要直接说“继续修”**；
3. 预期：Ink 显示原目标、约束、已发生改动、验证结果与未完成项；未知结果先核对；不自动重放可能已生效的写/命令；必要时 `/resume` 显式续做。
4. 取证：`--label w3-resume`。

### W3-3 外部改动后的恢复

1. 在某个中断后（尤其写工具中断），外部修改工作区：

   ```bash
   printf '\n# external change\n' >> .vibe/w2w3/workspace/calc.py
   ```

2. 重新打开 `--continue`，让 Agent 继续；
3. 预期：它重新读取文件/确认差异，不拿过期内容直接写；若写入需先核对。

### W3-4 继续旧任务 vs 新任务

- 不带 `--continue` 打开新会话，发送一个不相关的新任务（如“解释 README 的用途，不要改文件”）；
- 预期：不沿用旧目标/完成标记；`/session` 显示新会话路径；旧会话仍可用 `--continue` 找回。

### W3-5 正常退出恢复

完成一次正常修复（`calc.py` + 验证命令退出 0）后关闭，再用 `--continue` 打开，核对能看到目标、约束、改动与验证结果。

### W3-6 长任务压缩（可延后）

构造长任务触发上下文压缩（多次读取/修改/测试），确认仍能说明原目标、关键约束、未完成项；必要时用 `recall` 取回原文再行动。

### 三次真实小任务记录

最终至少记录三次：一次失败→修正、一次中断→续做、一次正常运行；把发现的问题转成回归用例并更新 MILESTONES。

## 记录模板

每个验收点记录：

| 字段 | 内容 |
|---|---|
| 时间 / 标签 | 例如 2026-10-23 / `w2-steer` |
| 模型与预算 | 实际模型、tokens、步数 |
| 操作步骤 | 发送的文本、按键、审批选择 |
| 观察 | 界面状态、notice、diff、退出码、进程/文件核对 |
| 结论 | 通过 / 未通过 + 原因 |
| 证据 | `.vibe/w2w3/evidence/<label>-<stamp>.json` |

全部通过后，更新 `docs/MILESTONES.md` 的 W2/W3 勾选项，并把发现的问题写成回归测试或新任务。
