# 07 v2 + TUI attach 人工验收步骤

日期：2026-11-05。适用基线：本轮 v2 `task_snapshot/approval_list/approval_decide` 与 TUI attach 实现。
自动验收结果：Python **1284 passed / 13 skipped**；TUI **354/355**（当前执行环境无 pty，`cli.test.ts` 的交互 smoke 失败；人工验收必须在**真实 pty 终端**中跑）。

## 0 前置条件

- macOS 或 Linux，真实交互终端（Ghostty/iTerm/Terminal 等，有可用 pty）。
- 项目 `.venv`、Node 22+、git 可用。
- 在项目根目录执行（后文记为 `$PROJECT`）：
  ```bash
  cd "$PROJECT"
  ./run_tests.sh
  cd tui && npm test && cd ..
  ```
- **不要**在 CI/无 pty 的沙箱里做交互步骤；TUI 会提示需要交互终端。
- 验收过程会产生 scratch 目录 `/tmp/vibe-acceptance`，可随时删除。

### 自动化基线（必须先通过）

| 检查 | 期望 |
|---|---|
| `./run_tests.sh` | 1284 passed，13 skipped，退出 0 |
| `cd tui && npm test` | 355 tests：真实终端下应 355 passed；本环境 354 passed / 1 pty 失败 |
| `./run_vibe.sh --tasks` | 输出 `no tasks under …`，退出 0 |

## 1 准备 scratch git 工作区

```bash
export PROJECT=/Users/endless/Desktop/self_project/mini-harness
export WS=/tmp/vibe-acceptance
rm -rf "$WS" && mkdir -p "$WS" && cd "$WS"
git init -q
git config user.email "acceptance@example.com"
git config user.name "Acceptance"
printf ".vibe/\n" > .gitignore
echo "base" > README.md
git add . && git commit -qm "acceptance base"
```

## 2 创建测试 session/task

```bash
cd "$PROJECT"
./.venv/bin/python3 scripts/acceptance_seed_task.py --workspace "$WS"
```

记录输出中的 `session_id`、`task_id`，后文用：

```bash
export SESSION=<session_id>
export TASK=<task_id>
```

期望：

- `<WS>/.vibe/sessions/<SESSION>/tasks/<TASK>/meta.json` 存在，status=`created`；
- 不创建 worktree（除非传 `--with-worktree`）。

## 3 测试场景

### TC-01 基线 TUI 与退出

```bash
cd "$PROJECT"
./run_ink.sh --fake --workspace "$WS" --session "$SESSION"
```

步骤/预期：

1. TUI 正常打开，显示工作台；
2. 启动时若检测到 seeded task，应显示任务提示（`检测到 … 输入 /task 查看；/attach …`）；
3. 输入 `/task`，应列出 `$TASK`（status=created/paused）且不报错；
4. 输入 `/approvals`，应显示“当前没有待审批项”或列表；
5. Ctrl+Q：当前无运行中 run/goal 时应直接退出，退出码 0。

**通过标准**：TUI 可交互、`/task` 能看到 task、Ctrl+Q 正常退出。

### TC-02 worker 运行与 TUI attach（v2 snapshot）

Terminal A：

```bash
cd "$PROJECT"
VIBE_TASK_RUNTIME=1 VIBE_FAKE_DELAY_SECONDS=120 \
  ./run_vibe.sh --worker --fake --workspace "$WS" --session "$SESSION" --task-id "$TASK"
```

Terminal B：

```bash
cd "$PROJECT"
./run_vibe.sh --tasks --workspace "$WS" --fake
```

预期：列出 `$TASK`，worker=`running`；`<WS>/.vibe/worktrees/$TASK/` 存在。

在 worker 完成前（120s 内）打开 TUI：

```bash
./run_ink.sh --fake --workspace "$WS" --session "$SESSION"
```

操作：

1. 启动后应看到 seeded task 提示；
2. 输入 `/attach`：应输出
   `./run_ink.sh --attach-task $TASK --session $SESSION --workspace $WS --fake`；
3. 输入 `/attach now`：当前 TUI 退出并重启到 attach 模式；
4. 重启后应显示 `已 attach：$TASK …` 或等价提示；
5. 输入 `/task` 仍能列出任务；等待 fake 延迟结束，应看到 done/任务完成；
6. 结束后 worker 退出；退出 TUI。

**通过标准**：
- attach 成功且收到 `task_snapshot`（TUI 有提示，或 attach CLI 输出含 `task_snapshot`）；
- TUI 没有协议错误/断连；
- 任务最终 `completed`（`./run_vibe.sh --tasks --workspace "$WS" --fake` 验证）。

