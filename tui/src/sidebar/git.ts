import {spawnSync} from 'node:child_process';

/** 侧栏只读 git 查询：固定参数、不经 shell、不写工作区。 */
export type FileStatus = 'modified' | 'added' | 'deleted' | 'renamed' | 'untracked' | 'conflicted';

export type ChangedFile = {
  path: string;
  originalPath?: string;
  status: FileStatus;
  index: string;
  worktree: string;
  additions: number | null;
  deletions: number | null;
  staged: boolean;
};

export type WorkspaceStatus = {
  root: string;
  branch: string | null;
  head: string | null;
  ahead: number;
  behind: number;
  files: ChangedFile[];
};

export type GitRunResult = {status: number | null; stdout: string; stderr: string; error?: string};
export type GitRunner = (args: string[], options: {cwd: string; timeoutMs?: number; maxBuffer?: number}) => GitRunResult;

export type StatusResult =
  | {ok: true; status: WorkspaceStatus}
  | {ok: false; reason: 'not-git' | 'git-missing' | 'error'; message: string};
export type DiffResult =
  | {ok: true; text: string; truncated: boolean}
  | {ok: false; message: string};

export const DEFAULT_TIMEOUT_MS = 2000;
export const DEFAULT_MAX_BUFFER = 4 * 1024 * 1024;
export const MAX_DIFF_LINES = 2000;
export const MAX_DIFF_CHARS = 400_000;
const STDERR_LIMIT = 400;

export const STATUS_ARGS: string[] = [
  '-c', 'core.quotepath=false',
  '--no-optional-locks',
  'status', '--porcelain=v2', '-z', '--branch', '--untracked-files=all',
];

/** 只读执行 git；任何调用都必须是固定参数数组，不拼接命令字符串。 */
export function runGit(args: readonly string[], options: {cwd: string; timeoutMs?: number; maxBuffer?: number}): GitRunResult {
  try {
    const result = spawnSync('git', [...args], {
      cwd: options.cwd,
      encoding: 'utf8',
      timeout: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
      maxBuffer: options.maxBuffer ?? DEFAULT_MAX_BUFFER,
      env: {...process.env, LC_ALL: 'C', GIT_OPTIONAL_LOCKS: '0'},
    });
    return {
      status: result.status,
      stdout: result.stdout ?? '',
      stderr: result.stderr ?? '',
      error: result.error ? result.error.message : undefined,
    };
  } catch (error) {
    return {status: null, stdout: '', stderr: '', error: error instanceof Error ? error.message : String(error)};
  }
}

export function numstatArgs(hasHead: boolean): string[] {
  const head = hasHead ? ['HEAD'] : [];
  return ['-c', 'core.quotepath=false', '--no-optional-locks', 'diff', ...head,
    '--numstat', '-z', '--no-renames', '--no-color', '--no-ext-diff'];
}

export function diffArgs(file: ChangedFile, hasHead: boolean): string[] {
  if (file.status === 'untracked') {
    return ['-c', 'core.quotepath=false', '--no-optional-locks', 'diff', '--no-index',
      '--no-color', '--no-ext-diff', '-U3', '--', '/dev/null', file.path];
  }
  const head = hasHead ? ['HEAD'] : [];
  return ['-c', 'core.quotepath=false', '--no-optional-locks', 'diff', ...head,
    '--no-color', '--no-ext-diff', '-U3', '--', file.path];
}

function clip(text: string, limit = STDERR_LIMIT): string {
  const line = text.trim().split('\n').filter(Boolean).at(-1) ?? '';
  return line.length <= limit ? line : `${line.slice(0, limit)}…`;
}

function statusOf(index: string, worktree: string, conflicted: boolean): FileStatus {
  if (conflicted) return 'conflicted';
  if (index === 'U' || worktree === 'U') return 'conflicted';
  if (index === 'R' || worktree === 'R' || index === 'C') return 'renamed';
  if (index === 'D' || worktree === 'D') return 'deleted';
  if (index === 'A' || worktree === 'A') return 'added';
  if (index === '?' || worktree === '?') return 'untracked';
  return 'modified';
}

function makeFile(path: string, index: string, worktree: string, conflicted: boolean, originalPath?: string): ChangedFile {
  return {
    path,
    ...(originalPath ? {originalPath} : {}),
    status: statusOf(index, worktree, conflicted),
    index,
    worktree,
    additions: null,
    deletions: null,
    staged: index !== '.' && index !== '?' && index !== ' ' && !conflicted,
  };
}

/** 解析 `git status --porcelain=v2 -z --branch`。纯函数，便于回归。 */
export function parsePorcelainV2(output: string, root: string): WorkspaceStatus {
  const files: ChangedFile[] = [];
  const records = output.split('\0');
  let branch: string | null = null;
  let head: string | null = null;
  let oid: string | null = null;
  let ahead = 0;
  let behind = 0;
  for (let index = 0; index < records.length; index += 1) {
    const record = records[index]!;
    if (!record) continue;
    if (record.startsWith('# branch.head ')) {branch = record.slice('# branch.head '.length); continue;}
    if (record.startsWith('# branch.oid ')) {oid = record.slice('# branch.oid '.length); continue;}
    if (record.startsWith('# branch.ab ')) {
      const match = /^# branch\.ab \+(\d+) -(\d+)$/u.exec(record);
      if (match) {ahead = Number(match[1]); behind = Number(match[2]);}
      continue;
    }
    if (record.startsWith('1 ')) {
      const match = /^1 ([^ ]{2}) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) (.+)$/u.exec(record);
      if (!match) continue;
      const [, xy, , , , , , , path] = match;
      files.push(makeFile(path!, xy![0]!, xy![1]!, false));
      continue;
    }
    if (record.startsWith('2 ')) {
      const match = /^2 ([^ ]{2}) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) (.+)$/u.exec(record);
      if (!match) continue;
      const [, xy, , , , , , , , path] = match;
      const originalPath = records[index + 1];
      if (originalPath !== undefined) index += 1;
      files.push(makeFile(path!, xy![0]!, xy![1]!, false, originalPath || undefined));
      continue;
    }
    if (record.startsWith('u ')) {
      const match = /^u ([^ ]{2}) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) ([^ ]+) (.+)$/u.exec(record);
      if (!match) continue;
      const [, xy, , , , , , , , , path] = match;
      files.push(makeFile(path!, xy![0]!, xy![1]!, true));
      continue;
    }
    if (record.startsWith('? ')) {
      files.push(makeFile(record.slice(2), '?', '?', false));
    }
  }
  const detached = branch === '(detached)' || branch === null;
  const resolvedHead = oid && oid !== '(initial)' ? oid : null;
  return {
    root,
    branch: detached ? (resolvedHead ? resolvedHead.slice(0, 7) : null) : branch,
    head: resolvedHead,
    ahead,
    behind,
    files,
  };
}

