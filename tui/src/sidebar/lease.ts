import {createHash, randomUUID} from 'node:crypto';
import {existsSync, mkdirSync, renameSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {readChannel} from './channel.js';
import {isProcessAlive} from './lifecycle.js';

type Holder = {pid: number; token: string};
export type SidebarLease = {release: () => void};

/** 完整的非空目录原子换入；退出代次保留，防止迟到清理删掉继任者。 */
export function claimSidebarLease(resource: string): SidebarLease | null {
  const key = createHash('sha256').update(resource).digest('hex');
  const root = path.join(tmpdir(), 'mini-harness-sidebar', 'leases', key);
  const active = path.join(root, 'active');
  const token = randomUUID();
  const candidate = path.join(root, `candidate-${token}`);
  mkdirSync(candidate, {recursive: true, mode: 0o700});
  const holder: Holder = {pid: process.pid, token};
  const read = (): Holder | null => {
    const value = readChannel(path.join(active, 'holder.json'));
    return value && Number.isSafeInteger(value.pid) && Number(value.pid) > 0 && typeof value.token === 'string' &&
      /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value.token) ? value as Holder : null;
  };
  const retire = (expected: Holder) => {
    if (read()?.token !== expected.token) return;
    try {
      // 同代只能移走一次；目标非空，第二个清理者无法覆盖它。
      renameSync(active, path.join(root, `retired-${expected.token}`));
    } catch (error) {
      if (!['ENOENT', 'EEXIST', 'ENOTEMPTY'].includes((error as NodeJS.ErrnoException).code ?? '')) throw error;
    }
  };
  try {
    writeFileSync(path.join(candidate, 'holder.json'), JSON.stringify(holder), {mode: 0o600, flag: 'wx'});
    for (let attempt = 0; attempt < 4; attempt++) {
      if (existsSync(active) && !read()) return null;
      try {
        renameSync(candidate, active);
        let released = false;
        return {release: () => {if (!released) {released = true; retire(holder);}}};
      } catch (error) {
        if (!['EEXIST', 'ENOTEMPTY'].includes((error as NodeJS.ErrnoException).code ?? '')) throw error;
      }
      const previous = read();
      if (!previous || isProcessAlive(previous.pid)) return null;
      retire(previous);
    }
    return null;
  } finally {rmSync(candidate, {recursive: true, force: true});}
}