### TC-03 审批流（v2 approval_decide）

重新 seed 一个 task（**不要复用 TC-02 的 completed task**）：

```bash
cd "$PROJECT"
./.venv/bin/python3 scripts/acceptance_seed_task.py --workspace "$WS"
# 记录新的 SESSION2/TASK2
```

Terminal A：

```bash
VIBE_TASK_RUNTIME=1 VIBE_FAKE_DEMO=1 VIBE_WORKER_APPROVAL_TIMEOUT=300 \
  ./run_vibe.sh --worker --fake --workspace "$WS" --session "$SESSION2" --task-id "$TASK2"
```

预期：worker 很快进入 `waiting_approval`，任务目录 `approvals.jsonl` 有 `requested` 条目。

Terminal B：

```bash
./run_ink.sh --fake --workspace "$WS" --session "$SESSION2"
```

操作：

1. 输入 `/attach now` 进入 attach；
2. attach 后应显示待审批（write_file `.vibe/fake-demo.txt`）；
3. 按 `y`（允许一次）；
4. 预期 worker 继续执行并完成任务；
5. 退出 TUI。

验证：

```bash
./run_vibe.sh --tasks --workspace "$WS" --fake          # TASK2 completed
ls "$WS/.vibe/worktrees/$TASK2/.vibe/fake-demo.txt"     # worktree 内文件
ls "$WS/.vibe/fake-demo.txt" 2>/dev/null                # 主工作区不应有
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("/tmp/vibe-acceptance/.vibe/sessions/<SESSION2>/tasks/<TASK2>/approvals.jsonl")
print([json.loads(line)["type"] for line in p.read_text().splitlines()])
# 期望 requested + resolved；resolved 的 decision=once
PY
```

**通过标准**：TUI 审批能真正 resolve worker 的 inbox，worker 未超时失败，worktree 有文件，主工作区没有该文件。

> 若 attach 后没有弹出审批面板：先记录屏幕与 `approvals.jsonl`，再用 `/approvals` 检查 pending；这是需要修 UI 回放/审批状态的缺陷，不要直接改数据绕过。

### TC-04 observer 只读（v2 approval_list / observer）

在 worker 仍等待审批或运行期间（另起一个 task 或用 TC-03 worker 未完成前），Terminal C：

```bash
cd "$PROJECT"
./.venv/bin/python3 scripts/acceptance_attach_probe.py \
  --workspace "$WS" --session "$SESSION2" --task-id "$TASK2" \
  --mode observe --request approvals
```

预期输出：

- `FIRST {"v":1,"type":"attach_ok",..., "mode":"observe", "capabilities":[...]}`
- `FRAME {"v":1,"type":"task_snapshot",...}`
- 回放若干 `ready`/history 帧后，`FRAME` 中应出现 `{"type":"approval_list",...}`
- 不应出现 controller 权限错误（`observer_readonly` 仅在发控制帧时出现）。
- 探针默认 `--listen-seconds 2`；回放较大时可加 `--listen-seconds 5` 并用 `grep` 过滤 `attachment/task_snapshot/approval_list`。

验证 observer 不能控制：

```bash
./.venv/bin/python3 scripts/acceptance_attach_probe.py \
  --workspace "$WS" --session "$SESSION2" --task-id "$TASK2" \
  --mode observe --request cancel
```

预期：`FIRST attach_ok`，随后 `FRAME {"type":"error","code":"observer_readonly",...}`。

**通过标准**：observer 能读 snapshot/approvals，但 cancel 被拒绝；worker 不受影响。

### TC-05 controller 接管

保持 TC-03 的 TUI attach（它持有 controller）。Terminal C：

```bash
./.venv/bin/python3 scripts/acceptance_attach_probe.py \
  --workspace "$WS" --session "$SESSION2" --task-id "$TASK2" \
  --mode control --take-control --request snapshot
```

预期：

- probe 的 FIRST 为 `attach_ok`（mode=control）；
- 原 TUI 收到错误提示，包含 `control_taken`；
- probe 退出后控制权释放，原 TUI 不会自动抢回（需要重新 attach）。

**通过标准**：接管明确、无两个 controller 同时审批；原 TUI 有可见提示。

### TC-06 detach 后 worker 继续

准备一个耗时 worker（同 TC-02，`VIBE_FAKE_DELAY_SECONDS=120`，新 seed/task）。

1. TUI `/attach now` 连接；
2. 按 Ctrl+Q 退出 attach TUI（注意选择“停止/等待/取消”相关提示时**不要停止任务**，直接退出 attach 即可；若提示需要确认，选择退出但保留 worker）；
3. 另开终端：

