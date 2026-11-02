# Harbor / SWE-bench Verified 踩坑与经验记录

> 目的：记录在本地 Windows/WSL + Harbor + mini-harness 上跑 SWE-bench Verified 时犯过的错误、根因、修复和防再犯检查项。
> 适用：`bench/adapter.py`、`scripts/fetch_swebench_task.py`、`scripts/prepare_bench_image.py`、`scripts/bench_report.py`、`scripts/collect_predictions.py` 相关流程。
> 更新：2026-11-02。

## 0. 当前状态快照

- 数据集：**SWE-bench Verified 500**（不是原始 SWE-bench 2,294，也不是 Lite 300）。
- 5 题 smoke：Oracle 5/5；Agent 5/5（Harbor 口径）。
- 20 题 oracle：19/20；`django__django-10097` 的 oracle 失败，尚未定位到官方口径原因。
- 19 题 Agent（Harbor 口径）：**15/19 resolved**。
- 当前正在重跑 4 个失败任务，验证新增的 bench 策略。
- 官方分数链路：Harbor 只跑 Agent，官方 `swebench` harness 评分；目前尚在打通。

## 1. 评测口径错误

### 1.1 把 Harbor 分数当成官方 SWE-bench 分数

- 现象：Harbor 输出 `Mean=1.0` / `resolved=true`，就以为拿到了官方 SWE-bench Verified 分数。
- 根因：Harbor 是独立的 Agent benchmark 执行器，`swebench-verified` 是它自己转换的 task 格式；官方评测器是 SWE-bench 自己的 `swebench` 包。
- 修复：Harbor 只负责跑 Agent、保存 patch；官方分数用 `swebench.harness.run_evaluation` 对 `predictions.jsonl` 计算。
- 防再犯：任何“官方分数”必须由官方 harness 产出；Harbor 的 `resolved` 只能叫“Harbor 口径”。

### 1.2 手工删除 PASS_TO_PASS 测试

- 现象：`astropy__astropy-7606` 的 verifier 报 `test_compose_roundtrip[]` 失败；我们手工把它从本地 `tests/config.json` 删掉后 oracle 1.0。
- 根因：本地 task 副本/缓存与官方数据集不一致；但当时没有先核对官方数据，直接用“删测试”来让 oracle 变绿。
- 真相：官方 Verified 的 7606 PASS_TO_PASS 本来就**没有** `test_compose_roundtrip[]`（241 项）；Harbor 本地副本多了一项。
- 修复：用官方 parquet 校验/覆盖 task config；不再手工删测试。
- 防再犯：
  - 先跑 `scripts/sync_verified_configs.py --write`；
  - 看官方 `princeton-nlp/SWE-bench_Verified` parquet；
  - 禁止为了让 oracle 变绿而修改 PASS_TO_PASS/FAIL_TO_PASS。

### 1.3 在 oracle 失败时直接跑 Agent

- 现象：`django__django-10097` oracle 0 分，仍继续跑 Agent，结果该题必失败。
- 根因：没有先确认 task/verifier 本身可解。
- 修复：每批先跑 oracle；oracle 不通过的 task 先定位，不进入 Agent 统计。
- 防再犯：**oracle preflight 是硬门禁**；全量 500 也必须先分批 oracle。

### 1.4 空 ID / 缺失测试导致假 0

- 现象：verifier 报某个测试失败，但 pytest 输出里根本没有该测试。
- 根因：Harbor task config 与官方 PASS_TO_PASS 不一致（空参数 ID、测试收集差异）。
- 修复：官方数据集校验；不要手工删；若官方 harness 也复现，记录为数据集/环境问题。
- 防再犯：`bench_report.py` 里同时看 `failed_tests` 和 `verifier/test-stdout.txt` 是否真的出现该测试名。

## 2. 网络 / 代理 / 镜像

### 2.1 把 shell 代理当成 Docker 代理

