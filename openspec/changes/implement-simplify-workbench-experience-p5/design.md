# Design
## Scope
阶段 5.A：统一入口 + max_steps、max_run_seconds、max_run_tokens、max_run_cost_usd。时长必须大于 0，其余 0 表示不限；费用只是按配置价格计算的估算，不承诺服务商硬账单上限。
## Decisions
- 复用阶段 4 的 .env 原子读写、版本复核和 ApprovalGate；只替换四项限制，保持 Key/代理及其他项不变。
- CLI → 进程环境 → 本次明确应用 → 安装目录 .env → 默认。保存和应用分开；运行中可编辑，空闲后应用。
- 任务第一次开始执行时在既有 trace 写入 run_limits；运行中冻结，续段与 /resume 查原快照；找不到旧快照明确提示并使用启动时限制，不受之后的应用影响。
- 运行期间临时给 Agent 使用所选限制，结束或异常恢复 Agent 原字段，下个新任务再读取默认；计划 profile 仍可进一步收紧。TaskRecord/goal 总账 schema 保持不变。
- 配置页只影响当前前台后端，新后台进程按其启动环境，旧后台进程不热更新。
- 四个区域以键盘切换，复用 ReadingPane/ActionButtons；独立数字草稿、校验提示、返回保护；审批优先。
- 高级页仅输出 MCP 数量/状态、沙箱开关、通知是否配置，避免输出外部工具参数、环境或 webhook 凭据。
## Follow-up
5.B 需统一前后台 goal 总预算和快照后再提供编辑；5.C 需独立验证 MCP 重连/失败回退再提供生命周期操作。高级安全开关不做未经验证的热切换。
