# Benchmark adapters（Harbor）

把本仓库的 Agent 接进 [Harbor](https://www.harborframework.com/docs/agents) 的任务容器，用于 SWE-bench / Terminal-Bench 跑分。设计与非目标见 `docs/specs/harbor-benchmark.md`。

## 前置

- Docker daemon（macOS 可用 `colima start`）
- Python 3.12+ / `uv`
- 模型 API key（`DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`；也支持 `VIBE_BENCH_*` 覆盖）
- 任务容器能访问模型端点

## 单题 smoke（推荐先跑）

先确认 Harbor 可用（本工作区已用 0.20.0 验证过 CLI 与 adapter 导入）：

```bash
PYTHONPATH=. uv run --with "harbor==0.20.0" harbor --version
```

```bash
export DEEPSEEK_API_KEY="sk-..."
PYTHONPATH=. uv run --with "harbor==0.20.0" harbor run \
  -d swebench-verified \
  --agent bench.adapter:VibeAgent \
  --model deepseek/deepseek-chat \
  --env docker --n-tasks 1 -n 1
```

`--model` 用 `provider/model` 前缀；adapter 会拆出 provider、选默认 base URL，并把 key 以文件传入容器。结果与日志在 `jobs/`。

## Terminal-Bench 2.1

同样用 Harbor 运行。用 `harbor dataset list` 确认可用 dataset 名称（Harbor 0.20.0 的 `-d` 接受 `name@version`）；建议先 `--n-tasks 1` 单题验证，再按预算扩展。上游记录 Terminal-Bench 为 89 题 × 5 次尝试，全量成本和时间都很高。

## 常用覆盖

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `VIBE_BENCH_MODEL` | 覆盖模型 | `deepseek-chat` |
| `VIBE_BENCH_PROVIDER` | `openai` / `deepseek` | 由模型前缀推断 |
| `VIBE_BENCH_BASE_URL` | OpenAI-compatible endpoint | provider 默认 |
| `VIBE_BENCH_API_KEY` / 原始 key 环境变量 | 凭据 | 无（必填） |
| `VIBE_BENCH_MAX_STEPS` | 单题步数上限 | 300 |
| `VIBE_BENCH_MAX_RUN_SECONDS` | 单题墙钟上限（秒） | 1500 |
| `VIBE_BENCH_COMPACT_INTERVAL_TOKENS` | 压缩周期 | 仓库默认 |
| `VIBE_BENCH_MAX_RUN_TOKENS` | 单题 token 上限 | 仓库默认（0=不限） |

## 实现

- `adapter.py`：Harbor `BaseInstalledAgent`，容器内上传 `app/`、`core/`，用 `uv` 安装依赖，执行 `python -m app --task ... --bench`。
- `support.py`：模型/环境/telemetry 的纯逻辑，可离线测试。
- 非交互入口：`python -m app --task "..." --bench --telemetry-out ... --runtime-dir ...`。

## 限制

- 未实现 ATIF 轨迹与 sb-cli/Modal 云评分。
- `--bench` 会放宽路径与自动批准动作，只用于一次性容器；不要在日常本地会话使用。
- 上游成绩（SWE-bench Verified 80.2%、Terminal-Bench 2.1 69.44%）是历史自报结果且未随当前源码重评，不能直接当作本仓库基线。
- macOS arm64 需要 amd64 模拟，镜像首次下载较慢。

## 验证

```bash
./run_tests.sh -q tests/test_bench_mode.py tests/test_bench_adapter.py
```

本工作区已验证：Harbor 0.20.0 CLI、`bench.adapter:VibeAgent` 可导入并实例化、14 个离线测试、`--task --fake --bench` smoke；真实 Docker + 模型单题仍待跑（需要 Docker daemon 与 API key）。
