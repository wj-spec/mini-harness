export type CommandGroup = '查看' | '任务' | '设置';
export type CommandDefinition = {
  command: string; group: CommandGroup; description: string;
  usage?: string; required?: boolean; aliases?: readonly string[]; keywords?: readonly string[];
};

const definitions: readonly CommandDefinition[] = [
  {command: '/help', group: '查看', description: '查看操作帮助', usage: '[命令或中文关键词]'},
  {command: '/resume', group: '任务', description: '继续已保存的任务', aliases: ['/continue'], keywords: ['续做任务', '恢复任务', '继续任务']},
  {command: '/context', group: '查看', description: '查看上下文与预算'},
  {command: '/cost', group: '查看', description: '查看本轮与会话成本', aliases: ['/costs']},
  {command: '/inspections', group: '查看', description: '查看历史结果与验证记录', keywords: ['历史结果', '旧任务结果']},
  {command: '/find', group: '查看', description: '在本次会话中查找关键词并定位', usage: '<关键词>', required: true, keywords: ['查找', '搜索', '定位']},
  {command: '/rounds', group: '查看', description: '按轮次定位本次会话历史', keywords: ['轮次', '历史定位']},
  {command: '/errors', group: '查看', description: '定位工具错误与失败轮次', keywords: ['错误', '失败定位']},
  {command: '/tools', group: '查看', description: '查看可用工具目录'},
  {command: '/diff', group: '查看', description: '查看工作区改动', keywords: ['查看改动', '文件变化']},
  {command: '/permissions', group: '设置', description: '查看权限来源与有效期，撤销本进程授权', keywords: ['授权', '权限', '撤销']},
  {command: '/permissions reset', group: '设置', description: '撤销本进程授权（档位与持久策略不变）'},
  {command: '/mcp', group: '查看', description: '查看 MCP 服务'},
  {command: '/skills', group: '设置', description: '查看技能列表', aliases: ['/skill']},
  {command: '/skills refresh', group: '设置', description: '重新发现技能'},
  {command: '/skills activate', group: '设置', description: '激活指定技能', usage: '<技能名>', required: true},
  {command: '/skills unload', group: '设置', description: '卸载指定技能', usage: '<技能名>', required: true},
  {command: '/goal', group: '任务', description: '查看目标；输入目标后自动续段', usage: '[目标]'},
  {command: '/goal stop', group: '任务', description: '停止当前目标自动续段'},
  {command: '/goal resume', group: '任务', description: '恢复已保存的目标'},
  {command: '/task', group: '查看', description: '选择当前会话任务、查看状态与记录', aliases: ['/tasks']},
  {command: '/approvals', group: '查看', description: '查看待审批收件箱'},
  {command: '/session', group: '查看', description: '查看会话路径'},
  {command: '/sessions', group: '查看', description: '选择并预览旧会话，显式打开（不自动运行）'},
  {command: '/theme', group: '设置', description: '查看或切换主题', usage: '[system|light|dark]'},
  {command: '/theme system', group: '设置', description: '跟随终端主题'},
  {command: '/theme light', group: '设置', description: '使用浅色主题'},
  {command: '/theme dark', group: '设置', description: '使用深色主题'},
  {command: '/followup', group: '任务', description: '查看后续消息；带正文排队', usage: '[消息正文]'},
  {command: '/copy', group: '查看', description: '复制最近回复'},
  {command: '/project', group: '设置', description: '管理项目背景'},
  {command: '/project add', group: '设置', description: '添加项目背景', usage: '<文本>', required: true},
  {command: '/project remove', group: '设置', description: '删除一条项目背景', usage: '<序号>', required: true},
  {command: '/project source', group: '设置', description: '修改项目背景来源', usage: '<序号> <来源>', required: true},
  {command: '/about', group: '设置', description: '管理个人偏好'},
  {command: '/about add', group: '设置', description: '添加已确认的个人偏好', usage: '<文本>', required: true},
  {command: '/about propose', group: '设置', description: '提出待确认的个人偏好', usage: '<文本>', required: true},
  {command: '/about observe', group: '设置', description: '从本会话提出待确认偏好'},
  {command: '/about edit', group: '设置', description: '修改个人偏好', usage: '<序号> <文本>', required: true},
  {command: '/about triggers', group: '设置', description: '修改偏好触发词', usage: '<序号> <逗号分隔触发词>', required: true},
  {command: '/about confirm', group: '设置', description: '确认待处理的偏好', usage: '<序号>', required: true},
  {command: '/about disable', group: '设置', description: '停用个人偏好', usage: '<序号>', required: true},
  {command: '/about enable', group: '设置', description: '启用个人偏好', usage: '<序号>', required: true},
  {command: '/about remove', group: '设置', description: '删除个人偏好', usage: '<序号>', required: true},
  {command: '/about expire', group: '设置', description: '设置或清除偏好过期时间', usage: '<序号> <天数|ISO 时间|->', required: true},
  {command: '/about resolve', group: '设置', description: '用待确认偏好替换冲突旧条目', usage: '<序号>', required: true},
  {command: '/about on', group: '设置', description: '启用个人偏好注入'},
  {command: '/about off', group: '设置', description: '关闭个人偏好注入'},
  {command: '/about clear', group: '设置', description: '清理指定状态的偏好', usage: '<proposed|stale>', required: true},
  {command: '/about clear proposed', group: '设置', description: '清理待确认的偏好'},
  {command: '/about clear stale', group: '设置', description: '清理已停用的偏好'},
  {command: '/about reset', group: '设置', description: '查看清空个人偏好的确认说明'},
  {command: '/about reset confirm', group: '设置', description: '确认清空个人偏好'},
  {command: '/about export', group: '查看', description: '导出脱敏的个人偏好'},
  {command: '/about audit', group: '查看', description: '查看个人偏好操作记录'},
  {command: '/sidebar', group: '查看', description: '打开右侧变更侧栏'},
  {command: '/sidebar split', group: '查看', description: '窗口内右侧分屏（跟随窗口）'},
  {command: '/sidebar quick', group: '查看', description: '屏幕右侧 quick terminal（不挤占）'},
  {command: '/sidebar window', group: '查看', description: '独立窗口模式（手动摆位）'},
  {command: '/sidebar setup', group: '设置', description: '写入 Ghostty 右侧配置'},
  {command: '/evidence', group: '查看', description: '查看最近一轮验收证据'},
  {command: '/trace', group: '查看', description: '查看会话 trace 观测摘要', usage: '[最近小时数]'},
  {command: '/priority', group: '任务', description: '调整待运行任务的队列优先级', usage: '<task_id> <0-100>', required: true},
  {command: '/queue', group: '查看', description: '查看后台计划任务队列'},
  {command: '/autonomy', group: '设置', description: '查看或调整授权档位', usage: '[ask|edits|workspace|full]'},
  {command: '/autonomy ask', group: '设置', description: '每次询问工具授权'},
  {command: '/autonomy edits', group: '设置', description: '自动允许工作区文件编辑'},
  {command: '/autonomy workspace', group: '设置', description: '自动允许工作区写入和执行'},
  {command: '/autonomy full', group: '设置', description: '完全执行，包含写入、执行和网络'},
  {command: '/chat', group: '任务', description: '本次只对话，不调用工具', usage: '<任务描述>', required: true},
  {command: '/act', group: '任务', description: '本次执行任务，不退出会话计划模式', usage: '<任务描述>', required: true},
  {command: '/plan', group: '任务', description: '审阅计划；输入任务开始只读规划', usage: '[任务描述]'},
  {command: '/plan on', group: '设置', description: '开启会话计划模式'},
  {command: '/plan off', group: '设置', description: '退出会话计划模式'},
  {command: '/plan status', group: '查看', description: '查看会话计划模式状态'},
  {command: '/plan request-changes', group: '任务', description: '按反馈修订计划', usage: '<反馈>', required: true},
  {command: '/plan dismiss', group: '任务', description: '关闭待审计划'},
  {command: '/plan markdown', group: '查看', description: '查看完整计划审阅稿'},
  {command: '/plan all', group: '查看', description: '查看完整计划'},
  {command: '/plan card', group: '查看', description: '查看计划审批卡'},
  {command: '/plan list', group: '查看', description: '查看计划版本'},
  {command: '/plan acceptance', group: '查看', description: '查看计划验收'},
  {command: '/plan diff', group: '查看', description: '比较两个计划版本', usage: '<版本一> <版本二>', required: true},
  {command: '/plan step', group: '查看', description: '查看计划步骤', usage: '<步骤 ID>', required: true},
  {command: '/plan edit-step', group: '任务', description: '修改计划步骤标题', usage: '<步骤 ID> <标题>', required: true},
  {command: '/plan kind', group: '任务', description: '修改计划步骤类型', usage: '<步骤 ID> <read|edit|run|verify>', required: true},
  {command: '/plan files', group: '任务', description: '修改计划步骤文件', usage: '<步骤 ID> <逗号分隔路径>', required: true},
  {command: '/plan depends', group: '任务', description: '修改计划步骤依赖', usage: '<步骤 ID> <逗号分隔依赖>', required: true},
  {command: '/plan revise', group: '任务', description: '创建修改目标后的计划草稿', usage: '<新目标>', required: true},
  {command: '/plan background', group: '任务', description: '准备后台执行计划'},
  {command: '/plan execute', group: '任务', description: '执行已批准的计划'},
  {command: '/plan approve', group: '任务', description: '批准当前计划'},
  {command: '/plan reject', group: '任务', description: '拒绝当前计划'},
  {command: '/explore', group: '任务', description: '只读子 Agent 探索', usage: '<问题>', required: true},
  {command: '/notifications', group: '查看', description: '查看后台任务通知', usage: '[通知类型]'},
  {command: '/attach', group: '任务', description: '查看后台任务的连接命令', usage: '[task_id]'},
  {command: '/attach now', group: '任务', description: '重新打开界面并连接后台任务', usage: '[task_id]'},
  {command: '/merge', group: '任务', description: '请求合并当前任务的工作区结果'},
  {command: '/merge approve', group: '任务', description: '批准待处理的合并请求', usage: '[approval_id]'},
  {command: '/stop-workers', group: '任务', description: '停止当前工作区的后台 worker', aliases: ['/workers stop']},
  {command: '/quit', group: '设置', description: '退出工作台', aliases: ['/exit']},
];

