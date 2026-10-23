import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdir, mkdtemp, readFile, readdir, rm, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';

const cli = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src/cli.js');
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

test('help works without a TTY and documents commands and the Ink entry', () => {
  const result = spawnSync(process.execPath, [cli, '--help'], {encoding: 'utf8', timeout: 10000});
  assert.equal(result.status, 0);
  assert.match(result.stdout, /--fake/);
  assert.match(result.stdout, /--test-session/);
  assert.match(result.stdout, /--fake[^\n]*测试会话/);
  assert.match(result.stdout, /普通[^\n]*测试会话/);
  assert.match(result.stdout, /MCP/);
  for(const feature of ['工作台','Ctrl+J','PageUp','Ctrl+O','Ctrl+K','/context','/theme','/help','/cost','--continue','--session','--list-sessions','/resume','/permissions reset','/diff','/mcp','/skills','/session','/followup','./run_ink.sh','./run_vibe.sh --eval'])assert.ok(result.stdout.includes(feature),feature);
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

test('--session accepts an exact saved session and keeps the interactive terminal requirement', () => {
  const result = spawnSync(process.execPath, [cli, '--session', 'saved-session', '--fake'], {encoding: 'utf8', timeout: 10000});
  assert.equal(result.status, 2);
  assert.match(result.stderr, /需要交互式终端/);
  assert.doesNotMatch(result.stderr, /不支持参数/);
});

test('explicit test-session scope is forwarded without bypassing the interactive terminal requirement', () => {
  const result = spawnSync(process.execPath, [cli, '--session', 'saved-test', '--test-session'], {encoding: 'utf8', timeout: 10000});
  assert.equal(result.status, 2);
  assert.match(result.stderr, /需要交互式终端/);
  assert.doesNotMatch(result.stderr, /不支持参数/);
});

test('session listing forwards plain output and exit code through the configured Python launcher', {skip:process.platform==='win32'}, async () => {
  const base=path.join(repoRoot,'.vibe','node-cli-tests');await mkdir(base,{recursive:true});
  const fixture=await mkdtemp(path.join(base,'list-launcher-'));
  const python=path.join(fixture,'python');
  await writeFile(python, `#!/usr/bin/env bash
if [ "$1" != "-m" ] || [ "$2" != "app" ] || [ "$3" != "--list-sessions" ] || [ "$4" != "--workspace" ] || [ "$5" != "a path with spaces" ]; then exit 91; fi
printf 'fixture-session\\t2\\tfixture title\\n'
printf 'fixture diagnostic\\n' >&2
exit 7
`,{mode:0o755});
  try {
    const result=spawnSync(process.execPath,[cli,'--list-sessions','--workspace','a path with spaces'],{encoding:'utf8',timeout:10000,env:{...process.env,PYTHON:python}});
    assert.equal(result.status,7,result.stderr);
    assert.equal(result.stdout,'fixture-session\t2\tfixture title\n');
    assert.equal(result.stderr,'fixture diagnostic\n');
  } finally {await rm(fixture,{recursive:true,force:true});}
});

test('session listing works without TTY, API credentials, or MCP startup and leaves the log unchanged', {skip:!existsSync(path.join(repoRoot,'.venv','bin','python3'))}, async () => {
  const base=path.join(repoRoot,'.vibe','node-cli-tests');await mkdir(base,{recursive:true});
  const workspace=await mkdtemp(path.join(base,'list-workspace-'));
  const session='saved-conversation';
  const root=path.join(workspace,'.vibe','sessions',session);
  await mkdir(root,{recursive:true});
  const log=[{type:'message',ts:'2026-10-21T01:00:00Z',message:{role:'user',content:'恢复更早的编码任务'}},{type:'message',ts:'2026-10-21T01:01:00Z',message:{role:'assistant',content:'历史回复'}}].map(item=>JSON.stringify(item)).join('\n')+'\n';
  await writeFile(path.join(root,'events.jsonl'),log);
  try {
    const result=spawnSync(process.execPath,[cli,'--list-sessions','--workspace',workspace],{encoding:'utf8',timeout:10000,env:{...process.env,PYTHON:path.join(repoRoot,'.venv','bin','python3'),VIBE_API_KEY:'',OPENAI_API_KEY:'',DEEPSEEK_API_KEY:'',VIBE_MCP_SERVERS:'{invalid config',VIBE_MCP_CONFIG:''}});
    assert.equal(result.status,0,result.stderr);
    assert.match(result.stdout,/saved-conversation\tuser\t2\t/);
    assert.match(result.stdout,/恢复更早的编码任务/);
    assert.equal(result.stderr,'');
    assert.deepEqual(await readdir(path.join(workspace,'.vibe','sessions')),[session]);
    assert.deepEqual(await readdir(root),['events.jsonl']);
    assert.equal(await readFile(path.join(root,'events.jsonl'),'utf8'),log);
  } finally {await rm(workspace,{recursive:true,force:true});}
});
