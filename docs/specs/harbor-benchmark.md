# Harbor 跑分接入（SWE-bench / Terminal-Bench）

日期：2026-10-27。状态：**非交互入口与 Harbor adapter 已实现，离线与 fake 链路通过；真实 Docker + 模型运行待验收**（本机当前没有 Docker daemon 与 API key）。

## 目标

用 [Harbor](https://www.harborframework.com/docs/agents) 0.20.0 把本地 Agent 跑在 SWE-bench / Terminal-Bench 的任务容器里，评分交给 Harbor 的 verifier；现有 `./run_eval.sh`（内部 eval + SWE-bench Lite）语义不变。

上游参考：`mini-harness/mini-harness` 的 `bench/adapter.py`、`bench/sb_cli.py` 与 `BENCHMARKS.md`。本实现只做 Harbor 路径，不复制其评分结论。

## 接口

### 非交互入口

```bash
python -m app --task "修复 tests/test_x.py 的失败" --bench \
  --telemetry-out /logs/agent/telemetry.json \
  --runtime-dir /logs/agent/vibe
```

- `--task`：跑一次任务后退出；不与 `--stdio/--eval/--continue/--session/--list-sessions` 组合。
- `--bench`：跑分模式。自动批准动作、放宽工具路径与 bash 工作区限制、注入 `[BENCH MODE]` 提示、禁用技能/`web_search`/个人偏好与项目背景、不启动 MCP。**只用于一次性容器**。
- `--telemetry-out`：结果 JSON；默认 `<runtime-dir>/telemetry.json`。
- `--runtime-dir`：session/offload/skills 配置等运行时文件；默认 `<workspace>/.vibe/bench-run/<stamp>`。
- 退出码：`done`/`stopped` → 0；`cancelled`/`error` → 1；参数/配置错误 → 2。

### telemetry v1

`status`、`stop_reason`、`steps`、`usage`、`cost_usd`、`run_id`、`tool_calls` / `tool_calls_ok`、`duration_ms`、`started_at` / `ended_at`、`workspace` / `runtime_dir` / `session_dir`、`agent_version`、`bench`、`limits`。

### Harbor adapter

- `bench/adapter.py`：`VibeAgent(BaseInstalledAgent)`。`install()` 在容器内上传 `app/`、`core/` 与依赖清单，用 `uv` 建 Python 3.12 venv；`run()` 通过文件传递 API key，执行 `python -m app --task ... --bench --workspace "$PWD"`；`populate_context_post_run()` 回填 token 用量。
- `bench/support.py`：模型前缀解析、`VIBE_*` 环境映射、telemetry 校验；不 import harbor。

## 运行方式

前置：Docker daemon（本机可用 `colima start`）、Harbor 0.20.0、模型 API key、可访问模型端点的容器网络。

```bash
export DEEPSEEK_API_KEY="sk-..."
PYTHONPATH=. uv run --with "harbor==0.20.0" harbor run \
  -d swebench-verified \
  --agent bench.adapter:VibeAgent \
  --model deepseek/deepseek-chat --env docker --n-tasks 1 -n 1
```

结果在 `jobs/`；每题的 telemetry 会随日志目录保留。Terminal-Bench 2.1 同样走 Harbor，dataset 名称以 Harbor 文档/`harbor run --help` 为准。

## 安全边界

- `--bench` 的自动批准与路径放宽**只用于一次性容器**；不在本地日常会话启用。
- 容器内仍保留危险命令模式拦截；不读取 `~/.vibe/user-profile.json`，不发现技能，不启动 MCP。
- API key 以文件传入容器，命令读取后立即删除，不写入 telemetry 或日志。
- 网络由 Harbor 环境决定；如果容器不能访问模型端点，`doctor` 通过也会在第一次请求失败。

## 验收

- [x] 新增 14 个离线测试：oneshot 自动批准/telemetry/bench prompt、非 bench fail-closed、路径放宽、运行时目录、CLI 模式冲突。
- [x] `--task --fake --bench` 本地 smoke：telemetry 写入、退出码 0、workspace 不产生 `.vibe/`。
- [x] Harbor 0.20.0 CLI 与 `bench.adapter:VibeAgent` 导入/实例化验证（`harbor --version` = 0.20.0；`uv run --with "harbor==0.20.0"` 可加载 adapter）。
- [ ] 真实 Harbor 单题（需要 Docker + API key）。
- [ ] SWE-bench Verified / Terminal-Bench 2.1 小规模与全量（成本/时间另算）。

## 非目标

- sb-cli / Modal 云评分、ATIF 轨迹导出（上游有，本切片不做）。
- 官方 leaderboard 提交或排名。
- 多 Agent、Computer Use。
- 把 `--bench` 当成日常本地模式的沙箱替代。

## 风险

- arm64 Mac 需要 amd64 模拟；镜像首次下载较慢。
- 上游 80.2% / 69.44% 是历史自报成绩（`deepseek-v4-flash`、私有归档、部分异常），不能当作本实现的对照基线；比较必须固定模型、任务子集与预算。
- 未实现 ATIF，Harbor 侧部分轨迹功能可能缺失；SWE-bench 评分只依赖容器内 patch 与 verifier。
