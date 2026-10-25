import type {ChangedFile, WorkspaceStatus} from './git.js';

/** 侧栏交互状态；保持纯函数，便于回归跟随/选中/滚动规则。 */
export type SidebarFocus = 'list' | 'diff';

export type SidebarState = {
  files: ChangedFile[];
  branch: string | null;
  head: string | null;
  ahead: number;
  behind: number;
  selected: string | null;
  follow: boolean;
  focus: SidebarFocus;
  diffScroll: number;
  signature: string;
  error: string | null;
};

export function initialSidebarState(): SidebarState {
  return {
    files: [], branch: null, head: null, ahead: 0, behind: 0, selected: null,
    follow: true, focus: 'list', diffScroll: 0, signature: '', error: null,
  };
}

export function fileSignature(file: ChangedFile): string {
  return `${file.status}:${file.originalPath ?? ''}:${file.additions ?? '-'}:${file.deletions ?? '-'}`;
}

export function filesSignature(files: ChangedFile[]): string {
  return files.map(file => `${file.path}\u0000${fileSignature(file)}`).join('\u0001');
}

export function selectedFile(state: SidebarState): ChangedFile | null {
  return state.files.find(file => file.path === state.selected) ?? null;
}

export function selectedIndex(state: SidebarState): number {
  return state.files.findIndex(file => file.path === state.selected);
}

/**
 * 应用一次工作区快照：
 * - 跟随开启时，自动选到签名发生变化的文件（本轮 agent 或外部改动）；
 * - 选中文件消失时退到第一个文件；
 * - 选中变化时重置 diff 滚动，其余情况保留用户位置。
 */
export function applyWorkspace(state: SidebarState, status: WorkspaceStatus): SidebarState {
  const previous = new Map(state.files.map(file => [file.path, fileSignature(file)]));
  const signature = filesSignature(status.files);
  let selected = state.selected;
  if (selected !== null && !status.files.some(file => file.path === selected)) selected = null;
  if (state.follow) {
    const changed = status.files.find(file => previous.get(file.path) !== fileSignature(file));
    if (changed) selected = changed.path;
  }
  if (selected === null && status.files.length) selected = status.files[0]!.path;
  return {
    ...state,
    files: status.files,
    branch: status.branch,
    head: status.head,
    ahead: status.ahead,
    behind: status.behind,
    selected,
    signature,
    diffScroll: selected === state.selected ? state.diffScroll : 0,
    error: null,
  };
}

export function selectDelta(state: SidebarState, delta: number): SidebarState {
  if (!state.files.length) return state;
  const current = selectedIndex(state);
  const next = Math.max(0, Math.min(state.files.length - 1, (current < 0 ? 0 : current) + delta));
  const path = state.files[next]!.path;
  if (path === state.selected) return {...state, follow: false};
  return {...state, selected: path, follow: false, diffScroll: 0, focus: 'list'};
}

export function toggleFollow(state: SidebarState): SidebarState {
  if (state.follow) return {...state, follow: false};
  const selected = state.files.length ? state.files[0]!.path : null;
  return {...state, follow: true, selected, diffScroll: 0};
}

export function toggleFocus(state: SidebarState): SidebarState {
  return {...state, focus: state.focus === 'list' ? 'diff' : 'list'};
}

export function scrollDiff(state: SidebarState, delta: number, maximum: number): SidebarState {
  const limit = Math.max(0, maximum);
  return {...state, diffScroll: Math.max(0, Math.min(limit, state.diffScroll + delta))};
}

export function clampDiffScroll(state: SidebarState, maximum: number): SidebarState {
  const limit = Math.max(0, maximum);
  if (state.diffScroll <= limit) return state;
  return {...state, diffScroll: limit};
}
