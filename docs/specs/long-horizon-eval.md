# 长任务评测 P0（fake worker 场景）

状态：2026-11-06 P0 场景实现（08 的 8 类 + multi-segment；真模型 P1 待后续）。
设计来源：[08 长任务评测](../design/long-horizon/08-long-horizon-eval.md)。

## 已实现

- `scripts/bench_long.py`
  - `--list`；`--real`（已配置 provider 时用真实模型，不自动跑）；
  - `--scenario <id>`（可重复）/ `--runs N` / `--run-root PATH`；
  - 每个场景创建独立 git workspace + test session/task，启动真实 `--worker --fake`；
  - 收集 task meta / worker state / approvals / changed files / duration；
  - 写 `.vibe/bench-long/<ts>/report.json` 与 `report.md`；
  - 同场景多次运行报告 `repeated_scenarios`（terminal/worker/segments/changed_files 一致）；
  - `summary.scenario_stats`：每场景 runs/passed/failed/pass_rate/avg_duration/max_duration，
    `report.md` 同步生成 scenario stats 表（重复运行稳定性对照）。
- 场景目录 `benchmarks/long/*.json`，schema 包含 `id/description/objective/action/worker_env/expected/timeout_seconds`。
- 当前场景（`benchmarks/long/`）：
  1. `detach-continue`：后台自然完成；
  2. `approval-timeout`：审批过期 → task failed + approval expired；
  3. `kill-switch`：stop → worker killed、stop file；
  4. `restart-attach`：attach observer 后任务继续完成；
  5. `crash-no-rerun`：kill -9 进程组 → supervisor recover → task interrupted，已执行段不重跑；
  6. `two-slot-write-serial`：第二个写 worker 通过 `VIBE_WORKSPACE_LOCK_WAIT=30` 排队，第一个完成后串行执行；默认仍立即拒绝；
  7. `goal-criteria-pending`：未确认 `acceptance_criteria` → worker 启动前退出，task 保持 created；
  8. `no-duplicate-effects`：崩溃恢复后 changed files 为空；
  9. `goal-multisegment`：max_steps 资源停止后续段，2 段完成；
  10. `task-deadline`：`VIBE_TASK_DEADLINE_SECONDS` 到期 → task failed、worker failed、事件 `task_deadline_exceeded`。
- 测试：`tests/test_bench_long.py`（场景 schema、校验、列表、报告可重复标记、scenario_stats）。

## 用法

```bash
./.venv/bin/python3 scripts/bench_long.py --list
./.venv/bin/python3 scripts/bench_long.py --scenario detach-continue --runs 3
```

## 未完成（P0 剩余）

- `two-slot-write-serial` 已覆盖写任务排队串行；只读任务并发与文件集并行仍待后续；
- 多段场景目前 1 个（goal-multisegment），其余场景为单段；
- 故障注入器仍是脚本内直连（kill -9/stop/attach），无 5xx/时钟/seed/trace 摘要；
- 重复运行统计已有 pass rate/耗时聚合，但还没有跨机器/跨提交的历史趋势对比。
- 成本 cap、token/成本指标、`agent_version`、真实模型 P1；
- fake 通过只表示接线/生命周期语义，不代表模型能力。
