import assert from 'node:assert/strict';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import test from 'node:test';
import {
  SIDEBAR_TITLE, checkQuickTerminalPosition, detectHost, ghosttyBinary, ghosttyScript, openSidebar, shellQuote, sidebarCommand, splitScript,
  type Runner,
} from '../src/sidebar/host.js';
import {sidebarCliPath} from '../src/sidebar/launch.js';

test('terminal host detection distinguishes Ghostty, Apple Terminal and unknown hosts', () => {
  assert.equal(detectHost({TERM_PROGRAM: 'ghostty'}), 'ghostty');
  assert.equal(detectHost({GHOSTTY_RESOURCES_DIR: '/Applications/Ghostty.app'}), 'ghostty');
  assert.equal(detectHost({TERM_PROGRAM: 'Apple_Terminal'}), 'terminal');
  assert.equal(detectHost({TERM_PROGRAM: 'iTerm.app'}), 'unknown');
  assert.equal(detectHost({}), 'unknown');
});

test('shell quoting keeps safe paths and escapes spaces and quotes', () => {
  assert.equal(shellQuote('/repo'), '/repo');
  assert.equal(shellQuote('/my repo'), `'/my repo'`);
  assert.equal(shellQuote("/it's"), `'/it'\\''s'`);
  assert.equal(shellQuote('/tmp/a.b+c'), '/tmp/a.b+c');
});

test('sidebar command changes directory, execs the entrypoint and links the parent pid', () => {
  const command = sidebarCommand({workspace: '/my repo', cliPath: '/app/dist/src/sidebar/cli.js', parentPid: 4242});
  assert.match(command, /^cd '\/my repo' && exec node \/app\/dist\/src\/sidebar\/cli\.js --workspace '\/my repo' --parent-pid 4242$/u);
  assert.doesNotMatch(sidebarCommand({workspace: '/repo', cliPath: '/app/sidebar.js'}), /--parent-pid/u);
  const cli = sidebarCliPath('/app/dist/src/cli.js');
  assert.equal(cli, path.resolve('/app/dist/src/sidebar/cli.js'));
});

test('split mode uses a right-hand split inside the current window and focuses an existing sidebar', () => {
  const script = splitScript('cd /repo && exec node /app/sidebar.js', '/repo');
  assert.match(script, /new surface configuration/u);
  assert.match(script, /set command of cfg to/u);
  assert.match(script, /split target direction right with configuration cfg/u);
  assert.match(script, /focus \(item 1 of sidebarHits\)/u);
  assert.match(script, /return "focused"/u);
  const calls: string[][] = [];
  const run: Runner = (command, args) => {
    calls.push([command, ...args]);
    return {status: 0, stdout: 'started\n', stderr: ''};
  };
  const result = openSidebar({workspace: '/repo', cliPath: '/app/sidebar.js', env: {TERM_PROGRAM: 'ghostty'}, platform: 'darwin',
    mode: 'split', run});
  assert.equal(result.outcome, 'started');
  assert.equal(calls.length, 1, 'split mode must not run the quick terminal preflight');
  assert.match(calls[0]![2]!, /split target direction right/u);
});

