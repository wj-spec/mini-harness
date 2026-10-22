import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {StringDecoder} from 'node:string_decoder';
import {backendCommand, backendLaunchOptions} from './backend-launch.js';
import {BACKEND_COMMANDS, MAX_FRAME_BYTES, parseServerEvent, validateClientArgs, validateSkillsArgs, type ApprovalDecision, type BackendCommand, type ClientRequest, type ServerEvent, type SkillsCommandArgs} from './protocol.js';

export interface BackendClient {
  subscribe(listener: (event: ServerEvent) => void): () => void;
  run(text: string): string;
  command(command: BackendCommand, args?: SkillsCommandArgs): string;
  cancel(target: string): void;
  approve(approvalId: string, decision: ApprovalDecision): void;
  shutdown(): Promise<void>;
  start(): void;
}

export interface BackendOptions {
  repoRoot?: string;
  python?: string;
  args?: readonly string[];
  shutdownTimeoutMs?: number;
  killTimeoutMs?: number;
  onDiagnostic?: (message: string) => void;
}

const MAX_DIAGNOSTIC_BYTES = 8192;
const diagnosticNotice = '\n[后端诊断超过 8 KiB，其余内容已省略]\n';
const diagnosticContentLimit = MAX_DIAGNOSTIC_BYTES - Buffer.byteLength(diagnosticNotice, 'utf8');
const MAX_REPLAY_BYTES = 16 * MAX_FRAME_BYTES;

