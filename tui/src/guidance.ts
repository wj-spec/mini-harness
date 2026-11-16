import type {ViewState} from './view-state.js';
import {outcomePresentation} from './outcome.js';

export type GuideAction = 'example-read' | 'example-edit' | 'example-review' | 'plan' | 'inspection' | 'tasks' | 'permissions' | 'model' | 'settings';
export type GuidePage = {title: string; body: string; action?: GuideAction; label?: string};
export const GUIDE_EXAMPLES: Record<string, string> = {
  'example-read': '读一下这个仓库的 README 和主要入口，解释它如何运行。先不要修改代码。',
  'example-edit': '我想修改的功能是：\n涉及的范围是：\n完成后请这样验证：',
  'example-review': '审查当前工作区改动，列出风险、可能的回归和尚未验证的部分。先不要修改代码。',
};
export const HOME_EXAMPLES: Record<'1' | '2' | '3', {title: string; detail: string; prompt: string}> = {
  '1': {title: '了解这个项目', detail: '读 README 与主要入口，解释运行方式；不改代码。',
    prompt: GUIDE_EXAMPLES['example-read']!},
  '2': {title: '定位一个问题', detail: '先复现并说明判断依据，再给最小修改与验证方式。',
    prompt: '定位一个可复现的问题：先说明判断依据与复现步骤，再给出最小修改和验证方式。先不要修改代码。'},
  '3': {title: '实现一个修改', detail: '说明要改的行为、范围与完成条件，先看方案再动手。',
    prompt: GUIDE_EXAMPLES['example-edit']!},
};

export function guidePages(available: {tasks: boolean; plan: boolean; inspection: boolean; permissions: boolean; model?: boolean; settings?: boolean}): GuidePage[] {
  return [
    {title: '对话', label: '填示例', action: 'example-read', body:
      '在底部输入任务、范围和完成条件。Enter 发送，Ctrl+J 换行。\n\n从示例开始（按数字选择，仅填草稿）：\n1 了解项目\n2 修改代码（填入后补充空项）\n3 审查变更\n\n已有草稿时不会覆盖；运行中不会替你发送。\n运行中输入可用于纠偏；/followup 文本用于排队后续。'},
    {title: '计划进度', ...(available.plan ? {label: '看计划', action: 'plan' as const} : {}), body:
      '先规划再执行：输入 /plan 任务描述，审阅步骤、范围与验证方式。\n计划待审时 Ctrl+R 查看；执行清单用 Ctrl+L 展开。\n批准/执行仍需你明确操作，工具权限继续单独审批。\n\n' +
      (available.plan ? '“看计划”打开已有计划；没有计划时仅在空草稿填入 /plan ，由你补充后发送。' : '当前后端未提供计划入口；普通对话仍可使用。')},
    {title: '检视结果', ...(available.inspection ? {label: '看变更', action: 'inspection' as const} : {}), body:
      '结果旁的“变更 / 计划 / 验证”分别核对文件、计划与检查证据。\nCtrl+O 看工具详情；Ctrl+K 看上下文。\n检查通过不等于需求已经满足。失败或未验证时先看证据，再决定继续。\n\n' +
      (available.inspection ? '“看变更”打开当前工作区的只读检查；历史结果请点击对应结果的入口，避免混用。' : '工作区尚未连接；连接后可查看变更。') + '\n工作台提示和命令结果保存在 /logs。'},
    {title: '任务会话', ...(available.tasks ? {label: '看任务', action: 'tasks' as const} : {}), body:
      '任务：同一会话里执行过的工作。会话：一组可恢复的对话与上下文。\n\n' +
      (available.tasks ? '“看任务”打开当前会话任务目录，只查看，不自动接入或执行。' : '当前后端没有任务目录能力，可用 /session 核对当前会话。') +
      '\n/sessions 需要后端支持旧会话目录；打开旧会话不自动运行。\n/resume 才继续已保存任务。后台任务的接入与停止要核对任务身份。'},
    {title: '配置帮助', ...(available.settings ? {label: '设置', action: 'settings' as const} : available.model ? {label: '模型', action: 'model' as const} : available.permissions ? {label: '看权限', action: 'permissions' as const} : {}), body:
      '输入 / 按中文意图查找命令；/help 查看完整说明，长说明保存在 /logs。\n\n设置中心：/settings 编辑单段限制，进入模型与权限；MCP/沙箱/通知查看状态与重启要求。\n模型设置：/model 查看来源、编辑、检测、保存或应用。若当前后端尚未支持，可在终端运行 ./start.sh --configure。\n预算/MCP 等见 docs/CONFIGURATION.md，修改后重启相关进程。\n\n/autonomy 调整本进程审批档位，/permissions 查看来源与授权。\n\n下方“关提示”只关闭当前工作台的情境提示，不关闭审批提醒。/guide on 可恢复；Ctrl+T 或 /guide 可再次打开指引。'},
  ];
}

export function guidanceHint(state: ViewState, columns: number): string {
  const short = columns < 60;
  if (state.attachOffline) return '离线只读 · 先核对历史结果';
  if (state.phase === 'closed') return '连接已断开 · Ctrl+Q退出后重启';
  if (state.phase === 'starting') return '先写草稿 · 就绪后 Enter 发送';
  if (state.phase === 'waiting_approval') return '先核对操作预览，再决定是否允许';
  if (state.phase === 'stopping') return '停止请求处理中 · 等待结果后核对';
  if (state.phase === 'running') return state.attachedTaskId ? '任务仍在运行 · Ctrl+Q仅断开' :
    short ? 'Enter纠偏 · Ctrl+C停止' : '需要调整方向？输入补充后 Enter 纠偏；Ctrl+C 停止';
  if (state.resumed && !state.hasStartedRun) return short ? '已恢复未运行 · /resume续做' : '会话已恢复，尚未运行；核对上下文后 /resume 续做';
  if (state.lastStatus === 'error') return short ? '先看错误 · /logs查记录' : '先看错误与工具详情，再决定是否 /resume 重试';
  if (state.lastStatus === 'stopped' && state.lastStopReason?.startsWith('acceptance_')) return '/plan acceptance 核对计划验收';
  if (state.lastStatus === 'stopped' && state.lastStopReason === 'plan_incomplete') return '/plan 核对未完成步骤';
  if (state.lastStatus === 'cancelled' || state.lastStatus === 'stopped' || state.lastStatus === 'truncated')
    return short ? '先核对变更 · /resume续做' : '先核对已发生的变更与停止原因，再决定 /resume 续做';
  if (state.lastStatus === 'done') {
    const result = outcomePresentation({status: 'done', stop_reason: state.lastStopReason ?? '', outcome: state.lastOutcome, verified: state.lastVerified});
    return result.visible ? (short ? '先核对变更与验证，再继续' : '打开结果旁的“变更 / 验证”核对证据，再开始下一项任务') :
      (short ? '继续提问，或输入下一项任务' : '可以继续提问，或直接描述下一项任务');
  }
  return short ? '描述任务 · Ctrl+T功能指引' : '从描述任务开始 · Ctrl+T 了解五个功能区';
}