- 现象：`git pull`、uv 下载都走了代理，但 `docker pull` 和 Harbor 的 Docker build 仍然极慢/卡住。
- 根因：三层代理互相独立：
  1. shell 的 `HTTP_PROXY`：curl/uv/pip/git；
  2. Docker daemon 代理：拉基础镜像；
  3. 容器内代理：verifier/parser 访问 GitHub、pip、uv。
- 修复：
  - daemon：`/etc/systemd/system/docker.service.d/http-proxy.conf`；
  - 容器：`bench/proxy-compose.yaml` + `scripts/local_proxy_bridge.py`；
  - shell：每个 shell 手动 export。
- 防再犯：每层都用对应命令验证；不要只验证 shell。

### 2.2 Docker build 的 RUN 不继承代理

- 现象：5 个任务 Docker build 卡在 `RUN curl -LsSf https://astral.sh/uv/...`，最后 exit 1。
- 根因：daemon 代理只管拉基础镜像；`RUN curl` 在构建容器里执行，不继承 daemon/容器代理。
- 修复：`scripts/patch_task_uv.py` 把 `RUN curl uv` 替换为从本地 `local/uv:0.7.13` 镜像 `COPY`。
- 防再犯：
  - 每个新 task 下载后先 `python3 scripts/patch_task_uv.py <task-root>`；
  - 本地 uv 镜像必须先用带 glibc 的基础镜像构建（见 2.5）。

### 2.2b Docker build 前 base image metadata EOF

- 现象：`docker compose build` 报 `failed to resolve source metadata ... Head https://registry-1.docker.io/v2/.../manifests/latest: EOF`。
- 根因：该 task 的 `swebench/...:latest` base image 本地没有缓存，BuildKit 去 Docker Hub 解析 tag 时网络被 reset；不是 `local/uv` 或 task 代码问题。
- 修复：先 `docker pull` 该 task Dockerfile 的 `FROM` 镜像，再跑 Harbor；可批量预拉。
- 防再犯：
  - 每批开跑前 `for d in ...; do docker pull "$(grep -m1 '^FROM ' "$d/environment/Dockerfile" | awk '{print $2}')"; done`；
  - 全量 500 题按批次预拉，避免 build 阶段临时解析 `latest`。

### 2.3 registry mirror 配置错误/慢

- 现象：`daemon.json` 里有 `docker.1panel.1ive`（疑似拼写错误）和多个公共 mirror，拉取仍然慢。
- 根因：无效/限速 mirror 会被 Docker 优先尝试；公共 mirror 不一定代理 `swebench/...`。
- 修复：移除无效 mirror；用代理或实测最快的 mirror；必要时只留一个。
- 防再犯：`docker info` 检查 Registry Mirrors；用 `docker pull` 实测，不要凭配置猜。

### 2.4 TUN 模式不作用于 WSL/Docker

- 现象：Windows 开了 TUN，浏览器正常，但 WSL 里官方 registry/镜像仍然慢或不通。
- 根因：TUN 只接管 Windows 侧流量；WSL 的 Docker daemon 走自己的路由。
- 修复：关闭 TUN，开启 HTTP/Mixed 代理端口（如 17891），daemon + 容器分别配代理。
- 防再犯：用 `curl -x http://127.0.0.1:<port>` 验证代理端口，而不是只看 Windows 浏览器。

### 2.5 本地 uv 镜像 `FROM scratch` 无法运行

- 现象：`docker run local/uv:0.7.13 /uv --version` 报 `exec /uv: no such file or directory`。
- 根因：uv 是动态链接二进制，scratch 没有 glibc loader。
- 修复：`FROM ubuntu:22.04` 或 `debian:bookworm-slim` 再 COPY uv。
- 防再犯：本地 uv 镜像必须能实际执行 `/uv --version`。

### 2.6 容器内 parser 访问 GitHub 被 reset

