import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdir, mkdtemp, rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';

const cli = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src/cli.js');
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

test('help works without a TTY and documents parity features while retaining Textual default', () => {
  const result = spawnSync(process.execPath, [cli, '--help'], {encoding: 'utf8', timeout: 10000});
  assert.equal(result.status, 0);
  assert.match(result.stdout, /--fake/);
  assert.match(result.stdout, /MCP/);
  for(const feature of ['工作台','Ctrl+J','PageUp','Ctrl+O','/theme','/help','/cost','--continue','/resume','/permissions reset','/diff','/mcp','Textual','超越'])assert.ok(result.stdout.includes(feature),feature);
  assert.equal(result.stderr, '');
});

test('interactive CLI enters and restores the alternate screen', {skip:process.platform==='win32'||!existsSync(path.join(repoRoot,'.venv','bin','python3'))}, async()=>{
  const base=path.join(repoRoot,'.vibe','node-cli-tests');await mkdir(base,{recursive:true});
  const workspace=await mkdtemp(path.join(base,'workspace-'));
  const script=`import errno, fcntl, json, os, pty, select, struct, subprocess, sys, termios, time
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 110, 0, 0))
env = {**os.environ, 'TERM': 'xterm-256color', 'CI': '', 'VIBE_MCP_CONFIG': '', 'VIBE_MCP_SERVERS': ''}
process = subprocess.Popen([sys.argv[1], sys.argv[2], '--fake', '--workspace', sys.argv[3]], stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True)
os.close(slave)
output = bytearray()
started = time.monotonic()
sent = False
try:
    while time.monotonic()-started < 10:
        readable, _, _ = select.select([master], [], [], 0.05)
        if readable:
            try:
                chunk = os.read(master, 65536)
                if not chunk: break
                output.extend(chunk)
            except OSError as error:
                if error.errno == errno.EIO: break
                raise
        if not sent and time.monotonic()-started > 0.6:
            os.write(master, b'\\x11')
            sent = True
        if process.poll() is not None:
            while select.select([master], [], [], 0)[0]:
                try:
                    chunk=os.read(master,65536)
                    if not chunk: break
                    output.extend(chunk)
                except OSError: break
            break
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.kill()
    code = process.wait(timeout=3)
    print(json.dumps({'entered': b'\\x1b[?1049h' in output, 'restored': b'\\x1b[?1049l' in output, 'exit': code}))
finally:
    os.close(master)
    if process.poll() is None:
        process.kill()
        process.wait()
`;
  try{
    const result=spawnSync(path.join(repoRoot,'.venv','bin','python3'),['-c',script,process.execPath,cli,workspace],{encoding:'utf8',timeout:20000});
    assert.equal(result.status,0,result.stderr);
    const terminal=JSON.parse(result.stdout);
    assert.equal(terminal.entered,true);
    assert.equal(terminal.restored,true);
    assert.equal(terminal.exit,0);
  }finally{await rm(workspace,{recursive:true,force:true});}
});

test('noninteractive invocation gives a clear terminal requirement', () => {
  const result = spawnSync(process.execPath, [cli, '--fake'], {encoding: 'utf8', timeout: 10000});
  assert.equal(result.status, 2);
  assert.match(result.stderr, /需要交互式终端/);
  assert.doesNotMatch(result.stderr, /Raw mode/);
});

test('unsupported migration flags are rejected before starting Python', () => {
  const result = spawnSync(process.execPath, [cli, '--eval'], {encoding: 'utf8', timeout: 10000});
  assert.equal(result.status, 2);
  assert.match(result.stderr, /不支持参数 --eval/);
});

test('--continue is supported and still requires an interactive terminal', () => {
  const result = spawnSync(process.execPath, [cli, '--continue', '--fake'], {encoding: 'utf8', timeout: 10000});
  assert.equal(result.status, 2);
  assert.match(result.stderr, /需要交互式终端/);
  assert.doesNotMatch(result.stderr, /不支持参数/);
});
