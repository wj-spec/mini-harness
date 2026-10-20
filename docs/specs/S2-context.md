# S2 Spec · Context

> 目标：给定一串 messages 和一个 token 预算，产出一串更小的 messages。
> 硬约束：pinned 不丢；每条被压缩的内容都有引用或摘要；工具调用和结果不被拆散。

## 接口

```python
@dataclass
class ContextResult:
    messages: list[Message]
    tokens: int
    offloaded: list[str]          # 可 resolve 的引用
    summarized: bool
    over_budget: bool
    notes: list[str]              # 发生了什么，给 trace 用

class ContextBuilder:
    def __init__(max_tokens=800, reserve_output=200, keep_recent_groups=2,
                 offload_dir=None, summarizer=None, estimator=estimate_tokens)
    def build(messages) -> ContextResult
    def resolve(ref) -> list[Message]
```

## 行为

1. `budget = max_tokens - reserve_output`；`estimator` 默认 `len//4`，可注入。
2. `system` 消息 = pinned；pinned 单独超预算时，`over_budget=True`，**不截断**，只报告。
3. 按“轮”分组：`assistant(tool_calls)` + 紧随的 `tool` 结果是一组，不能拆开。
4. 压缩顺序：
   - 先 offload 含 `tool` 的旧组；
   - 再 offload 其他旧组；
   - 还不够，就把剩余旧组合并成一条 `[summary]`（summarizer 可注入）；
   - 还没有，就 drop 最旧组，并记 `notes`。
5. 最近 `keep_recent_groups` 组永远保留。
6. 每条被压缩的组，要么有 `ref`（可 `resolve`），要么进了 summary，要么有 `dropped` 记录；**不允许静默消失**。

## 失败路径

- pinned 超预算 → `over_budget=True`，不截断。
- summarizer 抛异常 → 捕获，`notes` 记 `summary_failed`，继续 drop，不崩。
- offload 目录不可写 → 捕获，`notes` 记 `offload_failed`，退化为 summary/drop。
- 空 messages → 返回空，`tokens=0`。
- 预算 <= 0 或 pinned 超预算 → 保留原 messages，`over_budget` 标记，**不静默截断**。

## 不做

- 真 tokenizer（先用 `len//4`；以后可换 tiktoken）。
- 向量检索、语义压缩、真 LLM 摘要（测试用 fake summarizer）。

## 测试

- 未超预算：messages 不变。
- pinned 超预算：不截断，`over_budget=True`。
- 工具组先被 offload；`resolve(ref)` 能拿回原始组。
- offload 落盘；新 builder 也能 `resolve`。
- 最后 N 组保留。
- 预算很小：用 summarizer，结果含 `[summary]`。
- summarizer 抛异常：不崩，`notes` 有 `summary_failed`。
- `stable_prefix_hash` 不随 history 变化。
- 没有静默丢失：每个被移除的组都有 ref / summary / dropped 记录。
