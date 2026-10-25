# 修复：重开会话后不重新读取也能修改已有文件

`tests/test_tools.py` 中的恢复守卫测试当前失败：

- `test_restored_session_requires_reread_before_editing`：标记恢复后，不重新读取就能编辑/覆盖已有文件；
- `test_restored_session_allows_new_and_own_written_files` 依赖同一守卫。

要求：
- 让这两个测试通过，不要修改测试；
- 恢复后的首次修改已有文件前必须先重新读取；新建文件和本会话自己写过的文件不受影响；
- 读取后若文件被外部改动，仍要拒绝修改（不能放宽已有新鲜度检查）；
- 只做相关最小改动；
- 运行相关测试并报告命令、退出码与改动文件。