export const COMMAND_COMPLETIONS: readonly string[] = definitions.flatMap(item => [item.command, ...(item.aliases ?? [])]);
export const COMMAND_DESCRIPTIONS: Record<string, string> = Object.fromEntries(definitions.flatMap(item =>
  [item.command, ...(item.aliases ?? [])].map(command => [command, item.description])));
export function commandDefinition(command: string): CommandDefinition | undefined {
  const normalized = command.trim().replace(/\s+/g, ' ');
  const direct = definitions.find(item => item.command === normalized || item.aliases?.includes(normalized));
  if (direct) return direct;
  for (const item of definitions) {
    const alias = item.aliases?.find(alias => normalized.startsWith(alias + ' '));
    if (alias) return definitions.find(candidate => candidate.command === item.command + normalized.slice(alias.length));
  }
  return undefined;
}

export type CommandContext = {busy: boolean; activeCommand: string | null; tasks: boolean; blocked?: string; planModeControl?: boolean; modePending?: boolean; taskNavigation?: boolean; sessionNavigation?: boolean; permissionPanel?: boolean};

// 与 App 的本地操作、运行中纠偏和后端单请求边界一致。
export function commandUnavailable(value: string, context: CommandContext): string | undefined {
  if (context.blocked) return context.blocked;
  const text = value.trim().replace(/\s+/g, ' ');
  const root = commandDefinition(text.split(' ', 1)[0] ?? '')?.command;
  if (/^\/plan (on|off|status)$/.test(text) && context.planModeControl) return context.modePending ? '默认模式请求确认中' : undefined;
  if ((root === '/act' || root === '/chat' || root === '/plan' && text !== '/plan' && !commandDefinition('/plan ' + text.split(' ')[1])) && !context.tasks) {
    return '后端未启用任务运行时';
  }
  if (!root || !context.busy) return;
  if (root === '/task' && context.taskNavigation) return;
  if (root === '/sessions' && context.sessionNavigation) return;
  if (text === '/permissions' && context.permissionPanel) return;
  if (['/help', '/context', '/copy', '/inspections', '/find', '/rounds', '/errors', '/sidebar', '/project', '/about', '/theme', '/session', '/quit', '/attach', '/merge', '/stop-workers'].includes(root)) return;
  if (text === '/autonomy' || text === '/goal' || text === '/goal stop') return;
  if (root === '/followup') return context.activeCommand && context.activeCommand !== 'resume' ? '命令执行中，结束后再发送' : undefined;
  if (text === '/plan' || text === '/plan all') return;
  return '当前任务运行中；结束后重试';
}

