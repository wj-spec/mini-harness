#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const {createRequire} = require('node:module');
const {randomUUID, createHash} = require('node:crypto');

const MAX_ACTIONS = 20;
const MAX_RESULT = 18500;
const SENSITIVE = /login|sign[ -]?in|checkout|payment|captcha|登录|支付|付款|验证码/i;
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');

function runtime() {
  for (const directory of (process.env.PATH || '').split(path.delimiter)) {
    const executable = path.join(directory, 'playwright-mcp');
    if (!fs.existsSync(executable)) continue;
    const requireAt = createRequire(fs.realpathSync(executable));
    if (requireAt('./package.json').version !== '0.0.83') throw new Error('Playwright MCP version must be 0.0.83');
    return requireAt('playwright');
  }
  throw new Error('Missing Playwright MCP 0.0.83 runtime; install explicitly with npx --package @playwright/mcp@0.0.83, then install its Chromium. No installation was attempted.');
}

function originsFromEnvironment() {
  const origins = JSON.parse(process.env.VIBE_BROWSER_ORIGINS || '[]');
  if (!Array.isArray(origins) || !origins.length) throw new Error('allowed origins must be a non-empty list');
  for (const origin of origins) {
    const url = new URL(origin);
    if (!['http:', 'https:'].includes(url.protocol) || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
      || url.origin !== origin || url.username || url.password) throw new Error('only explicit loopback origins are allowed');
  }
  return new Set(origins);
}

// 固定可信检查；模型不能提供脚本或 selector。
function elementFacts(element, redact = false) {
  const sensitive = redact || ['password', 'file'].includes(element.type)
    || /^(cc-|one-time-code)/.test(element.autocomplete || '');
  return {
    tag: element.tagName.toLowerCase(), type: (element.type || '').toLowerCase(),
    disabled: !!element.disabled, editable: !!element.isContentEditable,
    value: sensitive ? '[redacted]' : element.value || '', connected: element.isConnected,
    sensitive,
    submit: element.tagName === 'BUTTON' && element.type === 'submit' && !!element.form
      || element.tagName === 'INPUT' && ['submit', 'image'].includes(element.type),
    options: element.tagName === 'SELECT' ? Array.from(element.options, option => ({
      value: option.value, name: option.label, disabled: option.disabled || !!option.closest('optgroup[disabled]'), selected: option.selected,
    })) : undefined,
  };
}

class BrowserWorker {
  constructor() {
    this.origins = originsFromEnvironment();
    this.browser = null;
    this.page = null;
    this.snapshot = null;
    this.handles = new Map();
    this.actions = 0;
    this.epoch = 0;
    this.console = [];
    this.network = [];
    this.blocked = [];
    this.documentUrl = null;
    this.closed = false;
  }