export function createBackendClient(options: BackendOptions = {}): BackendClient {
  const args = validateClientArgs(options.args ?? []);
  const listeners = new Set<(event: ServerEvent) => void>();
  const shutdownTimeout = options.shutdownTimeoutMs ?? 4000;
  const killTimeout = options.killTimeoutMs ?? 500;
  let child: ChildProcessWithoutNullStreams | undefined;
  let started = false;
  let shuttingDown = false;
  let exited = false;
  let receivedClosed = false;
  let failed = false;
  let ready: ServerEvent | undefined;
  const history: ServerEvent[] = [];
  const notices: ServerEvent[] = [];
  let historyBytes = 0;
  let historyTruncated = false;
  let latestStatus: ServerEvent | undefined;
  let startupStatus: ServerEvent | undefined;
  let loadedContext: ServerEvent | undefined;
  let loadedMetrics: ServerEvent | undefined;
  let hasStartedRun = false;
  const pendingApprovals = new Map<string, Extract<ServerEvent, {type: 'approval_request'}>>();
  let terminalError: ServerEvent | undefined;
  let terminalClosed: ServerEvent | undefined;
  let buffered = Buffer.alloc(0);
  let shutdownPromise: Promise<void> | undefined;
  let exitedPromise: Promise<void> = Promise.resolve();
  let markExited = () => {};
  let closedPromise: Promise<void> = Promise.resolve();
  let markClosed = () => {};
  let pipesClosed = false;
  let eofTimer: ReturnType<typeof setTimeout> | undefined;
  let exitDrainTimer: ReturnType<typeof setTimeout> | undefined;
  const diagnosticDecoder = new StringDecoder('utf8');
  let diagnosticPending = '';
  let diagnosticHistory = '';
  let diagnosticBytes = 0;
  let diagnosticInputBytes = 0;
  let diagnosticTruncated = false;
  let diagnosticEnded = false;

  function emit(event: ServerEvent) {
    if (event.type === 'ready') ready = event;
    if (event.type === 'history_message') {
      history.push(event);
      historyBytes += Buffer.byteLength(JSON.stringify(event), 'utf8');
      while (historyBytes > MAX_REPLAY_BYTES && history.length) {
        historyBytes -= Buffer.byteLength(JSON.stringify(history.shift()!), 'utf8');
        historyTruncated = true;
      }
    }
    if (event.type === 'notice' && notices.length < 32) notices.push(event);
    if (!hasStartedRun && event.type === 'context_snapshot' && event.snapshot.phase === 'loaded') loadedContext = event;
    if (!hasStartedRun && event.type === 'metrics' && event.id === null) loadedMetrics = event;
    if (event.type === 'status') {
      latestStatus = event;
      if (event.phase === 'starting') startupStatus = event;
      if (event.phase === 'idle') {pendingApprovals.clear(); startupStatus = undefined;}
    }
    if (event.type === 'approval_request') pendingApprovals.set(event.approval_id, event);
    if (event.type === 'approval_resolved') pendingApprovals.delete(event.approval_id);
    if (event.type === 'error') terminalError = event;
    if (event.type === 'closed') {terminalClosed = event; pendingApprovals.clear();}
    for (const listener of listeners) listener(event);
  }

  function appendDiagnostic(message: string) {
    if (!message) return;
    diagnosticHistory += message;
    diagnosticBytes += Buffer.byteLength(message, 'utf8');
    emit({v: 1, type: 'diagnostic', id: null, message});
    options.onDiagnostic?.(message);
  }

  function publishDiagnostic(message: string) {
    if (!message || diagnosticTruncated) return;
    const bytes = Buffer.from(message, 'utf8');
    const remaining = Math.max(0, diagnosticContentLimit - diagnosticBytes);
    if (bytes.length <= remaining) {
      appendDiagnostic(message);
      return;
    }
    // 丢弃边界上的半个字符，避免截断提示前出现乱码。
    appendDiagnostic(new StringDecoder('utf8').write(bytes.subarray(0, remaining)));
    diagnosticTruncated = true;
    appendDiagnostic(diagnosticNotice);
  }

  function flushDiagnostics() {
    const text = diagnosticPending;
    diagnosticPending = '';
    publishDiagnostic(text);
  }

  function receiveDiagnostics(chunk: Buffer) {
    if (diagnosticTruncated || diagnosticEnded) return;
    const remaining = Math.max(0, diagnosticContentLimit - diagnosticInputBytes);
    const accepted = chunk.subarray(0, remaining);
    diagnosticInputBytes += accepted.length;
    diagnosticPending += diagnosticDecoder.write(accepted);
    const newline = diagnosticPending.lastIndexOf('\n');
    if (newline >= 0) {
      const complete = diagnosticPending.slice(0, newline + 1);
      diagnosticPending = diagnosticPending.slice(newline + 1);
      publishDiagnostic(complete);
    }
    if (chunk.length > remaining) {
      flushDiagnostics();
      if (!diagnosticTruncated) {
        diagnosticTruncated = true;
        appendDiagnostic(diagnosticNotice);
      }
    }
  }

  function finishDiagnostics() {
    if (diagnosticEnded) return;
    diagnosticEnded = true;
    if (!diagnosticTruncated) diagnosticPending += diagnosticDecoder.end();
    flushDiagnostics();
  }

  function transportError(code: string, message: string) {
    if (failed) return;
    failed = true;
    emit({v: 1, type: 'error', id: null, code, message});
    void shutdown();
  }

  function signal(signalName: NodeJS.Signals) {
    if (!child?.pid || (process.platform === 'win32' && exited)) return;
    try {
      if (process.platform === 'win32') child.kill(signalName);
      else process.kill(-child.pid, signalName);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ESRCH') {
        publishDiagnostic(`无法终止后端：${(error as Error).message}`);
      }
    }
  }

  function groupAlive(): boolean {
    if (!child?.pid) return false;
    if (process.platform === 'win32') return !exited;
    try {process.kill(-child.pid, 0); return true;}
    catch (error) {return (error as NodeJS.ErrnoException).code !== 'ESRCH';}
  }

  function notifyClosed() {
    if (!terminalClosed) emit({v: 1, type: 'closed', id: null});
  }

  function send(request: ClientRequest, allowShutdown = false) {
    if (!child || exited || child.stdin.destroyed || (shuttingDown && !allowShutdown)) {
      throw new Error('Python 后端未运行');
    }
    const frame = JSON.stringify(request);
    if (Buffer.byteLength(frame, 'utf8') > MAX_FRAME_BYTES) {
      throw new Error('请求超过 1 MiB 帧大小上限');
    }
    // 限制排队请求，避免停滞的后端无限占用内存。
    if (child.stdin.writableLength + Buffer.byteLength(frame, 'utf8') + 1 > MAX_FRAME_BYTES * 2) {
      throw new Error('Python 后端请求队列已满');
    }
    child.stdin.write(`${frame}\n`);
    if (request.type === 'run' || (request.type === 'command' && request.command === 'resume')) {
      hasStartedRun = true;
      loadedContext = undefined;
      loadedMetrics = undefined;
    }
  }

  async function waitForExit(timeout: number): Promise<boolean> {
    if (exited) return true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const result = await Promise.race([
      exitedPromise.then(() => true),
      new Promise<boolean>(resolve => {timer = setTimeout(() => resolve(false), timeout);}),
    ]);
    if (timer) clearTimeout(timer);
    return result;
  }

  function shutdown(): Promise<void> {
    if (shutdownPromise) return shutdownPromise;
    shuttingDown = true;
    shutdownPromise = (async () => {
      if (!child) {notifyClosed(); return;}
      if (!exited) {
        try {send({v: 1, type: 'shutdown', id: randomUUID()}, true);} catch {/* 已断开则直接回收。 */}
        await waitForExit(shutdownTimeout);
      }
      // 父进程退出后，仍可能有同组工具进程存活。
      if (groupAlive()) {
        signal('SIGTERM');
        const deadline = Date.now() + killTimeout;
        while (groupAlive() && Date.now() < deadline) {
          await new Promise(resolve => setTimeout(resolve, Math.min(25, killTimeout)));
        }
      }
      if (groupAlive()) signal('SIGKILL');
      if (!await waitForExit(Math.max(killTimeout, 250))) {
        emit({v: 1, type: 'error', id: null, code: 'backend_shutdown', message: 'Python 后端在强制终止后仍未退出'});
        child.unref();
      }
      if (!pipesClosed) {
        let timer: ReturnType<typeof setTimeout> | undefined;
        await Promise.race([closedPromise, new Promise<void>(resolve => {timer = setTimeout(resolve, Math.max(killTimeout, 250));})]);
        if (timer) clearTimeout(timer);
      }
      child.stdin.destroy();
      child.stdout.destroy();
      child.stderr.destroy();
      finishDiagnostics();
      if (eofTimer) clearTimeout(eofTimer);
      if (exitDrainTimer) clearTimeout(exitDrainTimer);
      notifyClosed();
    })();
    return shutdownPromise;
  }

  function receive(chunk: Buffer) {
    if (failed || receivedClosed) return;
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(10, offset);
      const end = newline === -1 ? chunk.length : newline;
      const part = chunk.subarray(offset, end);
      if (buffered.length + part.length > MAX_FRAME_BYTES) {
        transportError('protocol_error', 'Python 后端消息超过 1 MiB 帧大小上限');
        return;
      }
      buffered = buffered.length ? Buffer.concat([buffered, part]) : Buffer.from(part);
      if (newline === -1) return;
      const frame = buffered;
      buffered = Buffer.alloc(0);
      offset = newline + 1;
      try {
        const text = new TextDecoder('utf-8', {fatal: true}).decode(frame);
        const event = parseServerEvent(text);
        if (event.type === 'closed') receivedClosed = true;
        emit(event);
        if (receivedClosed) return;
      } catch (error) {
        transportError('protocol_error', (error as Error).message);
        return;
      }
    }
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      const diagnostic: ServerEvent | undefined = diagnosticHistory ? {v: 1, type: 'diagnostic', id: null, message: diagnosticHistory} : undefined;
      const truncated: ServerEvent | undefined = historyTruncated ? {v: 1, type: 'notice', id: null, message: '恢复历史显示超过 16 MiB，较早消息已省略；模型仍使用后端保存的上下文。'} : undefined;
      const previous = [ready, truncated, ...history, ...notices, diagnostic, loadedContext, loadedMetrics, startupStatus !== latestStatus ? startupStatus : undefined,
        latestStatus, ...pendingApprovals.values(), terminalError, terminalClosed].filter((event): event is ServerEvent => event !== undefined);
      if (previous.length) queueMicrotask(() => {
        if (listeners.has(listener)) for (const event of previous) listener(event);
      });
      return () => {listeners.delete(listener);};
    },
    start() {
      if (started || shuttingDown) return;
      started = true;
      exitedPromise = new Promise<void>(resolve => {markExited = resolve;});
      closedPromise = new Promise<void>(resolve => {markClosed = resolve;});
      try {
        child = spawn(backendCommand, ['--stdio', ...args], {
          ...backendLaunchOptions(options),
          detached: process.platform !== 'win32',
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (error) {
        exited = true;
        markExited();
        transportError('backend_spawn', `无法启动 Python 后端：${(error as Error).message}`);
        return;
      }
      child.stdout.on('data', receive);
      child.stderr.on('data', receiveDiagnostics);
      child.stderr.on('end', finishDiagnostics);
      child.stdin.on('error', () => {
        if (!shuttingDown && !exited) transportError('backend_input', 'Python 后端输入通道已断开');
      });
      child.on('error', error => {
        exited = true;
        markExited();
        transportError('backend_spawn', `无法启动 Python 后端：${error.message}`);
      });
      child.on('exit', (code, signalName) => {
        exited = true;
        markExited();
        // close 可能被子进程继承的 stderr 阻塞；只给最后的 stdout 消息短暂排空窗口。
        exitDrainTimer = setTimeout(() => {
          if (!receivedClosed && !failed) {
            transportError('backend_exit', `Python 后端意外退出（${signalName ?? `exit ${code}`}）`);
          } else if (groupAlive()) {
            void shutdown();
          }
        }, 50);
      });
      child.on('close', (code, signalName) => {
        pipesClosed = true;
        finishDiagnostics();
        if (eofTimer) clearTimeout(eofTimer);
        if (exitDrainTimer) clearTimeout(exitDrainTimer);
        markClosed();
        if (!receivedClosed && !failed) {
          transportError('backend_exit', `Python 后端意外退出（${signalName ?? `exit ${code}`}）`);
        }
        notifyClosed();
      });
      child.stdout.on('end', () => {
        if (!receivedClosed && !failed) {
          // 给进程 close 事件留出时间，优先展示退出码。
          eofTimer = setTimeout(() => {
            if (!exited && !receivedClosed && !failed) transportError('backend_eof', 'Python 后端输出已结束，但没有 closed 消息');
            else if (buffered.length && !failed) transportError('protocol_error', 'Python 后端输出包含未完成的 JSON 帧');
          }, 50);
        }
      });
    },
    run(text) {
      if (!ready) throw new Error('Python 后端尚未就绪');
      if (!text.trim()) throw new Error('任务内容不能为空');
      const id = randomUUID();
      send({v: 1, type: 'run', id, text});
      return id;
    },
    command(command, args) {
      if (!ready) throw new Error('Python 后端尚未就绪');
      if (!BACKEND_COMMANDS.includes(command)) throw new Error('后端命令无效');
      if (args !== undefined && command !== 'skills') throw new Error('只有 skills 命令接受参数');
      if (args !== undefined) validateSkillsArgs(args);
      const id = randomUUID();
      if (command === 'skills') send({v: 1, type: 'command', id, command, ...(args === undefined ? {} : {args})});
      else send({v: 1, type: 'command', id, command});
      return id;
    },
    cancel(target) {
      if (!target || target.length > 128) throw new Error('取消目标无效');
      send({v: 1, type: 'cancel', id: randomUUID(), target});
    },
    approve(approvalId, decision) {
      if (!approvalId || approvalId.length > 128 || !['once', 'session', 'workspace', 'deny'].includes(decision)) throw new Error('审批响应无效');
      const approval = pendingApprovals.get(approvalId);
      if (!approval || !(approval.decisions ?? ['once', 'deny']).includes(decision)) throw new Error('审批已失效或超出允许范围');
      send({v: 1, type: 'approval', id: randomUUID(), approval_id: approvalId, decision});
      pendingApprovals.delete(approvalId);
    },
    shutdown,
  };
}
