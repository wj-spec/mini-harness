# 本地编码基准任务

每个目录是一个任务，格式固定：

- `task.md`：给 Agent 的完整任务描述（也是评分时的上下文）。
- `setup.py`：在仓库副本里注入初始 bug；断言源码锚点，代码变化后需要同步更新。
- `red.sh`：确认初始状态失败（退出码非 0 才算 RED 符合预期）。
- `check.sh`：评分入口。先用 `$BENCH_REPO_ROOT` 恢复原始测试文件（防止改测试绕过），
  再复制 `hidden/` 下的隐藏测试，运行并返回退出码。
- `hidden/`：不在工作区里出现的验收测试。

运行方式见 [本地编码基准规格](../../docs/specs/local-coding-benchmark.md)：

```bash
python3 scripts/bench_local.py --prepare   # 只校验夹具，不需要 API key
python3 scripts/bench_local.py             # 真实模型跑全部任务
```

新增任务时保证：初始 RED、修复后 check 通过。`report.json` 的 `failure_class` 自动区分 `no_change`、`edited_tests`、`agent_error`、`run_<status>` 与 `incomplete_fix`；人工再按需要归档为「没复现 / 找错文件 / 改测试 / 没验证 / 报告不实」。
