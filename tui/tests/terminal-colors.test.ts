import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {test} from 'node:test';

function colorPolicy(overrides: Record<string, string>, tty = false) {
  const env = {...process.env};
  for (const key of ['NO_COLOR', 'FORCE_COLOR', 'COLORTERM', 'TERM', 'TERM_PROGRAM', 'TERM_PROGRAM_VERSION', 'CI', 'TF_BUILD', 'AGENT_NAME', 'TEAMCITY_VERSION']) delete env[key];
  Object.assign(env, overrides);
  const moduleUrl = new URL('../src/terminal-colors.js', import.meta.url).href;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
    import tty from 'node:tty';
    if (${tty}) tty.isatty = () => true;
    const {terminalColorLevel} = await import(${JSON.stringify(moduleUrl)});
    const {default: chalk} = await import('chalk');
    process.stdout.write(JSON.stringify({level: terminalColorLevel(), inkLevel: chalk.level, sample: chalk.bgHex('#eef1f5')('sample')}));
  `], {cwd: new URL('..', import.meta.url), env, encoding: 'utf8'});
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout) as {level: number; inkLevel: number; sample: string};
}

test('terminal palette and Ink use the same color level, including explicit forced levels', () => {
  for (const level of [0, 1, 2, 3]) {
    const result = colorPolicy({FORCE_COLOR: String(level)});
    assert.equal(result.level, level);
    assert.equal(result.inkLevel, level);
  }
});

test('nonempty NO_COLOR disables Ink colors while explicit FORCE_COLOR keeps precedence', () => {
  const disabled = colorPolicy({TERM: 'xterm-256color', NO_COLOR: '1'}, true);
  assert.equal(disabled.level, 0);
  assert.equal(disabled.inkLevel, 0);
  assert.equal(disabled.sample, 'sample');
  assert.equal(colorPolicy({NO_COLOR: '1', FORCE_COLOR: '2'}).level, 2);
  assert.equal(colorPolicy({TERM: 'xterm-256color', NO_COLOR: ''}, true).level, 2);
});

test('automatic detection preserves Apple Terminal 256 colors instead of assuming truecolor', () => {
  const apple = colorPolicy({TERM: 'xterm-256color', TERM_PROGRAM: 'Apple_Terminal'}, true);
  assert.equal(apple.level, 2);
  assert.equal(apple.inkLevel, 2);
  assert.equal(colorPolicy({TERM: 'xterm-256color', COLORTERM: 'truecolor'}, true).level, 3);
  assert.equal(colorPolicy({TERM: 'xterm-256color', TERM_PROGRAM: 'iTerm.app', TERM_PROGRAM_VERSION: '3.5'}, true).level, 3);
  assert.equal(colorPolicy({TERM: 'xterm-256color'}, false).level, 0);
});