test('Ghostty AppleScript detects an existing sidebar, toggles it and starts a new one', () => {
  const script = ghosttyScript('cd /repo && exec node /app/sidebar.js --workspace /repo');
  assert.match(script, /toggle_quick_terminal/u);
  assert.match(script, new RegExp(SIDEBAR_TITLE, 'u'));
  assert.match(script, /input text "cd \/repo/u);
  assert.match(script, /send key "enter"/u);
  assert.match(script, /return "started"/u);
  assert.match(script, /return "toggled"/u);
  assert.match(script, /return "opened"/u);
});

test('openSidebar maps Ghostty AppleScript markers to user-facing outcomes', () => {
  const makeRun = (status: number, stdout: string, stderr = ''): Runner => () => ({status, stdout, stderr});
  const base = {workspace: '/repo', cliPath: '/app/sidebar.js', env: {TERM_PROGRAM: 'ghostty'}, platform: 'darwin' as const, ghosttyBinary: null};
  for (const [marker, outcome] of [['started', 'started'], ['toggled', 'toggled'], ['opened', 'opened']] as const) {
    const result = openSidebar({...base, run: makeRun(0, `${marker}\n`)});
    assert.equal(result.outcome, outcome);
  }
  const failed = openSidebar({...base, run: makeRun(0, 'action-failed\n')});
  assert.equal(failed.outcome, 'failed');
  assert.match(failed.message, /quick terminal/u);
});

test('quick terminal position is read from Ghostty show-config with top as the default', () => {
  assert.deepEqual(
    checkQuickTerminalPosition({binary: '/fake/ghostty', run: () => ({status: 0, stdout: 'quick-terminal-position = right\n', stderr: ''})}),
    {checked: true, ok: true, position: 'right', binary: '/fake/ghostty'},
  );
  assert.equal(checkQuickTerminalPosition({binary: '/fake/ghostty', run: () => ({status: 0, stdout: '', stderr: ''})}).position, 'top');
  assert.equal(checkQuickTerminalPosition({binary: '/fake/ghostty', run: () => ({status: 1, stdout: '', stderr: 'boom'})}).checked, false);
  assert.equal(checkQuickTerminalPosition({binary: null}).checked, false);
  const discovered = ghosttyBinary({GHOSTTY_RESOURCES_DIR: '/App/Ghostty.app/Contents/Resources'}, candidate => candidate.endsWith('Ghostty.app/Contents/MacOS/ghostty'));
  assert.equal(discovered, '/App/Ghostty.app/Contents/MacOS/ghostty');
  assert.equal(ghosttyBinary({}, () => false), null);
});

test('openSidebar refuses a top-positioned quick terminal and points at /sidebar setup', () => {
  const calls: string[][] = [];
  const run: Runner = (command, args) => {
    calls.push([command, ...args]);
    if (args[0] === '+show-config') return {status: 0, stdout: 'quick-terminal-position = top\n', stderr: ''};
    return {status: 0, stdout: 'started\n', stderr: ''};
  };
  const result = openSidebar({workspace: '/repo', cliPath: '/app/sidebar.js', env: {TERM_PROGRAM: 'ghostty'}, platform: 'darwin',
    ghosttyBinary: '/fake/ghostty', run});
  assert.equal(result.outcome, 'misconfigured');
  assert.equal(calls.length, 1, 'must not open the quick terminal when it would appear at the top');
  assert.match(result.message, /sidebar setup/u);
});

test('openSidebar proceeds once quick-terminal-position is right', () => {
  const calls: string[][] = [];
  const run: Runner = (command, args) => {
    calls.push([command, ...args]);
    if (args[0] === '+show-config') return {status: 0, stdout: 'quick-terminal-position = right\n', stderr: ''};
    return {status: 0, stdout: 'started\n', stderr: ''};
  };
  const result = openSidebar({workspace: '/repo', cliPath: '/app/sidebar.js', env: {TERM_PROGRAM: 'ghostty'}, platform: 'darwin',
    ghosttyBinary: '/fake/ghostty', run});
  assert.equal(result.outcome, 'started');
  assert.ok(calls.some(call => call.includes('+show-config')));
  assert.ok(calls.some(call => call[0] === 'osascript'));
});

test('automatic sidebars explain unsupported hosts and macOS automation permissions', () => {
  const unsupportedTerminal = openSidebar({workspace: '/repo', cliPath: '/app/sidebar.js', env: {TERM_PROGRAM: 'Apple_Terminal'}, platform: 'darwin'});
  assert.equal(unsupportedTerminal.outcome, 'unsupported');
  assert.match(unsupportedTerminal.message, /macOS Terminal/u);
  assert.match(unsupportedTerminal.message, /请先手动运行/u);
  const otherPlatform = openSidebar({workspace: '/repo', cliPath: '/app/sidebar.js', env: {TERM_PROGRAM: 'ghostty'}, platform: 'linux'});
  assert.equal(otherPlatform.outcome, 'unsupported');
  const denied = openSidebar({workspace: '/repo', cliPath: '/app/sidebar.js', env: {TERM_PROGRAM: 'ghostty'}, platform: 'darwin', ghosttyBinary: null,
    run: () => ({status: 1, stdout: '', stderr: 'execution error: Not authorized to send Apple events to Ghostty. (-1743)'})});
  assert.equal(denied.outcome, 'failed');
  assert.match(denied.message, /自动化权限/u);
});

test('generated AppleScript compiles with the macOS scripting compiler', {skip: process.platform !== 'darwin' || !hasOsacompile()}, () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'mini-harness-sidebar-'));
  try {
    for (const [name, script] of [['quick', ghosttyScript('echo hello')], ['split', splitScript('echo hello', '/repo')]] as const) {
      const output = path.join(directory, `${name}.scpt`);
      const result = spawnSync('osacompile', ['-o', output, '-e', script], {encoding: 'utf8'});
      assert.equal(result.status, 0, `${name}: ${result.stderr}`);
    }
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
});

function hasOsacompile(): boolean {
  const result = spawnSync('osacompile', ['-o', path.join(tmpdir(), 'mini-harness-sidebar-probe.scpt'), '-e', 'return 1'], {encoding: 'utf8'});
  if (result.status === 0) {
    rmSync(path.join(tmpdir(), 'mini-harness-sidebar-probe.scpt'), {force: true});
    return true;
  }
  return false;
}
