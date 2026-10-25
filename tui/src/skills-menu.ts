import type {SkillInfo} from './protocol.js';

export type SkillMenuState = {
  matches: string[];
  descriptions: Record<string, string>;
};

function describe(skill: SkillInfo): string {
  const status = skill.active ? '已激活' : '未激活';
  return `${skill.summary || '（无说明）'} · ${status}`;
}

/** /skills 后的动态补全菜单：↑↓ 滚动，Tab 填入完整命令，Enter 执行。 */
export function buildSkillMenu(input: string, skills: readonly SkillInfo[]): SkillMenuState | null {
  if (!skills.length || !input.startsWith('/skills')) return null;
  const trimmed = input.trimEnd();
  const wanted = (action: 'activate' | 'unload', skill: SkillInfo) =>
    ({command: `/skills ${action} ${skill.name}`, description: describe(skill)});

  let entries: {command: string; description: string}[];
  if (trimmed === '/skills' || input === '/skills') {
    entries = skills.map(skill => wanted(skill.active ? 'unload' : 'activate', skill));
  } else if (input.startsWith('/skills activate')) {
    entries = skills.map(skill => wanted('activate', skill));
  } else if (input.startsWith('/skills unload')) {
    entries = skills.filter(skill => skill.active).map(skill => wanted('unload', skill));
  } else {
    return null;
  }
  const filtered = entries.filter(entry => entry.command.startsWith(input));
  if (!filtered.length) return null;
  return {
    matches: filtered.map(entry => entry.command),
    descriptions: Object.fromEntries(filtered.map(entry => [entry.command, entry.description])),
  };
}
