# 参与 mini-harness

欢迎提交 bug 复现、文档改进、使用反馈和代码改动。项目仍在早期，优先把单 Agent 编码任务做可靠、可理解；较大的功能或架构调整，请先在 [Issues](https://github.com/wj-spec/mini-harness/issues) 讨论具体场景。

## 报告问题

请提供操作系统与终端、启动命令、复现步骤、预期与实际行为。可附相关错误信息或截图；不要附 API Key、完整 .env 或含私人代码的会话日志。

~~~bash
./start.sh --doctor
./run_vibe.sh --version
~~~

没有 Key 时可用 <code>./start.sh --doctor --fake</code> 检查本地演示环境。自检会报告配置是否齐全，不会调用真实模型。

## 开发环境

准备 Python 3.11+、Node.js 22+ 和 Git。克隆仓库后：

~~~bash
./start.sh --install
~~~

开发测试额外需要 pytest：

~~~bash
# 使用 uv
uv sync --extra test

# 或使用已经创建的项目 venv
.venv/bin/python3 -m pip install 'pytest>=9,<10'
~~~

常用检查：

~~~bash
./run_tests.sh -q
npm --prefix tui test
./start.sh --demo
~~~

更完整的验收入口是 <code>./scripts/verify_all.sh</code>。真实模型验收需要 Key 且会消耗额度，不是普通文档修改的前置条件；相关口径见[评测规格](docs/specs/evaluation.md)。

## 提交改动

1. 从当前主线创建一个用途清楚的分支，保持 PR 聚焦。
2. 修改行为前先加或调整测试，复现问题，再实现改动。
3. 跑与修改相关的检查，提交前运行 ./run_tests.sh；涉及 Ink 时运行前端测试并检查终端效果。
4. PR 说明问题、改动后的行为、验证结果和仍未验证的部分。
5. 行为或配置发生变化时同步使用文档。工程验收数据写入对应记录，不堆到项目首页。

## 目录与边界

| 目录 | 职责 |
| --- | --- |
| core/ | 可复用 harness，仅依赖 Python 标准库 |
| app/ | 模型接入、运行、工具、审批、会话等应用逻辑 |
| tui/ | React + Ink 界面 |
| scripts/ | 启动、开发和验证辅助脚本 |
| docs/ | 使用说明、设计、研究与验收记录 |

完整约定见 [AGENTS.md](AGENTS.md)、[目录地图](docs/STRUCTURE.md)、[app 分域](app/README.md)和 [core 分域](core/README.md)。

写文件与执行命令经过 ApprovalGate；不要绕开审批。运行时产物放 .vibe/，不提交 .env、密钥、会话记录、依赖目录和编译产物。

代码按项目的 [MIT 许可证](LICENSE) 发布。