```bash
./run_vibe.sh --tasks --workspace "$WS" --fake
```

预期 worker 仍 `running`；等待延迟结束后 completed。

**通过标准**：attach 断开不等于取消，worker 继续执行。

### TC-07 workspace kill switch

在 worker 运行中（第二个终端）：

```bash
cd "$PROJECT"
./run_vibe.sh --stop --workspace "$WS"
```

预期：

- 输出 JSON，`workers` 包含 `$TASK`，`signalled=true`；
- `<WS>/.vibe/stop` 存在；
- 进程退出；`worker.json` status=`killed`；
- TUI attach 收到错误/断开。

清理停止信号后再继续其它用例：

```bash
rm -f "$WS/.vibe/stop"
```

**通过标准**：worker 被停止、状态明确，不悬挂。

### TC-08 merge CLI + 合并后验证

准备带 worktree 的独立 task：

```bash
cd "$PROJECT"
./.venv/bin/python3 scripts/acceptance_seed_task.py --workspace "$WS" --with-worktree
# 记录 SESSION3/TASK3
```

请求合并：

```bash
./run_vibe.sh --merge-task "$TASK3" --session "$SESSION3" --workspace "$WS" --fake
# 期望输出 diff + "merge approval requested: <APPROVAL_ID>"，退出码 2
```

批准并验证：

```bash
./run_vibe.sh --merge-task "$TASK3" --session "$SESSION3" --workspace "$WS" \
  --approve-merge "$APPROVAL_ID" \
  --merge-verify "test -f ACCEPTANCE_MERGE.txt" --fake
```

预期：

- JSON `merged=true`，`post_merge_verified=true`；
- `<WS>/ACCEPTANCE_MERGE.txt` 存在；
- `git -C "$WS" log --oneline -1` 出现 merge 提交。

**通过标准**：无审批不能合并；批准后主工作区得到文件且合并后验证通过。

### TC-09 非 git 工作区默认拒绝

```bash
export NONGIT=/tmp/vibe-nongit
rm -rf "$NONGIT" && mkdir -p "$NONGIT"
cd "$PROJECT"
./.venv/bin/python3 scripts/acceptance_seed_task.py --workspace "$NONGIT"
# 记录 SESSION_NG/TASK_NG
./run_vibe.sh --worker --fake --workspace "$NONGIT" --session "$SESSION_NG" --task-id "$TASK_NG"
echo $?
```

预期：退出码 3，stderr 包含 `non-git workspace`；不创建 worktree。

**通过标准**：非 git 后台写被明确拒绝（不静默写主工作区）。

## 4 通过/失败记录模板

```
TC-01 [ ] pass [ ] fail  证据：
TC-02 [ ] pass [ ] fail  证据：
TC-03 [ ] pass [ ] fail  证据：
TC-04 [ ] pass [ ] fail  证据：
TC-05 [ ] pass [ ] fail  证据：
TC-06 [ ] pass [ ] fail  证据：
TC-07 [ ] pass [ ] fail  证据：
TC-08 [ ] pass [ ] fail  证据：
TC-09 [ ] pass [ ] fail  证据：
```

失败时请附：

- 终端完整输出（TUI 截图/复制）；
- `./run_vibe.sh --tasks --workspace "$WS" --fake` 输出；
- 任务目录 `meta.json`、`worker.json`、`approvals.jsonl`、`worker.log`；
- `git -C "$WS" status --porcelain` 与 worktree 列表；
- 若协议错误，附 attach 进程 stderr/JSONL 帧。

## 5 清理

```bash
# 停 worker（如有）
cd "$PROJECT"
./run_vibe.sh --stop --workspace "$WS" || true
rm -rf "$WS" /tmp/vibe-nongit
```

## 6 与自动化测试的对应

| 人工用例 | 自动化覆盖 |
|---|---|
| TC-02 attach/snapshot | `test_worker_attach_control.py`、TUI protocol/attach tests |
| TC-03 approval | worker approval tests + TUI approval path（需人工确认 UI 回放） |
| TC-04 observer | `test_worker_attach_control.py` |
| TC-05 takeover | `test_worker_attach_control.py` |
| TC-06 detach | worker 生命周期测试（部分） |
| TC-07 kill switch | `test_killswitch.py` |
| TC-08 merge | `test_merge.py`、`test_cli_merge_stop.py` |
| TC-09 non-git | `test_worker.py::test_worker_rejects_non_git_without_opt_in` |

人工验收重点是：**真实 pty 终端下的 TUI 渲染/键盘/审批面板/attach 切换**，以及 worker 在真实交互中的可观察性。
