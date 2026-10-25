import type {Entry} from '../view-state.js';

const PATCH_HEADER = /^diff --git a\/(.+?) b\/(.+)$/u;
const PATCH_NEW_FILE = /^\+\+\+ b\/(.+)$/u;
const PATCH_OLD_FILE = /^--- a\/(.+)$/u;

/** 从 apply_patch 的 unified diff 文本提取目标路径；不解释内容，只用于侧栏标记。 */
export function patchPaths(patch: string): string[] {
  const paths: string[] = [];
  for (const line of patch.split('\n')) {
    const header = PATCH_HEADER.exec(line);
    if (header) {
      paths.push(header[2]!);
      continue;
    }
    const newer = PATCH_NEW_FILE.exec(line);
    if (newer) paths.push(newer[1]!);
  }
  if (paths.length) return paths;
  for (const line of patch.split('\n')) {
    const older = PATCH_OLD_FILE.exec(line);
    if (older) paths.push(older[1]!);
  }
  return paths;
}

/** 本轮 Agent 实际成功写过的文件；用于侧栏高亮与跟随。 */
export function agentTouchedFiles(entries: Entry[]): string[] {
  const files: string[] = [];
  const seen = new Set<string>();
  const add = (value: unknown) => {
    if (typeof value !== 'string' || !value || value.length > 512) return;
    const normalized = value.replace(/^\.\//u, '');
    if (seen.has(normalized)) return;
    seen.add(normalized);
    files.push(normalized);
  };
  const latestRun = [...entries].reverse().find(entry => entry.kind === 'tool' && entry.runId)?.runId;
  for (const entry of entries) {
    if (entry.kind !== 'tool' || entry.ok === false || entry.uncertain) continue;
    if (latestRun && entry.runId && entry.runId !== latestRun) continue;
    const name = entry.call?.name;
    const args = entry.call?.args ?? {};
    if (name === 'write_file' || name === 'edit_file') add(args.path);
    if (name === 'apply_patch' && typeof args.patch === 'string') for (const item of patchPaths(args.patch)) add(item);
  }
  return files.slice(-200);
}
