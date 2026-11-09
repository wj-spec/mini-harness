import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spawn, spawnSync, type ChildProcess} from 'node:child_process';
import {existsSync, mkdtempSync, mkdirSync, readdirSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {setTimeout as pause} from 'node:timers/promises';
import {fileURLToPath} from 'node:url';

const host = new URL('../src/sidebar/host.js', import.meta.url).href;
const repo = fileURLToPath(new URL('../../../', import.meta.url));
const python = path.join(repo, '.venv/bin/python3');

test('sidebar CLI shares its startup reservation across relative and absolute workspace paths',
  {skip: process.platform === 'win32' || !existsSync(python)}, () => {
    // 暂停首个 stdout 输出，让第二个 CLI 在首个登记完成前竞争。
    const barrier = `import {existsSync, writeFileSync} from 'node:fs';
const original = process.stdout.write.bind(process.stdout);
process.stdout.write = (...args) => {
  process.stdout.write = original;
  writeFileSync(process.env.SIDEBAR_TEST_ENTERED, 'entered');
  const deadline = Date.now() + 10000;
  while (!existsSync(process.env.SIDEBAR_TEST_RELEASE) && Date.now() < deadline)
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
  return original(...args);
};`;
    const script = `import fcntl, json, os, pty, struct, subprocess, sys, tempfile, termios, time, uuid
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='sidebar-cli-race-') as directory:
    root = Path(directory).resolve()
    (root/'barrier.mjs').write_text(sys.argv[3])
    (root/'tmp').mkdir()
    owner = str(uuid.uuid4())
    children = []
    def start(name, workspace):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
        env = dict(os.environ, TERM='xterm-256color', TERM_PROGRAM='vibe-pty', GHOSTTY_RESOURCES_DIR='',
                   TMPDIR=str(root/'tmp'), SIDEBAR_TEST_ENTERED=str(root/name), SIDEBAR_TEST_RELEASE=str(root/'release'))
        process = subprocess.Popen([sys.argv[1], '--import', str(root/'barrier.mjs'), sys.argv[2],
            '--workspace', workspace, '--parent-pid', str(os.getpid()), '--instance-id', owner, '--refresh-ms', '0'],
            cwd=root, stdin=slave, stdout=slave, stderr=subprocess.PIPE, env=env)
        os.close(slave)
        children.append((process, master))
        return process, master
    try:
        first, _ = start('first', str(root))
        deadline = time.monotonic() + 6
        while not (root/'first').exists() and first.poll() is None and time.monotonic() < deadline: time.sleep(.01)
        assert (root/'first').exists(), 'first CLI did not reach terminal output'
        second, terminal = start('second', '.')
        deadline = time.monotonic() + 6
        while second.poll() is None and not (root/'second').exists() and time.monotonic() < deadline: time.sleep(.01)
        output = second.stderr.read() if second.poll() is not None else b''
        print(json.dumps({'exit': second.poll(), 'duplicate_rendered': (root/'second').exists(),
                          'original_alive': first.poll() is None, 'output': output.decode(errors='replace')}))
    finally:
        for process, terminal in children:
            if process.poll() is None: process.kill()
            process.wait(timeout=3)
            os.close(terminal)
`;
    const result = spawnSync(python, ['-c', script, process.execPath,
      fileURLToPath(new URL('../src/sidebar/cli.js', import.meta.url)), barrier], {encoding: 'utf8', timeout: 20000});
    assert.equal(result.status, 0, result.stderr);
    const outcome = JSON.parse(result.stdout);
    assert.equal(outcome.exit, 2, 'the duplicate must exit before either CLI registers');
    assert.equal(outcome.duplicate_rendered, false);
    assert.equal(outcome.original_alive, true);
    assert.match(outcome.output, /已有侧栏/);
  });
const childCode = `
import {openSidebar} from ${JSON.stringify(host)};
import {existsSync, writeFileSync} from 'node:fs';
const options = JSON.parse(process.argv[1]);
process.on('message', () => {
  const result = openSidebar({...options, platform: 'darwin', env: {TERM_PROGRAM: 'ghostty'}, ghosttyBinary: null,
    run: () => {
      writeFileSync(options.marker, String(process.pid));
      const deadline = Date.now() + 8000;
      while (!existsSync(options.release) && Date.now() < deadline) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
      return {status: 0, stdout: options.mode === 'window' ? 'started:window-test' : 'started', stderr: ''};
    }});
  process.send(result, () => process.disconnect());
});
process.send('ready');
`;

async function contender(root: string, name: string, mode: string, instanceId = randomUUID()) {
  const marker = path.join(root, 'entered', name);
  const child = spawn(process.execPath, ['--input-type=module', '-e', childCode, JSON.stringify({workspace: root, cliPath: '/test/sidebar.js',
    owner: mode === 'window' ? {parentPid: process.pid, instanceId} : {instanceId, parentPid: process.pid}, mode, marker, release: path.join(root, 'release')})],
  {env: {...process.env, TMPDIR: path.join(root, 'tmp')}, stdio: ['ignore', 'pipe', 'pipe', 'ipc']});
  let errors = ''; child.stderr!.on('data', chunk => {errors += String(chunk);});
  const result = new Promise<{outcome: string; message: string}>((resolve, reject) => {
    child.on('message', value => {if (value !== 'ready') resolve(value as {outcome: string; message: string});});
    child.on('error', reject); child.on('exit', code => {if (code !== 0) reject(new Error(`child exited ${code}: ${errors}`));});
  });
  await new Promise<void>((resolve, reject) => {child.once('message', () => resolve()); child.once('error', reject);});
  return {child, result};
}
function fixture() {
  const root = mkdtempSync(path.join(tmpdir(), 'sidebar-launch-'));
  mkdirSync(path.join(root, 'entered')); mkdirSync(path.join(root, 'tmp'));
  return root;
}
async function stop(child: ChildProcess) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise<void>(resolve => child.once('exit', () => resolve())); child.kill('SIGTERM'); await exited;
}
async function until(check: () => boolean) {
  const deadline = Date.now() + 4000;
  while (!check() && Date.now() < deadline) await pause(10);
  assert.ok(check(), 'concurrent host action did not reach the expected state');
}
async function race(root: string, children: Awaited<ReturnType<typeof contender>>[]) {
  let completed = 0;
  for (const item of children) {void item.result.then(() => {completed++;}); item.child.send('go');}
  await until(() => completed === children.length - 1 || readdirSync(path.join(root, 'entered')).length > 1);
}

