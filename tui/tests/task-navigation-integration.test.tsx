import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as pause} from 'node:timers/promises';
import {test} from 'node:test';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import {createBackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
async function waitFor(check: () => boolean) {
  const until = Date.now() + 10000;
  while (!check()) {if (Date.now() > until) throw new Error('navigation timeout'); await pause(20);}
}
test('real backend task selection reads only the selected history with no new run', async () => {
  const workspace = await mkdtemp(path.join(tmpdir(), 'task-navigation-'));
  const fixture = JSON.parse(execFileSync(path.join(root, '.venv/bin/python3'), ['-c', `
import json,sys
from pathlib import Path
from app.session.store import SessionStore
from app.runtime.tasks import TaskStore
from app.runtime.inspection import InspectionStore
from core.contracts import Message
workspace=Path(sys.argv[1]);session=SessionStore.new(workspace,'fake',purpose='test')
session.append_message(Message('user','导航的旧会话'))
tasks=TaskStore(session.root);inspections=InspectionStore(session.root,workspace)
ids=[]
for index in range(7):
 task=tasks.create(kind='run',objective=f'原任务 {index}',workspace=str(workspace));ids.append(task.task_id)
 inspections.start(f'original-{index}',task.objective,task={'task_id':task.task_id,'segment':1,'status':'completed'}).finish({'status':'done','text':'历史已结束'})
print(json.dumps({'session':session.id,'ids':ids}))
`, workspace], {cwd: root, encoding: 'utf8'}));
  const client = createBackendClient({repoRoot: root, args: ['--fake', '--workspace', workspace, '--session', fixture.session]});
  const events: ServerEvent[] = []; client.subscribe(event => events.push(event));
  const view = render(<App client={client} windowSize={{columns: 100, rows: 30}} publishSidebarSignal={() => {}}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(35);};
  const click = async (label: string) => {
    await waitFor(() => Boolean(view.lastFrame()?.includes(label))); await pause(30);
    const lines = view.lastFrame()!.split('\n'); const index = lines.findIndex(line => line.includes(label));
    const column = stringWidth(lines[index]!.slice(0, lines[index]!.indexOf(label))) + 2;
    await key(`\x1b[<0;${column};${index + 1}M\x1b[<0;${column};${index + 1}m`);
  };
  try {
    client.start(); await waitFor(() => events.some(event => event.type === 'status' && event.phase === 'idle'));
    await key('保留中文草稿'); await key('\x1b[D'); await click('[ 任务 ]'); await waitFor(() => Boolean(view.lastFrame()?.includes('原任务 6')));
    await click('[ 更早 ]'); await waitFor(() => Boolean(view.lastFrame()?.includes('原任务 1'))); await click('[ 后条 ]');
    assert.match(view.lastFrame()!, /原任务 0/); await click('[ 查看记录 ]');
    await waitFor(() => events.some(event => event.type === 'inspection_history'));
    const history = events.find(event => event.type === 'inspection_history');
    assert.ok(history?.type === 'inspection_history'); assert.equal(history.task_id, fixture.ids[0]);
    assert.equal(history.records.length, 1); assert.equal(history.records[0]!.scope.requestId, 'original-0');
    assert.match(view.lastFrame()!, /原任务 0/); assert.doesNotMatch(view.lastFrame()!, /回执范围不匹配/);
    await click('[ 返回 ]'); await key('中'); assert.match(view.lastFrame()!, /保留中文草中稿/);
    assert.equal(events.filter(event => event.type === 'done' || event.type === 'approval_request').length, 0);
    const transcript = await readFile(path.join(workspace, '.vibe/sessions', fixture.session, 'events.jsonl'), 'utf8');
    assert.equal(transcript.split('\n').filter(line => line && JSON.parse(line).type === 'message').length, 1);
  } finally {await client.shutdown(); view.unmount(); cleanup(); await rm(workspace, {recursive: true, force: true});}
});
