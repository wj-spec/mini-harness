# 免费 Web 搜索工具调研与接入建议

日期：2026-10-21。范围：当前可用的免费检索方式、Python 接口与本项目的接入边界。价格和免费政策以本次读取的一手文档为准；官方文档可读不代表当前网络出口一定能完成真实检索。

## 结论

基础工具保持一个 `web_search` 即可，返回标题、URL、摘要和明确的错误。当前有两个无需账号即可起步的候选：**Tavily keyless 官方 API** 和 **`ddgs==9.16.0` 本地元搜索库**。Tavily 现在已经支持无 key 调用，不能再按旧资料写成“必须注册 key”。[Tavily keyless 官方文档](https://docs.tavily.com/documentation/keyless)、[DDGS 固定版本](https://pypi.org/project/ddgs/9.16.0/)

本轮选择 **DDGS**：无需配置账号，提供本地元搜索接口，已有经审批的真实检索成功证据，并通过独立进程接入可取消执行路径。Tavily keyless 作为可选方案保留；其原生 HTTP 接口适合异步调用，但免费数值限额未公开。当前只交付一个搜索工具，不增加供应商切换或账号升级功能。两者均须在审批中说明检索词将发送给第三方；DDGS 仍要接受网页结构变化和上游反爬导致失败。这是结合本项目实现与实测作出的取舍，不是搜索质量或服务可用率的基准测试结论。

## 方案对比

| 方案 | 免费方式 | 账号、key、信用卡 | 接入与限制 | 本项目判断 |
|---|---|---|---|---|
| Tavily keyless | 官方 Search / Extract 免费试用，受限流控制；官方没有给出固定数值额度 | 不需要账号、key 或信用卡 | 原生 HTTP API；成功结构与带 key 相同 | 可选方案，本轮不接入 |
| Tavily 免费账号 | 每月 1,000 credits；basic 搜索每次 1 credit，advanced 每次 2 credits | 需要账号和 key，不需信用卡 | 有明确月额度；与 keyless 使用同一响应结构 | keyless 额度不足时由用户自行选择的升级路径 |
| DDGS | MIT 开源库，无库服务计费；调用公开搜索服务 | 不需账号、key 或信用卡 | 多引擎聚合，依赖上游可达性和网页解析；没有无限使用或 SLA 承诺 | 本轮选择，固定版本并隔离同步工作 |
| Brave Search API | 每计划每月 5 美元额度；Search 每 1,000 次 5 美元 | 需要账号、key 和信用卡 | 官方当前明确没有独立免费套餐；免费额度可覆盖约 1,000 次 Search | 有绑卡门槛，当前不作为默认 |
| SearXNG | 免费开源软件；自建承担机器和维护成本 | 自建无统一 key / 绑卡要求，实例可配置访问控制 | 需要部署；公共实例常关闭 JSON，可能限流或遭上游封禁 | 有自建需求后再考虑 |

来源：[Tavily keyless](https://docs.tavily.com/documentation/keyless)、[Tavily credits](https://docs.tavily.com/documentation/api-credits)、[DDGS](https://github.com/deedy5/ddgs/tree/v9.16.0)、[Brave 当前套餐与免费额度说明](https://api-dashboard.search.brave.com/documentation/resources/help-feedback)、[Brave Search 定价](https://brave.com/search/api/)、[SearXNG API](https://docs.searxng.org/dev/search_api.html)。

## Tavily：官方支持的无 key 接口

无需安装 SDK，可向 `https://api.tavily.com/search` 发送 POST JSON，使用请求头 `X-Tavily-Access-Mode: keyless` 和 `Content-Type: application/json`。最小请求是 `{"query": "Python asyncio documentation", "max_results": 3}`。若同时发送有效 Bearer key，API key 优先并消耗账号额度，因此不能把已有环境变量悄悄接入 keyless 路径。[官方 keyless 请求与优先级](https://docs.tavily.com/documentation/keyless)

标准结果位于响应的 `results` 数组，条目包含 `title`、`url`、`content`、`score`，可能附 `raw_content` 等字段。接入时只保留需要的标题、地址和短摘要，关闭生成答案和完整正文，避免多余内容进入上下文。[Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)

可选 SDK 为 `tavily-python`：本次 PyPI 最新版 0.8.5，发布于 2026-10-06，要求 Python >=3.8。`TavilyClient().search(query, max_results=5, timeout=10)` 可以无 key 使用；未显式传 key 时会读取 `TAVILY_API_KEY`，因此官方 HTTP 示例更容易固定本项目实际使用的访问方式。[PyPI](https://pypi.org/project/tavily-python/)、[SDK 源码](https://github.com/tavily-ai/tavily-python/blob/master/tavily/tavily.py)

SDK 搜索请求默认超时 60 秒。限流有 `TavilyKeylessLimitError`，携带 `code`、`window`、`retry_after_seconds`；其他异常包括 `UsageLimitExceededError`、`InvalidAPIKeyError`、`ForbiddenError`、`BadRequestError`、`TimeoutError`。直接 HTTP 集成要自己处理非 2xx、无效 JSON、错误 envelope 与缺失结果，不能把错误消息当成功搜索摘要。[SDK 参数](https://docs.tavily.com/sdk/python/reference)、[官方异常类](https://github.com/tavily-ai/tavily-python/blob/master/tavily/errors.py)

keyless 的官方承诺是免费、限流、适合探索和轻量使用，没有公布可以依赖的每日或每月数值。带 key 文档的 Development 100 RPM / Production 1,000 RPM 不能套用到 keyless。收到 429 时可展示 `Retry-After`，不应自动注册、付费或在当前工具内无限重试。[keyless 限制](https://docs.tavily.com/documentation/keyless)、[带 key 的限流说明](https://docs.tavily.com/documentation/rate-limits)

隐私方面，无 key 也会把检索词传给 Tavily；官方专门说明 keyless 适用于尝试，生产或敏感场景需要明确选择账号、合同和数据控制。不能把“无账号”解释为“不出网”或“零留存”。[Tavily keyless 数据说明](https://www.tavily.com/blog/What-keyless-search-really-means-for-your-data)、[隐私政策](https://www.tavily.com/privacy)

## DDGS：固定 9.16.0 后可直接调用

本次 PyPI 最新版为 9.16.0，发布于 2026-08-26，Python >=3.10，MIT；基础依赖为 `click`、`primp`、`lxml`，不需要安装 `[api]` 或 `[mcp]` 扩展。适合放在 `app/`，不进入 `core/`。[PyPI 固定版本](https://pypi.org/project/ddgs/9.16.0/)、[固定版本依赖定义](https://github.com/deedy5/ddgs/blob/v9.16.0/pyproject.toml)

库的概念接口为：

```python
from ddgs import DDGS

results = DDGS(timeout=5).text(
    query="Python asyncio documentation",
    region="us-en",
    safesearch="moderate",
    max_results=5,
    backend="auto",
)
```

`text()` 返回 `list[dict]`，标准字段是 `title`、`href`、`body`。`region` 默认 `us-en`；`timelimit` 接受 `d/w/m/y`；`page` 默认为 1；`backend` 默认 `auto`。结果应转换成工具自己的稳定字段，而不是把第三方返回对象直接长期暴露给模型。[固定版本 README](https://github.com/deedy5/ddgs/blob/v9.16.0/README.md)、[结果类型源码](https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/results.py)

源码核对要点：

- `proxy=None` 时仍自动读取 `DDGS_PROXY`。代理不能在工具错误中原样输出，避免泄露其中的用户名或密码。
- `_engines_cache` 是实例内字典；审阅的入口、检索、HTTP、结果聚合路径使用内存缓存，未见落盘或 P2P 缓存调用。此结论限定于 9.16.0 审阅路径，不扩展为所有依赖或未来版本从不写盘的保证。
- 搜索在同步线程池中分批执行，`timeout=5` 同时传入各引擎的 HTTP 客户端和批次等待，不构成完整调用的硬上限。异步调用方仅取消等待不能证明底层检索已停止；独立子进程配合整体超时和回收可提供清楚的停止边界。

上述为固定版本源码观察：[DDGS 核心](https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/ddgs.py)、[HTTP 客户端](https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/http_client.py)。

`DDGSException` 是基本异常；另有 `TimeoutException` 和 `RatelimitException`。但本版本核心没有为所有 HTTP 429 统一生成 `RatelimitException`。无结果时也可能抛 `DDGSException("No results found.")`，不能只靠 `[]` 判断无结果；不要根据普通错误文本声称已明确区分无结果、限流与反爬。[异常定义](https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/exceptions.py)、[检索结束逻辑](https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/ddgs.py)

`backend="auto"` 会选择多个上游；DDGS 中的 `brave` 后端抓取 `search.brave.com` 网页，并非 Brave Search API。DuckDuckGo 后端请求公开 HTML 搜索。网页结构变化、验证码、IP 限制和网络可达性都可能使解析无结果；这是从实现方式推导的风险。项目 README 还明确注明教育用途，不能据其开源许可证宣称上游允许任意抓取或无限调用。[Brave 后端](https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/engines/brave.py)、[DuckDuckGo 后端](https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/engines/duckduckgo.py)、[README 声明](https://github.com/deedy5/ddgs/blob/v9.16.0/README.md)

隐私边界是检索词和出口 IP 会到达所选上游，代理配置还会改变流量路径。`auto` 不提供“只把查询交给 DuckDuckGo”的保证。Wikipedia 后端可能进一步获取长正文，结果长度必须由本项目限制。[引擎选择](https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/ddgs.py)、[Wikipedia 后端](https://github.com/deedy5/ddgs/blob/v9.16.0/ddgs/engines/wikipedia.py)

## Brave 与 SearXNG 的后续位置

Brave 当前要求绑卡激活计划，生成 API key；支持把预付金额设为 0 以只使用每月免费额度，可能有立即退回的 0 或 1 美元验证冻结。不能沿用旧文章中的“独立免费套餐 2,000 次/月”。官方文档还限制 API 数据留存，若需要把结果存入 Agent 会话，应先核对适用方案及条款。[当前免费额度和留存说明](https://api-dashboard.search.brave.com/documentation/resources/help-feedback)、[Quickstart](https://api-dashboard.search.brave.com/documentation/quickstart)

调用 Brave 可直接用现有 HTTP 客户端：GET `https://api.search.brave.com/res/v1/web/search`，请求头 `X-Subscription-Token`，参数 `q` 和 `count`，读取 `web.results` 中的 `title`、`url`、`description`；无需为了 HTTP API 增加第三方 Python SDK。[Brave 官方请求与响应示例](https://brave.com/search/api/)

SearXNG 是可自建的 AGPL-3.0-or-later 元搜索服务，源码要求 Python >=3.10。客户端使用 HTTP，与服务端 Python 版本解耦。GET 或 POST `/search` 并传 `q`、`format=json`；JSON 格式必须由实例启用，否则返回 403，官方明确很多公共实例禁用了这类格式。[服务端包定义](https://github.com/searxng/searxng/blob/master/setup.py)、[HTTP API](https://docs.searxng.org/dev/search_api.html)

公共实例还需要信任管理员；实例可能记录请求，也可能因滥用被上游 CAPTCHA 或封 IP。SearXNG 有自身 bot / IP 限制器，但自建也不会自动消除上游限流。需要稳定私有出口和配置控制时再自建，不把随机公共实例硬编码为默认免费 API。[私有实例与隐私](https://docs.searxng.org/own-instance.html)、[Limiter](https://docs.searxng.org/admin/searx.limiter.html)

## 接入本项目的边界与验收

依据现有 `app/tools.py`、[建设方向](../roadmap.md) 和 [W1 / W2 里程碑](../MILESTONES.md)，建议作为单 Agent 的一个基础工具切片，沿用 `ToolRegistry → ApprovalGate → 工具执行 → 模型回灌`：

1. 暴露 `query` 和少量检索选项；限制空查询、字符串长度、结果数量（最多 10）。供应商地址、任意 HTTP 目标和代理不让模型自由指定。
2. 将搜索标记为网络风险，审批先展示供应商和将外发的查询；拒绝或等待审批时取消，不启动请求。
3. 异步 HTTP 设置请求超时；若用同步 DDGS 则懒加载并在可回收子进程运行，另设整体超时。任务取消后回收执行实体，避免只取消外层等待。
4. 只保留有效 HTTP(S) URL、标题、短摘要，限制每项和总长度。结果是外部不可信数据；其中的操作指令不改变用户目标、审批或系统规则。
5. 搜索失败、无结果、超时、取消分别回灌可核对的状态；不把未经确认的结果包装成成功。提示 Agent 用返回 URL 引用来源，不把摘要当已阅读全文。
6. 先写确定性测试，覆盖审批拒绝零请求、错误响应、格式异常、空结果、超时、取消、截断和真实调用参数；再运行项目测试与一次公开查询 smoke。

本轮已完成一次 DDGS 真实网络 smoke：通过 `FakeProvider → ApprovalGate → 独立搜索进程`，返回 3 条 `docs.python.org` 的 TaskGroup 官方文档结果，结果回灌第二轮模型成功，记录位于 `.vibe/web-search-smoke.json`。它证明当前网络出口下该查询能够运行，以及确定性模型的工具回灌接线；FakeProvider 不证明真实模型会自主选择检索或正确引用来源，也不能保证所有引擎和查询持续可用。

实现和自动测试仅证明接线及失败边界；真实模型的选择检索和引用能力需要另外验收。此调研不提升 W1 的真实使用验收状态，也不引入全文抓取、多供应商平台或自动付费逻辑。
