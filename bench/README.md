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

- `adapter.py`：Harbor `BaseInstalledAgent`，容器内上传 `app/`、`core/`；优先复用镜像自带 uv 与 Python 3.11+（SWE-bench 镜像的 Dockerfile 已装 uv），只安装 `fastmcp/ddgs/httpx`；镜像 Python <3.11 时才回退 uv-managed 3.12。执行 `python -m app --task ... --bench`。
- `support.py`：模型/环境/telemetry 的纯逻辑，可离线测试。
- 非交互入口：`python -m app --task "..." --bench --telemetry-out ... --runtime-dir ...`。

## 加速本地跑分镜像（可选）

Harbor 每个 trial 都会在任务容器里安装一次 Agent 依赖；如果每次都从 PyPI 下载会比较慢。可以给本地 task 加一层预装缓存（只改 `local-bench/` 下的任务副本，不改官方评分逻辑）：

```bash
python scripts/prepare_bench_image.py local-bench/smoke/astropy__astropy-7606

# 首次需要重建镜像
PYTHONPATH=. uv run --managed-python --python 3.12 \
  --with "harbor==0.20.0" harbor run \
  -p local-bench/smoke \
  --agent bench.adapter:VibeAgent \
  --model deepseek/deepseek-flash \
  --env docker -n 1 --force-build
```

脚本会：

- 在任务 Dockerfile 末尾预建 `/opt/vibe-venv`（`fastmcp/ddgs/httpx`），adapter 在 trial 里只做 symlink；
- 预热 verifier parser 的 uv cache（`swebench/datasets/fastcore`）与 testbed 的 `pip install -e .[test]`；
- 在 `task.toml` 的 `[verifier]` 加 `UV_HTTP_TIMEOUT=600` 与 PyPI 镜像 env；
- 所有预装步骤带 `timeout` 和 `|| true`，网络差时镜像仍能构建，adapter 会回退到运行时安装。

默认镜像源是 `https://pypi.tuna.tsinghua.edu.cn/simple`，可用 `--pypi-index https://mirrors.aliyun.com/pypi/simple/` 替换。镜像层只在本地构建，不改变评测题目或隐藏测试。

## 批量跑本地 SWE-bench Verified

下载指定 task（只拉 6 个文件，不克隆 4.3GB 数据集）：

```bash
python3 scripts/fetch_swebench_task.py \
  astropy__astropy-12907 django__django-11099 \
  sympy__sympy-20590 matplotlib__matplotlib-14623 \
  pytest-dev__pytest-7432 \
  --dest local-bench/batch1
```

全量时 task 数量多，建议共享宿主机缓存，而不是给每个 task 重建预装镜像。Harbor 支持 `--mounts`，把 uv/pip cache 和 uv-managed Python 挂进容器：

```bash
mkdir -p ~/.cache/vibe-bench/uv ~/.cache/vibe-bench/pip ~/.local/share/uv/python

MOUNTS=$(python3 - <<'PY'
import json, os
home = os.path.expanduser("~")
print(json.dumps([
    {"type": "bind", "source": f"{home}/.cache/vibe-bench/uv", "target": "/root/.cache/uv"},
    {"type": "bind", "source": f"{home}/.cache/vibe-bench/pip", "target": "/root/.cache/pip"},
    {"type": "bind", "source": f"{home}/.local/share/uv/python", "target": "/root/.local/share/uv/python"},
]))
PY
)
```

先跑 oracle 校验 task 口径（不需要 API key）：

```bash
PYTHONPATH=. uv run --managed-python --python 3.12 \
  --with "harbor==0.20.0" harbor run \
  -p local-bench/batch1 \
  --agent oracle \
  --env docker -n 2 \
  --job-name "oracle-batch1-$(date +%s)" \
  --mounts "$MOUNTS" \
  --environment-build-timeout-multiplier 5
```

再用 Agent 跑同一批：

```bash
PYTHONPATH=. uv run --managed-python --python 3.12 \
  --with "harbor==0.20.0" harbor run \
  -p local-bench/batch1 \
  --agent bench.adapter:VibeAgent \
  --model deepseek/deepseek-flash \
  --env docker -n 2 \
  --job-name "agent-batch1-$(date +%s)" \
  --mounts "$MOUNTS" \
  --environment-build-timeout-multiplier 5 \
  --agent-setup-timeout-multiplier 5
```

缓存目录会跨 task 复用：第一个 task 预热 uv/pip/python 3.12，后续 task 明显更快。全量 500 题要分批跑，批次之间清理不用的 Docker 镜像：

```bash
docker image prune -af
```

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