test('simultaneous quick launches from different sessions allow only one host action', async () => {
  const root = fixture(); const children = await Promise.all([contender(root, 'a', 'quick'), contender(root, 'b', 'quick')]);
  try {
    await race(root, children);
    const entered = readdirSync(path.join(root, 'entered'));
    writeFileSync(path.join(root, 'release'), 'go');
    const results = await Promise.all(children.map(item => item.result));
    assert.equal(entered.length, 1, 'only the winner may inspect or toggle the shared quick terminal');
    assert.equal(results.filter(result => result.outcome === 'started').length, 1);
    assert.match(results.find(result => result.outcome === 'failed')!.message, /正在|占用/);
  } finally {writeFileSync(path.join(root, 'release'), 'go'); await Promise.all(children.map(item => stop(item.child))); rmSync(root, {recursive: true, force: true});}
});

test('the same workbench cannot race a split launch against a window launch', async () => {
  const root = fixture(); const owner = randomUUID();
  const children = await Promise.all([contender(root, 'split', 'split', owner), contender(root, 'window', 'window', owner)]);
  try {
    await race(root, children);
    const entered = readdirSync(path.join(root, 'entered'));
    writeFileSync(path.join(root, 'release'), 'go');
    const results = await Promise.all(children.map(item => item.result));
    assert.equal(entered.length, 1, 'one workbench must not create two panes concurrently');
    assert.equal(results.filter(result => result.outcome === 'started').length, 1);
    assert.match(results.find(result => result.outcome === 'failed')!.message, /正在|占用/);
  } finally {writeFileSync(path.join(root, 'release'), 'go'); await Promise.all(children.map(item => stop(item.child))); rmSync(root, {recursive: true, force: true});}
});

test('different workbenches can open independent splits at the same time', async () => {
  const root = fixture(); const children = await Promise.all([contender(root, 'a', 'split'), contender(root, 'b', 'split')]);
  try {
    for (const item of children) item.child.send('go');
    await until(() => readdirSync(path.join(root, 'entered')).length === 2);
    writeFileSync(path.join(root, 'release'), 'go');
    assert.ok((await Promise.all(children.map(item => item.result))).every(result => result.outcome === 'started'));
  } finally {writeFileSync(path.join(root, 'release'), 'go'); await Promise.all(children.map(item => stop(item.child))); rmSync(root, {recursive: true, force: true});}
});

test('concurrent recovery after a killed quick launcher preserves the new winner', async () => {
  const root = fixture(); const original = await contender(root, 'original', 'quick');
  const originalResult = original.result.catch(() => null);
  const children: Awaited<ReturnType<typeof contender>>[] = [];
  try {
    original.child.send('go'); await until(() => readdirSync(path.join(root, 'entered')).length === 1);
    const exited = new Promise<void>(resolve => original.child.once('exit', () => resolve()));
    original.child.kill('SIGKILL'); await exited; await originalResult;
    rmSync(path.join(root, 'entered', 'original'));
    children.push(...await Promise.all(Array.from({length: 6}, (_, i) => contender(root, `recovery-${i}`, 'quick'))));
    await race(root, children);
    assert.equal(readdirSync(path.join(root, 'entered')).length, 1);
    const late = await contender(root, 'late', 'quick'); children.push(late); late.child.send('go');
    assert.equal((await late.result).outcome, 'failed', 'late cleanup must not remove the live successor');
    writeFileSync(path.join(root, 'release'), 'go');
    const results = await Promise.all(children.map(item => item.result));
    assert.equal(results.filter(result => result.outcome === 'started').length, 1);
  } finally {writeFileSync(path.join(root, 'release'), 'go'); await stop(original.child); await Promise.all(children.map(item => stop(item.child))); rmSync(root, {recursive: true, force: true});}
});
