const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const readline = require('node:readline');
const {test} = require('node:test');

test('controlled worker fills local controls and refuses stale refs and submissions',
  {skip: process.env.RUN_BROWSER_SMOKE !== '1', timeout: 120000}, async () => {
    const hits = [];
    const server = http.createServer((request, response) => {
      hits.push(request.url);
      if (request.url === '/redirect') {
        response.writeHead(302, {Location: 'http://127.0.0.1:1/forbidden'}).end();
        return;
      }
      if (request.url === '/secret-network') {
        response.writeHead(404, {'Content-Type': 'text/plain'}).end('secret-network');
        return;
      }
      response.writeHead(200, {'Content-Type': 'text/html'});
      response.end(`<!doctype html><title>Controlled fixture</title><h1>Fixture</h1>
        <p id="result">Ready</p><button type="button" onclick="result.textContent='Clicked'">Change</button>
        <label>Name<input id="name"></label><label>Color<select><option value="red">Red</option><option value="blue">Blue</option></select></label>
        <form action="/submitted" method="get"><button>Submit</button></form>
        <button type="button" onclick="document.querySelector('form').submit()">Script submit</button>
        <button type="button" onclick="fetch('/submitted?fetch=1')">Fetch submit</button>
        <button type="button" onclick="setTimeout(()=>location.reload(),150)">Reload later</button>
        <button type="button" onclick="setTimeout(()=>{const n=document.getElementById('name');n.replaceWith(n.cloneNode(true));},150)">Replace later</button>
        ${request.url === '/sensitive' ? `<input type="password" value="do-not-expose-this">
          <input autocomplete="cc-number" value="secret-card"><input autocomplete="one-time-code" value="secret-otp">
          <input type="email" value="secret-email@example.test"><select><option value="secret-option">Secret option</option></select>
          <script>console.error('secret-console');fetch('/secret-network')</script>` : ''}`);
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const worker = spawn(process.execPath, [path.resolve(__dirname, '../scripts/browser_worker.cjs')], {
      env: {...process.env, VIBE_BROWSER_ORIGINS: JSON.stringify([origin]), VIBE_BROWSER_NO_SANDBOX: '1'},
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stderr = '', serial = 0;
    const pending = new Map();
    worker.stderr.on('data', data => { stderr += data; });
    readline.createInterface({input: worker.stdout}).on('line', line => {
      const message = JSON.parse(line);
      const item = pending.get(message.id);
      if (!item) return;
      pending.delete(message.id);
      message.error ? item.reject(new Error(message.error)) : item.resolve(message.result);
    });
    worker.on('exit', code => {
      for (const item of pending.values()) item.reject(new Error(`worker exit ${code}: ${stderr}`));
      pending.clear();
    });
    function call(tool, args = {}) {
      const id = ++serial;
      return new Promise((resolve, reject) => {
        pending.set(id, {resolve, reject});
        worker.stdin.write(JSON.stringify({id, tool, args}) + '\n');
      });
    }
    try {
      for (const tool of ['browser_snapshot', 'browser_console_messages']) {
        await assert.rejects(call(tool), error => {
          assert.match(error.message, /browser_not_navigated/);
          assert.match(error.message, /about:blank/);
          assert.ok(error.message.includes(origin));
          return true;
        });
      }
      assert.equal(hits.length, 0);
      let snapshot = await call('browser_navigate', {url: origin + '/'});
      const original = snapshot;
      const ref = name => snapshot.elements.find(element => element.name === name).ref;
      snapshot = await call('browser_click', {snapshot_id: snapshot.snapshot_id, ref: ref('Change')});
      assert.equal(snapshot.changed, true);
      assert.match(snapshot.snapshot, /Clicked/);
      await assert.rejects(call('browser_click', {snapshot_id: original.snapshot_id, ref: original.elements.find(e => e.name === 'Change').ref}), /stale/);
      snapshot = await call('browser_snapshot');
      snapshot = await call('browser_type', {snapshot_id: snapshot.snapshot_id, ref: ref('Name'), text: 'Ada'});
      assert.match(snapshot.snapshot, /Ada/);
      snapshot = await call('browser_select_option', {snapshot_id: snapshot.snapshot_id, ref: ref('Color'), values: ['blue']});
      assert.equal(snapshot.elements.find(e => e.name === 'Color').options.find(o => o.value === 'blue').selected, true);
      await assert.rejects(call('browser_click', {snapshot_id: snapshot.snapshot_id, ref: ref('Submit')}), /submit/);
      snapshot = await call('browser_snapshot');
      await assert.rejects(call('browser_type', {snapshot_id: snapshot.snapshot_id, ref: ref('Name'), text: 'bad', submit: true}), /submit/);
      snapshot = await call('browser_snapshot');
      await assert.rejects(call('browser_click', {snapshot_id: snapshot.snapshot_id, ref: ref('Script submit')}), /navigation|blocked/);
      assert.equal(hits.includes('/submitted'), false);
      snapshot = await call('browser_navigate', {url: origin + '/'});
      await assert.rejects(call('browser_click', {snapshot_id: snapshot.snapshot_id, ref: ref('Fetch submit')}), /network.*blocked/);
      assert.equal(hits.some(url => url.startsWith('/submitted')), false);
      snapshot = await call('browser_navigate', {url: origin + '/'});
      snapshot = await call('browser_click', {snapshot_id: snapshot.snapshot_id, ref: ref('Reload later')});
      await new Promise(resolve => setTimeout(resolve, 250));
      await assert.rejects(call('browser_click', {snapshot_id: snapshot.snapshot_id, ref: ref('Change')}), /stale/);
      snapshot = await call('browser_navigate', {url: origin + '/'});
      snapshot = await call('browser_click', {snapshot_id: snapshot.snapshot_id, ref: ref('Replace later')});
      await new Promise(resolve => setTimeout(resolve, 250));
      await assert.rejects(call('browser_type', {snapshot_id: snapshot.snapshot_id, ref: ref('Name'), text: 'replaced'}), /stale/);
      snapshot = await call('browser_navigate', {url: origin + '/sensitive'});
      assert.equal(snapshot.sensitive, true);
      const secrets = ['do-not-expose-this', 'secret-card', 'secret-otp', 'secret-email', 'secret-option', 'secret-console', 'secret-network'];
      for (const secret of secrets) assert.equal(JSON.stringify(snapshot).includes(secret), false, secret);
      for (const tool of ['browser_console_messages', 'browser_network_requests']) {
        const output = await call(tool);
        assert.equal(output.redacted, true);
        for (const secret of secrets) assert.equal(JSON.stringify(output).includes(secret), false, secret);
      }
      await assert.rejects(call('browser_click', {snapshot_id: snapshot.snapshot_id, ref: ref('Change')}), /sensitive/);
      snapshot = await call('browser_navigate', {url: origin + '/'});
      for (const secret of secrets) assert.equal(JSON.stringify(snapshot).includes(secret), false, secret);
      for (const tool of ['browser_console_messages', 'browser_network_requests']) {
        const output = await call(tool);
        for (const secret of secrets) assert.equal(JSON.stringify(output).includes(secret), false, secret);
      }
      await assert.rejects(call('browser_navigate', {url: origin + '/redirect'}), /redirect|blocked/);
      await assert.rejects(call('browser_navigate', {url: 'https://example.org/'}), /origin/);
      await call('browser_close');
      const previousOrigins = process.env.VIBE_BROWSER_ORIGINS;
      const previousSandbox = process.env.VIBE_BROWSER_NO_SANDBOX;
      process.env.VIBE_BROWSER_ORIGINS = JSON.stringify([origin]);
      process.env.VIBE_BROWSER_NO_SANDBOX = '1';
      const {BrowserWorker} = require('../scripts/browser_worker.cjs');
      const budgetWorker = new BrowserWorker();
      try {
        let budgetSnapshot = await budgetWorker.call('browser_navigate', {url: origin + '/'});
        for (let index = 0; index < 20; index += 1) {
          const control = budgetSnapshot.elements.find(element => element.name === 'Change');
          budgetSnapshot = await budgetWorker.call('browser_click', {snapshot_id: budgetSnapshot.snapshot_id, ref: control.ref});
          assert.equal(budgetSnapshot.changed, index === 0);
        }
        const control = budgetSnapshot.elements.find(element => element.name === 'Change');
        await assert.rejects(budgetWorker.call('browser_click', {snapshot_id: budgetSnapshot.snapshot_id, ref: control.ref}), /budget.*20/);
      } finally {
        await budgetWorker.close();
        if (previousOrigins === undefined) delete process.env.VIBE_BROWSER_ORIGINS;
        else process.env.VIBE_BROWSER_ORIGINS = previousOrigins;
        if (previousSandbox === undefined) delete process.env.VIBE_BROWSER_NO_SANDBOX;
        else process.env.VIBE_BROWSER_NO_SANDBOX = previousSandbox;
      }
    } finally {
      worker.stdin.end();
      await new Promise(resolve => {if (worker.exitCode !== null) resolve(); else worker.once('exit', resolve);});
      await new Promise(resolve => server.close(resolve));
    }
  });
