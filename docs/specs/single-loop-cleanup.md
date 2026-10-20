# 单一 Agent loop 清理

## 目标

TUI 和 eval 统一使用 `app.agent.Agent.run`，删除未接入运行入口的同步教学循环。

## 范围

- 删除 `core/loop.py` 和仅针对它的 `tests/test_loop.py`。
- 清理 `core` 的 `run` / `FakeModel` / `RunResult` 导出，以及专用的 `RunResult` / `Status` 类型。
- 保留仍被 FakeProvider 使用的 `Response` 和共享消息类型。
- 同步 README、目录说明和复盘中的当前架构描述。

## 验证

- 删除前，补强实际 Agent 的消息顺序及工具异常回灌测试。
- 删除后，运行完整测试和 fake TUI 启动检查。
- 文本回复、工具结果配对、异常恢复、取消、预算、审批和 trace 行为保持不变。

## 边界

此次只清理同步循环及其专用依赖；`archive/` 保留为历史记录。
