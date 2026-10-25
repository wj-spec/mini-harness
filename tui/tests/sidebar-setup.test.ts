import assert from 'node:assert/strict';
import {existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import test from 'node:test';

const script = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'scripts', 'setup-ghostty-sidebar.sh');

function tempHome(): string {
  return mkdtempSync(path.join(tmpdir(), 'mini-harness-ghostty-'));
}

function env(home: string): NodeJS.ProcessEnv {
  return {...process.env, HOME: home, XDG_CONFIG_HOME: ''};
}

const skip = process.platform === 'win32' ? 'needs bash' : false;

test('setup script dry-run prints the right-side block without writing', {skip}, () => {
  const home = tempHome();
  try {
    const result = spawnSync('bash', [script, '--dry-run'], {encoding: 'utf8', env: env(home)});
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /quick-terminal-position = right/u);
    assert.match(result.stdout, /quick-terminal-size = 420px/u);
    assert.equal(existsSync(path.join(home, '.config/ghostty/config')), false);
  } finally {
    rmSync(home, {recursive: true, force: true});
  }
});

test('setup script writes an idempotent right-side configuration', {skip}, () => {
  const home = tempHome();
  const config = path.join(home, '.config/ghostty/config');
  try {
    const first = spawnSync('bash', [script], {encoding: 'utf8', env: env(home)});
    assert.equal(first.status, 0, first.stderr);
    const content = readFileSync(config, 'utf8');
    assert.match(content, /quick-terminal-position = right/u);
    assert.match(content, /quick-terminal-autohide = false/u);
    const second = spawnSync('bash', [script], {encoding: 'utf8', env: env(home)});
    assert.equal(second.status, 0, second.stderr);
    assert.match(second.stdout, /已配置为右侧/u);
    assert.equal(readFileSync(config, 'utf8'), content);
  } finally {
    rmSync(home, {recursive: true, force: true});
  }
});

test('setup script refuses to overwrite an existing non-right position', {skip}, () => {
  const home = tempHome();
  const config = path.join(home, '.config/ghostty/config');
  try {
    mkdirSync(path.dirname(config), {recursive: true});
    writeFileSync(config, 'quick-terminal-position = top\n');
    const result = spawnSync('bash', [script], {encoding: 'utf8', env: env(home)});
    assert.equal(result.status, 1);
    assert.match(result.stderr, /改为 right/u);
    assert.equal(readFileSync(config, 'utf8'), 'quick-terminal-position = top\n');
  } finally {
    rmSync(home, {recursive: true, force: true});
  }
});
