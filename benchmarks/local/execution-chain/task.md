# 修复：命令执行证据链在升级后断裂

一次跨层重构后，命令的执行状态与退出码在多处丢失。以下测试当前失败：

- `tests/test_execution_persistence.py`：工具消息不再携带 execution 元数据，恢复/压缩后退出码丢失；
- `tests/test_delivery_receipt.py::test_receipt_records_verified_command_exit_codes`：交付核对不再包含实际验证的退出码。

此外，界面回归：工具卡应显示 `零退出 · exit=0` / `非零退出 · exit=2` 这类执行状态，而不是笼统的“调用已返回”。

要求：
- 让上述测试通过，不要修改测试；
- 修复要贯通链路：工具执行结果 → Agent 消息与命令记录 → stdio 事件 → Ink 状态与展示；
- 保持既有行为：正常结束、超时、取消的命令分别显示对应状态；
- 只做相关最小改动；
- 运行相关 Python 测试与 Ink 测试并报告命令、退出码与改动文件。
