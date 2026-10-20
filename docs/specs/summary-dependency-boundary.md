# 摘要依赖分层

## 目标

`core/` 在只有 Python 标准库的环境里可以导入并生成确定性摘要。

## 改动

- `core.summarize` 只保留 `summarize_messages`。
- `LLMSummarizer` 及其 HTTP 请求、模型提示词和输入整理移到 `app.summarize`。
- 入口在 `app/` 创建摘要适配器，再注入现有的 `ContextBuilder.summarizer`。
- 保留现有模型配置、请求参数、输出截断、错误记录和确定性回退行为。

## 验证

- 先用独立 Python 进程验证 `core` 无应用依赖；迁移前应失败。
- 摘要 HTTP 测试验证请求配置、成功返回和失败回退，使用 MockTransport。
- 迁移后执行全套测试和 fake TUI 启动检查。

## 边界

产品长期方向记录到现有文档；本轮不新增长期记忆或个人数据功能。