  checkUrl(value) {
    let url;
    try { url = new URL(value); } catch { throw new Error('invalid browser URL'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || !this.origins.has(url.origin))
      throw new Error('origin not allowed');
    return url;
  }

  block(reason) {
    this.blocked.push(reason);
    this.blocked = this.blocked.slice(-20);
  }

  async start() {
    if (this.closed) throw new Error('browser closed; restart the MCP server for a new session');
    if (this.browser) return;
    // Chromium 的临时 profile 必须用绝对路径，否则 headless 的稳定性检查会挂起。
    for (const key of ['HOME', 'TMPDIR', 'XDG_CACHE_HOME', 'PLAYWRIGHT_BROWSERS_PATH', 'PWTEST_SOCKETS_DIR']) {
      if (process.env[key]) process.env[key] = path.resolve(process.env[key]);
    }
    const {chromium} = runtime();
    this.browser = await chromium.launch({
      headless: true, channel: 'chromium', chromiumSandbox: process.env.VIBE_BROWSER_NO_SANDBOX !== '1',
    });
    this.context = await this.browser.newContext({acceptDownloads: false, serviceWorkers: 'block', permissions: []});
    await this.context.route('**/*', async route => {
      const request = route.request();
      try {
        this.checkUrl(request.url());
        if (this.documentUrl === null) throw new Error('network request blocked outside approved navigation (submissions are disabled)');
        if (!['GET', 'HEAD'].includes(request.method())) throw new Error('non-read request blocked');
        if (request.isNavigationRequest() && request.resourceType() === 'document'
          && request.url() !== this.documentUrl) throw new Error('document navigation blocked (submissions are disabled)');
        const response = await route.fetch({maxRedirects: 0, timeout: 5000});
        if (response.status() >= 300 && response.status() < 400) throw new Error('redirect blocked');
        const headers = response.headers();
        if (/attachment/i.test(headers['content-disposition'] || '')
          || /application\/(octet-stream|pdf)/i.test(headers['content-type'] || '')) throw new Error('download blocked');
        await route.fulfill({response});
        await response.dispose();
      } catch (error) {
        this.block(error.message);
        await route.abort('blockedbyclient').catch(() => {});
      }
    });
    await this.context.routeWebSocket('**/*', socket => { this.block('websocket blocked'); socket.close(); });
    await this.context.addInitScript(() => {
      window.addEventListener('submit', event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        console.warn('browser policy: submit blocked');
      }, true);
    });
    this.context.on('page', popup => {
      if (this.page && popup !== this.page) {
        this.block('popup blocked');
        void popup.close().catch(() => {});
      }
    });
    this.page = await this.context.newPage();
    this.page.setDefaultTimeout(3000);
    this.page.setDefaultNavigationTimeout(10000);
    this.page.on('framenavigated', frame => { if (frame === this.page.mainFrame()) this.epoch += 1; });
    this.page.on('console', message => {
      if (['error', 'warning'].includes(message.type())) this.console = [...this.console, {level: message.type(), text: message.text().slice(0, 500)}].slice(-10);
    });
    this.page.on('pageerror', error => {this.console = [...this.console, {level: 'error', text: error.message.slice(0, 500)}].slice(-10);});
    this.page.on('response', response => {
      if (response.status() >= 400) this.network = [...this.network, {url: response.url().split('?')[0].slice(0, 300), status: response.status()}].slice(-10);
    });
    this.page.on('requestfailed', request => {
      this.network = [...this.network, {url: request.url().split('?')[0].slice(0, 300), error: request.failure()?.errorText}].slice(-10);
    });
    this.page.on('download', download => {this.block('download blocked'); void download.cancel().catch(() => {});});
    this.page.on('filechooser', () => {this.block('file chooser blocked');});
    this.page.on('dialog', dialog => {this.block('dialog blocked'); void dialog.dismiss().catch(() => {});});
  }

  async pageFacts() {
    const url = this.page.url();
    if (url === 'about:blank') {
      throw new Error('browser_not_navigated: new browser session is at about:blank; previous page state is not restored. '
        + `Use browser_navigate to an allowed local origin (${[...this.origins].join(', ')}) with approval before reading the page.`);
    }
    this.checkUrl(url);
    const title = await this.page.title();
    const facts = await this.page.evaluate(() => ({
      markup: document.documentElement.outerHTML,
      sensitive: !!document.querySelector('input[type="password"],input[type="file"],input[autocomplete^="cc-"],input[autocomplete="one-time-code"],iframe[src*="captcha"],[id*="captcha"],[class*="captcha"]')
        || [...document.forms].some(form => /login|sign.?in|payment|checkout|captcha/i.test(form.action)),
    }));
    return {url: this.page.url(), title, sensitive: facts.sensitive || SENSITIVE.test(title + ' ' + this.page.url()), markup: digest(facts.markup)};
  }