/** 解析 `git diff --numstat -z --no-renames`；二进制或无法计数时值为 null。 */
export function parseNumstat(output: string): Map<string, {additions: number | null; deletions: number | null}> {
  const counts = new Map<string, {additions: number | null; deletions: number | null}>();
  for (const record of output.split('\0')) {
    if (!record) continue;
    const match = /^(\d+|-)\t(\d+|-)\t([\s\S]+)$/u.exec(record);
    if (!match) continue;
    counts.set(match[3]!, {
      additions: match[1] === '-' ? null : Number(match[1]),
      deletions: match[2] === '-' ? null : Number(match[2]),
    });
  }
  return counts;
}

export function mergeCounts(files: ChangedFile[], counts: Map<string, {additions: number | null; deletions: number | null}>): ChangedFile[] {
  return files.map(file => {
    const count = counts.get(file.path);
    if (!count) return file;
    return {...file, additions: count.additions, deletions: count.deletions};
  });
}

export function readWorkspaceStatus(root: string, run: GitRunner = runGit): StatusResult {
  const status = run(STATUS_ARGS, {cwd: root});
  if (status.error) {
    if (/ENOENT|not found/iu.test(status.error)) return {ok: false, reason: 'git-missing', message: '未找到 git 命令'};
    return {ok: false, reason: 'error', message: `git status 失败：${clip(status.error)}`};
  }
  if (status.status === 128 && /not a git repository/iu.test(status.stderr)) {
    return {ok: false, reason: 'not-git', message: '当前工作区不是 Git 仓库'};
  }
  if (status.status !== 0) {
    return {ok: false, reason: 'error', message: `git status 退出码 ${status.status ?? '未知'}：${clip(status.stderr)}`};
  }
  const statusValue = parsePorcelainV2(status.stdout, root);
  const counts = run(numstatArgs(statusValue.head !== null), {cwd: root});
  if (counts.status === 0 && counts.stdout) statusValue.files = mergeCounts(statusValue.files, parseNumstat(counts.stdout));
  return {ok: true, status: statusValue};
}

export function readFileDiff(root: string, file: ChangedFile, hasHead = true, run: GitRunner = runGit): DiffResult {
  const result = run(diffArgs(file, hasHead), {cwd: root, maxBuffer: DEFAULT_MAX_BUFFER});
  if (result.error) return {ok: false, message: `读取 diff 失败：${clip(result.error)}`};
  // --no-index 在存在差异时返回 1，属于正常结果。
  if (result.status !== 0 && result.status !== 1) {
    return {ok: false, message: `读取 diff 失败（退出码 ${result.status ?? '未知'}）：${clip(result.stderr)}`};
  }
  let text = result.stdout;
  let truncated = false;
  const lines = text.split('\n');
  if (lines.length > MAX_DIFF_LINES) {
    text = `${lines.slice(0, MAX_DIFF_LINES).join('\n')}\n… diff 已截断（共 ${lines.length - 1} 行）`;
    truncated = true;
  }
  if (text.length > MAX_DIFF_CHARS) {
    text = `${text.slice(0, MAX_DIFF_CHARS)}\n… diff 已截断（超过 ${MAX_DIFF_CHARS} 字符）`;
    truncated = true;
  }
  return {ok: true, text, truncated};
}

export function statusLetter(file: ChangedFile): 'M' | 'A' | 'D' | 'R' | 'U' | '?' | 'C' {
  switch (file.status) {
    case 'added': return 'A';
    case 'deleted': return 'D';
    case 'renamed': return 'R';
    case 'untracked': return '?';
    case 'conflicted': return 'U';
    default: return 'M';
  }
}

export function statusLabel(file: ChangedFile): string {
  switch (file.status) {
    case 'added': return '新增';
    case 'deleted': return '删除';
    case 'renamed': return '重命名';
    case 'untracked': return '未跟踪';
    case 'conflicted': return '冲突';
    default: return '修改';
  }
}

export function changeCounts(file: ChangedFile): string {
  if (file.additions === null && file.deletions === null) return '';
  const additions = file.additions === null ? '?' : `+${file.additions}`;
  const deletions = file.deletions === null ? '?' : `-${file.deletions}`;
  return `${additions} ${deletions}`;
}

export function totalCounts(files: ChangedFile[]): {additions: number; deletions: number; unknown: boolean} {
  let additions = 0;
  let deletions = 0;
  let unknown = false;
  for (const file of files) {
    if (file.additions === null || file.deletions === null) {unknown = true; continue;}
    additions += file.additions;
    deletions += file.deletions;
  }
  return {additions, deletions, unknown};
}