- 现象：verifier 的 parser 报 `ConnectionResetError(104)`；`report.json` 没生成。
- 根因：Docker daemon 代理不作用于容器进程；parser `make_test_spec` 需要从 GitHub 拉 requirements。
- 修复：`scripts/local_proxy_bridge.py` 监听 WSL `0.0.0.0:17892`，转发到 Windows 代理；`--extra-docker-compose bench/proxy-compose.yaml` 注入容器 `HTTP_PROXY`。
- 防再犯：每次 Harbor run 都带 proxy overlay；验证容器内 `curl -x http://host.docker.internal:17892 https://github.com`。

## 3. Harbor / 任务缓存

### 3.1 改本地 task 后 Harbor 继续用旧副本

- 现象：改了 `local-bench/.../tests/config.json`，verifier 仍用旧配置。
- 根因：Harbor 缓存本地 task 到 `~/.cache/harbor/tasks/<hash>/`。
- 修复：修改后 `rm -rf ~/.cache/harbor/tasks`。
- 防再犯：任何 task 文件修改后都清缓存；Dockerfile 改动也需要。

### 3.2 `--force-build` 和缓存混用

- 现象：有时镜像不重建，有时重复构建；环境和 task 缓存理解混乱。
- 修复：首次改 Dockerfile 用 `--force-build`；后续复用镜像；本地 task 变更清 task cache。
- 防再犯：分开记住“镜像层缓存”和“task 文件缓存”。

### 3.3 job 目录路径写错

- 现象：`JOB=jobs/xxx` 传给 `--job-name`，产生 `jobs/jobs/xxx`。
- 根因：`--job-name` 本身会拼到 `jobs/` 下。
- 修复：`JOB="agent-$(date +%s)"`，不要带 `jobs/` 前缀。
- 防再犯：统一用时间戳 + 前缀。

## 4. 运行 / 环境 / 脚本

### 4.1 WSL 没有 `python`

- 现象：`python - <<'PY'` 全部失败，导致“移除 PASS_TO_PASS”实际上没执行。
- 根因：WSL 里只有 `python3`，但命令按 macOS 习惯用了 `python`。
- 修复：所有脚本/命令用 `python3`。
- 防再犯：文档和命令统一 `python3`；出错先看 command not found。

### 4.2 API key 失效/旧 key

- 现象：Agent `NonZeroAgentExitCodeError`，tokens 0；telemetry 里 `HTTP 401 ... invalid`。
- 根因：chat 里暴露过的旧 key 被撤销/替换，shell 里还是旧值。
- 修复：重新 `read -s` 输入新 key，`curl /v1/models` 验证 200。
- 防再犯：每次跑 Agent 前验证 key；不要在聊天里贴 key。

### 4.3 没有保存 model.patch

- 现象：Harbor 打完分后无法生成官方 predictions，因为容器已销毁。
- 根因：旧 adapter 只写 telemetry，没有保存 `git diff`。
- 修复：adapter `run()` 结束后写 `agent/model.patch` 和 `git-status.txt`。
- 防再犯：任何要出官方分数的 run 必须用新 adapter；先用 `collect_predictions.py` 检查 patch 存在。

### 4.4 预装 layer 没有超时，10 小时挂起

- 现象：Harbor 跑了 10 小时，最后 CancelledError。
- 根因：Dockerfile `RUN uv run` / `pip install -e .[test]` 没有超时；网络挂起。
- 修复：`prepare_bench_image.py` 的每层加 `timeout` + `|| true`。
- 防再犯：任何 Dockerfile 网络 RUN 必须有硬超时；构建日志要能看见。

### 4.5 全量镜像/磁盘

- 现象：500 题镜像可能 0.5–1.5TB，本机 924G 会满。
- 修复：分批跑，批间 `docker image prune -af`；数据盘/云机器。
- 防再犯：跑前 `df -h` / `docker system df`；每批 20–50 题。

## 5. Agent / harness 策略

### 5.1 允许 Agent 修改测试文件

- 现象：matplotlib、sympy 的 Agent 直接在 `tests/` 下加/改测试；官方 patch 被污染。
- 根因：bench prompt 只是“不要改测试”，没有工具级约束。
- 修复：bench 模式 `write_file/edit_file/apply_patch` 拒绝测试路径；`collect_predictions.py` 默认剥离测试 diff 块。
- 防再犯：官方评测前检查 `predictions.jsonl` 没有测试文件 hunk。

