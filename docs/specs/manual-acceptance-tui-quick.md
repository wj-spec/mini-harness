# TUI 终端快速验收清单（人工，10 分钟）

日期：2026-11-07。用途：在**真实终端**里补完自动化无法覆盖的 Ink 渲染/键盘路径。
自动化已覆盖：Python 全量、TUI 单测（含 8 个真实后端跨层用例）、长任务 12/12、真实模型 suite 六模式 6/6。
本清单只验证「真实终端里的可见效果与按键手感」。

## 0. 环境

```bash
cd /path/to/mini-harness
set -a; . ./.env; set +a          # 需要真实 provider；也可用 --fake 走界面
./run_ink.sh --doctor             # 期望：结论「可以启动 ./run_ink.sh」
```

## 1. 启动与首屏

```bash
./run_ink.sh
```

期望：像素猫 Mini 首屏、项目/会话信息、输入框可输入中文；`Ctrl+Q` 退出时若有运行中任务应弹确认。

## 2. 单任务 + 审批面板

输入：`读取 README.md 并用一句话说明项目用途，只读。`

期望：流式输出；无工具审批（只读）；结束后状态栏显示「已完成 · 已验证」；`/evidence` 可看到本轮证据与验收结果行。

## 3. 审批拒绝路径

输入：`创建 .vibe/acceptance-demo.txt 并写入 ok`（或在 VIBE_FAKE_DEMO=1 下直接回车）。

期望：审批面板出现副作用层级；按 `n` 拒绝后文件不存在，Agent 明确说明未执行。

## 4. Plan 覆盖层按键

1. `/plan` 打开覆盖层；
2. `↑↓` 选择、`s` 步骤详情、`t` 改步骤标题（选中步骤行）、`d` 与上一版本比较、`v` 版本列表、`a` 验收信息；
3. proposed 计划按 `y` 批准；approved 计划 `Enter` 执行、`b` 后台执行；
4. `Esc` 返回对话。

期望：各键在真实终端里响应迅速、覆盖层滚动正常、提示行完整显示。

## 5. `/explore` 子 Agent

输入：`/explore 读取 README.md 并总结，只读。`

期望：出现「子 Agent 开始 / 子 Agent ok」notice、`subagent_event` 提示、返回摘要与 tokens/费用；`/cost` 含 subagent 计数。

## 6. 后台 worker 与通知

```bash
# 终端 A：后台 plan
./run_vibe.sh --tasks --workspace <ws>
./run_vibe.sh --notifications --workspace <ws>
./run_vibe.sh --approvals --workspace <ws>
./run_vibe.sh --evidence --session <sid> --workspace <ws>
./run_vibe.sh --trace-summary --session <sid> --workspace <ws>
# TUI 内也可 /trace 或 /trace 24 查看观测摘要
```

期望：`--tasks` 显示 MODE/APPROVALS/PRIORITY 列；`/notifications` 与 CLI 都能看到 `worker_started/worker_exited`；`--decide-approval` 可处理后台待审批。

## 7. 计划队列

```bash
./run_vibe.sh --prepare-plan-task --plan-task-priority 5 --session <sid> --workspace <ws>
./run_vibe.sh --run-plan-queue --queue-dry-run --session <sid> --workspace <ws>
# TUI 内可用 /queue 查看队列、/priority <task_id> 9 调整待运行任务优先级
./run_vibe.sh --run-plan-queue --session <sid> --workspace <ws>
```

期望：dry-run 显示 `plan_executable=true` 与 priority；串行执行后 task completed、验收结果可在 `acceptance.v1.json` 与 `/evidence` 核对。

## 8. attach / stop

```bash
./run_vibe.sh --tasks --workspace <ws>      # 找到运行中的 task
./run_vibe.sh --attach --mode observe --workspace <ws> --session <sid> --task-id <tid>
./run_vibe.sh --pause-task <tid> --workspace <ws>
./run_vibe.sh --stop --workspace <ws>
```

期望：observe 只读回放；pause/stop 后任务状态与 worker 状态符合预期，不留孤儿进程。

## 9. 侧栏与复制

1. `Ctrl+G` 或 `/sidebar` 打开右侧 Git 变更侧栏（Ghostty/Terminal 按本机配置）；
2. 拖拽选中文本松开复制；`Ctrl+Y`/`/copy` 复制最近回复。

期望：侧栏跟随工作区刷新；复制内容正确、中文不截断。

## 记录

| 步骤 | 通过? | 备注 |
|---|---|---|
| 1 启动/首屏 |  |  |
| 2 单任务 |  |  |
| 3 审批拒绝 |  |  |
| 4 Plan 覆盖层 |  |  |
| 5 /explore |  |  |
| 6 后台/通知/证据 |  |  |
| 7 计划队列 |  |  |
| 8 attach/stop |  |  |
| 9 侧栏/复制 |  |  |
