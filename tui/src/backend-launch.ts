import path from 'node:path';
import {fileURLToPath} from 'node:url';

export interface BackendLaunchOptions {
  repoRoot?: string;
  python?: string;
}

export const backendCommand = './run_vibe.sh';
const defaultRepoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

export function backendLaunchOptions(options: BackendLaunchOptions = {}) {
  return {
    cwd: options.repoRoot ?? defaultRepoRoot,
    env: options.python ? {...process.env, PYTHON: options.python} : process.env,
  };
}