export function commandMatches(value: string): string[] {
  if (!value.startsWith('/') || value.includes('\n')) return [];
  if (value === '/') {
    const order = {'查看': 0, '任务': 1, '设置': 2};
    return definitions.filter(item => !item.command.includes(' ')).sort((a, b) => order[a.group] - order[b.group]).map(item => item.command);
  }
  if (definitions.some(item => item.aliases?.includes(value))) return [value];
  const prefix = definitions.filter(item => item.command.startsWith(value) && (value.includes(' ') || !item.command.includes(' ')));
  if (prefix.length) return prefix.map(item => item.command);
  if (definitions.some(item => value.startsWith(item.command + ' '))) return [];
  const words = value.slice(1).trim().toLowerCase().split(/\s+/);
  return definitions.filter(item => words.every(word => [item.command, item.description, ...(item.keywords ?? [])].join(' ').toLowerCase().includes(word))).map(item => item.command);
}

export function commandHelp(topic = ''): string {
  const query = topic.trim().replace(/^\//, '');
  if (query) {
    const exact = commandDefinition('/' + query);
    const matches = exact ? definitions.filter(item => item.command === exact.command || item.command.startsWith(exact.command + ' '))
      : commandMatches('/' + query).map(command => commandDefinition(command)!);
    if (!matches.length) return `没有找到“${query}”的操作；输入 /help 查看分组，或输入 / 按中文查找。`;
    return matches.map(item => `用法：${item.command}${item.usage ? ' ' + item.usage : ''}\n${item.description}${item.aliases?.length ? '\n别名：' + item.aliases.join('、') : ''}`).join('\n\n');
  }
  const groups: CommandGroup[] = ['查看', '任务', '设置'];
  return ['输入 / 按中文意图或命令前缀查找；↑↓ 选择，Enter 确认，Tab 只填充。',
    '需要参数时先填写；/help context 查看单项说明。普通输入 Enter 发送，Ctrl+J 换行。',
    '工具审批可点击按钮；Tab 选择、Enter 确认，Esc 返回草稿。Ctrl+C 停止，Ctrl+Q 退出。',
    ...groups.map(group => `${group}\n` + definitions.filter(item => item.group === group && !item.command.includes(' ')).map(item =>
      `${item.command}${item.usage ? ' ' + item.usage : ''} · ${item.description}`).join('\n'))].join('\n\n');
}
