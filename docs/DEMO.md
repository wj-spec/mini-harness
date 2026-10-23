# 3 分钟 demo

前置：按 [使用指南](USAGE.md) 安装依赖并配置真实模型，在项目根目录运行。此演示会调用模型，运行时间取决于模型响应和审批等待。

```bash
./run_tests.sh -q
```

## 0:00–0:30 启动

```bash
./scripts/demo.sh
```

它会复制 `examples/demo-repo` 到临时目录并启动 Ink 工作台。

看启动日志：

- `AGENTS.md loaded`：项目规则已注入；
- `session ...`：本次 session 的 JSONL 路径。

## 0:30–1:10 先读再答

输入：

```
这个项目怎么跑？calc.py 有什么问题？
```

预期：模型调用 `read_file` / `list_files`，指出 `test_calc.py` 的断言和 `calc.py` 的 bug。

## 1:10–2:00 改文件 + 审批

输入：

```
修一下 calc.py，让 python3 test_calc.py 通过。不要改测试。
```

预期：

1. 模型读 `calc.py`；
2. 调 `edit_file`；
3. **弹出 diff 审批框**；
4. 按 `y` 允许一次；
5. 模型调 `bash` 跑测试，可能再弹一次命令审批；
6. 输出 `ok`。

> 拒绝就按 `n`：文件不变，模型收到 `ERROR denied_by_user`，可以继续解释。

## 2:00–2:30 看成本

输入：

```
/cost
```

预期：显示 runs / tokens / cost / tools / last status。

如果价格没配，cost 是 0：

```bash
VIBE_INPUT_PRICE_PER_1M=0.27 VIBE_OUTPUT_PRICE_PER_1M=1.10 ./scripts/demo.sh
```

## 2:30–3:00 恢复与 eval

退出后：

```bash
./run_ink.sh --workspace <demo-repo> --continue
```

将 `<demo-repo>` 替换为启动时输出的临时工作区路径。明确停止或取消的任务先展示保存进度，输入 `/resume` 才开启新一轮预算；缺少终态记录的中断任务沿恢复流程处理。恢复后先核对实际文件和执行结果。

最后跑一个小 eval：

```bash
./run_vibe.sh --eval --eval-limit 3
```

报告在 `.vibe/evals/<timestamp>/report.md`。

## 讲解重点

- 审批不是弹窗装饰：拒绝后副作用必须为零；
- 上下文不是无限堆：1M 也分区，超了先 offload，再 recall，再摘要；
- session 不是聊天记录：它是 append-only 事件日志，崩溃后要能对账；
- eval 不是“感觉能跑”：有 check、trace、cost、regression delta。
