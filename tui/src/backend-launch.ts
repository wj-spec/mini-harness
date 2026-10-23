import path from 'node:path';
import {fileURLToPath} from 'node:url';

export interface BackendLaunchOptions {
  repoRoot?: string;
}

export const backendCommand = './run_vibe.sh';
const defaultRepoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

export function backendLaunchOptions(options: BackendLaunchOptions = {}) {
  return {
    cwd: options.repoRoot ?? defaultRepoRoot,
    env: process.env,
  };
}