  async capture() {
    const facts = await this.pageFacts();
    const tree = await this.page.ariaSnapshotJSON({mode: 'ai'});
    const text = await this.page.ariaSnapshot({mode: 'ai'});
    const elements = [], handles = new Map();
    const nodes = [...tree];
    while (nodes.length && elements.length < 150) {
      const node = nodes.shift();
      nodes.push(...(node.children || []));
      if (!node.ref) continue;
      const handle = await this.page.locator(`aria-ref=${node.ref}`).elementHandle();
      if (!handle) continue;
      const metadata = await handle.evaluate(elementFacts, facts.sensitive);
      const field = ['input', 'textarea', 'select'].includes(metadata.tag);
      const element = {ref: node.ref, role: node.role, name: metadata.sensitive && field ? '[sensitive field]' : node.name || '', ...metadata};
      elements.push(element);
      handles.set(node.ref, handle);
    }
    return {facts, tree, text, elements, handles, fingerprint: digest([facts, tree, elements]), epoch: this.epoch};
  }

  async invalidate() {
    this.snapshot = null;
    const handles = this.handles;
    this.handles = new Map();
    await Promise.all([...handles.values()].map(handle => handle.dispose().catch(() => {})));
  }

  async publish(captured, extra = {}) {
    await this.invalidate();
    const {markup, ...facts} = captured.facts;
    const result = {
      snapshot_id: randomUUID(), timestamp: new Date().toISOString(), ...facts, elements: captured.elements, snapshot: captured.text.slice(0, 9000),
      console: this.console.slice(-5), network: this.network.slice(-5), blocked: this.blocked.slice(-5), untrusted: true, ...extra,
    };
    if (facts.sensitive) {
      result.elements = captured.elements.map(({ref, role, name}) => ({ref, role, name}));
      result.snapshot = result.elements.map(element => `- ${element.role} ${JSON.stringify(element.name)} [ref=${element.ref}]`).join('\n').slice(0, 9000);
      result.console = [];
      result.network = [];
    }
    if (captured.text.length > 9000) result.truncated = true;
    // 保留的 ref 必须在展示文本里，完整 JSON 留在 MCP 的文本上限以内。
    result.elements = result.elements.filter(element => result.snapshot.includes(`[ref=${element.ref}]`));
    while (JSON.stringify(result).length > MAX_RESULT && result.elements.length) {
      result.elements.pop();
      result.truncated = true;
    }
    if (JSON.stringify(result).length > MAX_RESULT) {
      result.snapshot = result.snapshot.slice(0, 3000);
      result.console = [];
      result.network = [];
      result.elements = [];
      result.truncated = true;
    }
    if (JSON.stringify(result).length > MAX_RESULT) throw new Error('browser output too large');
    this.handles = captured.handles;
    this.snapshot = {id: result.snapshot_id, captured, elements: result.elements};
    return result;
  }

  async action(tool, args) {
    const previous = this.snapshot;
    if (!previous || args.snapshot_id !== previous.id) throw new Error('stale snapshot; read browser_snapshot again');
    if (this.epoch !== previous.captured.epoch) throw new Error('stale snapshot: page navigated during approval');
    const element = previous.elements.find(element => element.ref === args.ref);
    if (!element) throw new Error('stale or unknown ref');
    if (this.actions >= MAX_ACTIONS) throw new Error('browser action budget exhausted (20)');
    if (previous.captured.facts.sensitive) throw new Error('sensitive page blocked');
    if (element.disabled) throw new Error('disabled element');
    if (element.submit) throw new Error('submit controls are blocked');
    if (tool === 'browser_type') {
      if (args.submit !== undefined && args.submit !== false) throw new Error('submit must be false');
      if (typeof args.text !== 'string' || args.text.length > 4000) throw new Error('text must be a string of at most 4000 characters');
      if (!(element.tag === 'textarea' || element.tag === 'input' && ['text', 'search', 'url', 'tel', 'email', ''].includes(element.type))) throw new Error('field is not a permitted text input');
    }
    if (tool === 'browser_select_option') {
      if (element.tag !== 'select' || !Array.isArray(args.values) || !args.values.length
        || !args.values.every(value => typeof value === 'string' && element.options.some(option => option.value === value && !option.disabled)))
        throw new Error('select values must be available enabled options');
    }
    const current = await this.capture();
    const handle = this.handles.get(args.ref);
    const same = handle && await handle.evaluate(element => element.isConnected).catch(() => false);
    for (const item of current.handles.values()) await item.dispose().catch(() => {});
    if (current.epoch !== previous.captured.epoch || current.fingerprint !== previous.captured.fingerprint || !same)
      throw new Error('stale snapshot: page changed during approval');
    this.snapshot = null;
    this.blocked = [];
    this.actions += 1;
    if (tool === 'browser_click') await handle.click({noWaitAfter: true});
    else if (tool === 'browser_type') await handle.fill(args.text);
    else await handle.selectOption(args.values);
    // 本地 UI 的异步事件获得一个事件循环窗口；稍后变化仍由下次 preflight 检出。
    await this.page.waitForTimeout(50);
    if (this.blocked.length) throw new Error(this.blocked.join('; '));
    const after = await this.capture();
    return this.publish(after, {changed: after.fingerprint !== previous.captured.fingerprint});
  }

