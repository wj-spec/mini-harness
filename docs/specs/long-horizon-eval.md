# 长任务评测 P0（fake worker 场景）

状态：2026-11-06 P0 起步实现（4/8 类场景）；真模型与剩余场景待后续。
设计来源：[08 长任务评测](../design/long-horizon/08-long-horizon-eval.md)。

## 已实现

- `scripts/bench_long.py`
  - `--list`；
  - `--scenario <id>`（可重复）/ `--runs N` / `--run-root PATH`；
  - 每个场景创建独立 git workspace + test session/task，启动真实 `--worker --fake`；
  - 收集 task meta / worker state / approvals / changed files / duration；
  - 写 `.vibe/bench-long/<ts>/report.json` 与 `report.md`；
  - 同场景多次运行报告 `repeated_scenarios`（terminal/worker/segments/changed_files 一致）。
- 场景目录 `benchmarks/long/*.json`，schema 包含 `id/description/objective/action/worker_env/expected/timeout_seconds`。
- 当前 4 类：
  1. `detach-continue`：后台自然完成；
  2. `approval-timeout`：审批过期 → task failed + approval expired；
  3. `kill-switch`：stop → worker killed、stop file；
  4. `restart-attach`：attach observer 后任务继续完成。
- 测试：`tests/test_bench_long.py`（场景 schema、校验、列表、报告可重复标记）。

## 用法

```bash
./.venv/bin/python3 scripts/bench_long.py --list
./.venv/bin/python3 scripts/bench_long.py --scenario detach-continue --runs 3
```

## 未完成（P0 剩余）

- 08 设计中的另外 4 类：`two-slot-write-serial`、`crash-no-rerun`、`goal-criteria-pending`、独立 `no-duplicate-effects` 横切；
- 每场景 ≥2 段（当前 fake 场景 1 段）；
- 故障注入器（kill9/5xx/时钟/seed）与可重放 trace 摘要；
- 成本 cap、token/成本指标、`agent_version`、真实模型 P1；
- fake 通过只表示接线/生命周期语义，不代表模型能力。