### 5.2 Agent 看到失败测试仍直接 done

- 现象：astropy-13033 看到了失败测试，最后仍 `status=done`。
- 根因：bench 模式之前关闭了 completion guard。
- 修复：bench 模式启用 completion guard：最后一次修改后必须有成功验证，否则拒绝一次 done；prompt 要求失败测试不能直接结束。
- 防再犯：看 `bench_report.py` 的 `agent_status` 和 `failed_tests` 交叉验证；不要只看 status。

### 5.3 HTTP 400：tool_calls 与 tool 消息不配对

- 现象：sympy-12096 报 `An assistant message with 'tool_calls' must be followed by tool messages...`。
- 根因：请求历史里 assistant tool_calls 与 tool 结果数量/ID 不完整。
- 修复：`ensure_tool_pairing` 补 ID、修复错配、补缺失结果；每次模型请求前执行。
- 防再犯：保留该回归测试；出现 400 先看 tool 配对，而不是先怀疑模型。

### 5.4 Agent 在 `/app` 与 `/testbed` 之间浪费步数

- 现象：多个 Agent 先 `cd /app` 失败，再发现 `/testbed`。
- 根因：prompt 没有明确 workspace 路径。
- 修复：bench prompt 明确“当前目录是任务仓库，通常 /testbed”。
- 防再犯：新 task 先在 prompt 里确认 workspace；adapter 传 `--workspace "$PWD"`。

## 6. 必须记住的检查清单

每次跑分前：

- [ ] `git pull` 成功，adapter 是新版；
- [ ] `python3` 不是 `python`；
- [ ] `DEEPSEEK_API_KEY` 有效（curl 200）；
- [ ] Docker daemon proxy 生效（`docker info`）；
- [ ] 代理桥监听 `17892`；
- [ ] `--extra-docker-compose bench/proxy-compose.yaml`；
- [ ] `--mounts` 缓存挂载；
- [ ] 本地 task 已 `sync_verified_configs.py`；
- [ ] Dockerfile 已 `patch_task_uv.py`；
- [ ] 改了 task 文件后清 `~/.cache/harbor/tasks`；
- [ ] 每批 base images 已 `docker pull`（避免 build metadata EOF）；
- [ ] 先 oracle preflight，再 Agent。

出结果后：

- [ ] `bench_report.py` 看失败原因；
- [ ] `collect_predictions.py` 确认 `model.patch` 存在且无测试 hunk；
- [ ] 官方 `swebench.harness.run_evaluation` 评分；
- [ ] 不要把 Harbor 的 resolved 当官方分数。

## 7. 尚未解决的问题

1. `django__django-10097` oracle 失败：conftest/config 与官方一致，但当前镜像/测试执行下 7 个 `AuthTemplateTests` 失败；需要官方 harness 交叉验证。
2. 官方 `swebench` harness 的评测容器网络/代理尚未打通。
3. 500 题全量的镜像拉取、磁盘、API token 成本与批次策略尚未最终确定。
4. `deepseek-flash` 在 19 题上仍有 3 题能力失败；是否换 `deepseek-chat` 需要对照实验。

## 8. 相关提交

- `3112b88` perf(bench): reuse image uv and Python 3.11
- `18d3642` fix(bench): bound prebuilt image layer with timeouts
- `06af5b6` feat(bench): fetch SWE-bench tasks and batch via shared cache mounts
- `ff159bc` feat(bench): bridge container egress to host HTTP proxy
- `f6573e3` fix(bench): avoid Docker build-time uv download
- `c5632f1` feat(bench): sync task configs with official Verified dataset
- `574c005` feat(bench): capture model patches and report per-task failures
- `e6a2c89` fix(agent): repair tool call pairing before model requests
- `edaa812` fix(bench): reject test edits, enable completion guard, strip test hunks