  async call(tool, args) {
    if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('arguments must be an object');
    const fields = {
      browser_navigate: ['url'], browser_snapshot: [], browser_click: ['snapshot_id', 'ref'],
      browser_type: ['snapshot_id', 'ref', 'text', 'submit'], browser_select_option: ['snapshot_id', 'ref', 'values'],
      browser_console_messages: [], browser_network_requests: [], browser_close: [],
    };
    try {
      if (!Object.hasOwn(fields, tool)) throw new Error('unknown controlled browser tool');
      if (Object.keys(args).some(key => !fields[tool].includes(key))) throw new Error('unexpected browser arguments');
      if (tool === 'browser_close') {
        await this.close();
        return {closed: true};
      }
      if (tool === 'browser_navigate') this.checkUrl(args.url);
      await this.start();
      if (tool === 'browser_console_messages' || tool === 'browser_network_requests') {
        const facts = await this.pageFacts();
        const key = tool === 'browser_console_messages' ? 'console' : 'network';
        return facts.sensitive ? {[key]: [], redacted: true} : {[key]: this[key]};
      }
      if (tool === 'browser_navigate') {
        await this.invalidate();
        this.console = [];
        this.network = [];
        this.blocked = [];
        const url = this.checkUrl(args.url);
        url.hash = '';
        this.documentUrl = url.href;
        try { await this.page.goto(args.url, {waitUntil: 'load'}); }
        finally { this.documentUrl = null; }
        if (this.blocked.length) throw new Error(this.blocked.join('; '));
        return this.publish(await this.capture());
      }
      if (tool === 'browser_snapshot') return this.publish(await this.capture());
      return await this.action(tool, args);
    } catch (error) {
      await this.invalidate();
      throw error;
    }
  }

  async close() {
    this.closed = true;
    await this.invalidate();
    if (this.browser) await this.browser.close();
    this.browser = null;
  }
}

async function main() {
  const worker = new BrowserWorker();
  const input = readline.createInterface({input: process.stdin});
  let queue = Promise.resolve();
  input.on('line', line => {
    queue = queue.then(async () => {
      let request;
      try {
        if (line.length > 100000) throw new Error('request too large');
        request = JSON.parse(line);
        const result = await worker.call(request.tool, request.args || {});
        process.stdout.write(JSON.stringify({id: request.id, result}) + '\n');
      } catch (error) {
        process.stdout.write(JSON.stringify({id: request?.id ?? null, error: String(error.message || error).slice(0, 1200)}) + '\n');
      }
    });
  });
  input.on('close', () => {void queue.finally(() => worker.close());});
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => {void worker.close().finally(() => process.exit(0));});
}

if (require.main === module) main().catch(error => {process.stderr.write(error.message + '\n'); process.exitCode = 1;});

module.exports = {BrowserWorker};
