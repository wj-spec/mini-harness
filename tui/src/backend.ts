import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {StringDecoder} from 'node:string_decoder';
import {validateTaskCatalogArgs, type TaskCatalogArgs} from './protocol.js';
import {backendCommand, backendLaunchOptions} from './backend-launch.js';
import {BACKEND_COMMANDS, MAX_FRAME_BYTES, parseServerEvent, validateAutonomyArgs, validateClientArgs, validateExploreArgs, validateNotificationsArgs, validatePlanArgs, validateInspectionArgs, validateSessionCatalogArgs, type InspectionHistoryArgs, validateSkillsArgs, validateTraceArgs, validatePriorityArgs, type ApprovalDecision, type AutonomyCommandArgs, type BackendCommand, type ClientRequest, type CommandArgs, type ExploreCommandArgs, type NotificationsCommandArgs, type PlanCommandArgs, type RunTaskMeta, type ServerEvent, type SkillsCommandArgs, type TraceCommandArgs, type PriorityCommandArgs} from './protocol.js';

export interface BackendClient {
  subscribe(listener: (event: ServerEvent) => void): () => void;
  capabilities?(): readonly string[];
  run(text: string, task?: RunTaskMeta, requestId?: string): string;
  command(command: BackendCommand, args?: CommandArgs): string;
  cancel(target: string): string;
  steer(target: string, text: string): string;
  approve(approvalId: string, decision: ApprovalDecision): string;
  shutdown(): Promise<void>;
  start(): void;
  connectTask?(target: TaskConnectionTarget, signal?: AbortSignal): Promise<BackendClient>;
  openSession?(target: SessionOpenTarget, signal?: AbortSignal): Promise<BackendClient>;
}

export interface TaskConnectionTarget {workspace: string; sessionId: string; taskId: string}

export interface SessionOpenTarget {workspace: string; sessionId: string; identity: string}

export interface AttachOptions {
  taskId: string;
  session: string;
}

export function backendSpawnArgs(args: readonly string[], attach?: AttachOptions): string[] {
  if (attach) {
    return ['--attach', '--task-id', attach.taskId, '--session', attach.session,
      '--mode', 'control', ...args];
  }
  return ['--stdio', ...args];
}

export interface BackendOptions {
  repoRoot?: string;
  args?: readonly string[];
  attach?: AttachOptions;
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
  let latestPlanMode: ServerEvent | undefined;
  let latestTaskSnapshot: ServerEvent | undefined;
  const connections = new Set<BackendClient>();
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
    if (event.type === 'plan_mode') latestPlanMode = event;
    if (event.type === 'task_snapshot') latestTaskSnapshot = event;
    if ((event.type === 'plan_review' && event.outcome === 'launched' && event.launch.run_started) ||
        (event.type === 'plan_revision' && event.task?.run_started)) {
      hasStartedRun = true;
      loadedContext = undefined;
      loadedMetrics = undefined;
    }
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
    if (ready?.type === 'ready' && ready.capabilities.includes('attach-offline') && request.type !== 'shutdown' &&
        !(request.type === 'command' && (request.command === 'inspections' || request.command === 'plan' && request.args?.action === 'review'))) {
      throw new Error('离线结果只读，不会启动或控制任务');
    }
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
      await Promise.all([...connections].map(connection => connection.shutdown()));
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
      const previous = [ready, latestTaskSnapshot, latestPlanMode, truncated, ...history, ...notices, diagnostic, loadedContext, loadedMetrics, startupStatus !== latestStatus ? startupStatus : undefined,
        latestStatus, ...pendingApprovals.values(), terminalError, terminalClosed].filter((event): event is ServerEvent => event !== undefined);
      if (previous.length) queueMicrotask(() => {
        if (listeners.has(listener)) for (const event of previous) listener(event);
      });
      return () => {listeners.delete(listener);};
    },
    async connectTask(target, abortSignal) {
      if (shuttingDown || abortSignal?.aborted) throw new Error('接入已取消');
      if (!target.workspace || !/^[-A-Za-z0-9_]{1,128}$/.test(target.sessionId) || !/^t-[-A-Za-z0-9_]{1,126}$/.test(target.taskId)) throw new Error('任务接入身份无效');
      const attachedArgs: string[] = [];
      for (let i = 0; i < args.length; i += 1) {
        if (args[i] === '--workspace' || args[i] === '--session') {i += 1; continue;}
        if (args[i] !== '--continue') attachedArgs.push(args[i]!);
      }
      attachedArgs.push('--workspace', target.workspace);
      const connection = createBackendClient({...options, args: attachedArgs, attach: {taskId: target.taskId, session: target.sessionId}});
      connections.add(connection);
      const untrack = connection.subscribe(event => {if (event.type === 'closed') {connections.delete(connection); untrack();}});
      try {
        await new Promise<void>((resolve, reject) => {
          let scoped = false; let identified = false; let settled = false; let diagnostic = '';
          const finish = (error?: Error) => {
            if (settled) return;
            settled = true; clearTimeout(timer); unsubscribe(); abortSignal?.removeEventListener('abort', abort);
            if (error) reject(error); else resolve();
          };
          const abort = () => finish(new Error('接入已取消，原会话保留'));
          const timer = setTimeout(() => finish(new Error('任务接入超时，原会话保留')), 10_000);
          const unsubscribe = connection.subscribe(event => {
            if (event.type === 'diagnostic') diagnostic = (diagnostic + event.message).slice(-2000);
            else if (event.type === 'ready') {
              if (event.workspace !== target.workspace || event.session_id !== target.sessionId || !event.capabilities.includes('attach')) {
                finish(new Error('接入回执的工作区或会话不匹配')); return;
              }
              scoped = true;
            } else if (event.type === 'task_snapshot') {
              if (event.task.task_id !== target.taskId) {finish(new Error('接入回执的任务不匹配')); return;}
              identified = true;
            } else if (event.type === 'error' || event.type === 'closed') {
              finish(new Error(diagnostic.trim() || (event.type === 'error' ? event.message : '连接在确认任务前关闭'))); return;
            }
            if (scoped && identified) finish();
          });
          abortSignal?.addEventListener('abort', abort, {once: true});
          connection.start();
        });
        return connection;
      } catch (error) {await connection.shutdown(); connections.delete(connection); throw error;}
    },
    async openSession(target, abortSignal) {
      if (shuttingDown || abortSignal?.aborted) throw new Error('打开已取消，当前会话保留');
      if (!ready || !(ready.type === 'ready' && ready.capabilities.includes('session-navigation'))) throw new Error('后端不支持旧会话目录');
      if (!target.workspace || !/^[-A-Za-z0-9_]{1,128}$/.test(target.sessionId) || !/^[0-9a-f]{64}$/.test(target.identity)) throw new Error('旧会话身份无效');
      const nextArgs: string[] = [];
      for (let i = 0; i < args.length; i += 1) {
        if (args[i] === '--workspace' || args[i] === '--session' || args[i] === '--session-identity') {i += 1; continue;}
        if (args[i] === '--continue' || args[i] === '--list-sessions') continue;
        nextArgs.push(args[i]!);
      }
      nextArgs.push('--workspace', target.workspace, '--session', target.sessionId, '--session-identity', target.identity);
      const connection = createBackendClient({...options, args: nextArgs, attach: undefined});
      connections.add(connection);
      const untrack = connection.subscribe(event => {if (event.type === 'closed') {connections.delete(connection); untrack();}});
      try {
        await new Promise<void>((resolve, reject) => {
          let settled = false; let diagnostic = '';
          const finish = (error?: Error) => {
            if (settled) return;
            settled = true; clearTimeout(timer); unsubscribe(); abortSignal?.removeEventListener('abort', abort);
            if (error) reject(error); else resolve();
          };
          const abort = () => finish(new Error('打开已取消，当前会话保留'));
          const timer = setTimeout(() => finish(new Error('打开旧会话超时，当前会话保留')), 10_000);
          const unsubscribe = connection.subscribe(event => {
            if (event.type === 'diagnostic') diagnostic = (diagnostic + event.message).slice(-2000);
            else if (event.type === 'ready') {
              if (event.workspace !== target.workspace || event.resumed !== true || event.session_id !== target.sessionId ||
                  event.session_identity !== target.identity) {finish(new Error('打开回执的工作区、会话或身份不匹配')); return;}
              finish();
            } else if (event.type === 'error' || event.type === 'closed') {
              finish(new Error(diagnostic.trim() || (event.type === 'error' ? event.message : '打开旧会话失败，当前会话保留'))); return;
            }
          });
          abortSignal?.addEventListener('abort', abort, {once: true});
          connection.start();
        });
        return connection;
      } catch (error) {await connection.shutdown(); connections.delete(connection); throw error;}
    },
    start() {
      if (started || shuttingDown) return;
      started = true;
      exitedPromise = new Promise<void>(resolve => {markExited = resolve;});
      closedPromise = new Promise<void>(resolve => {markClosed = resolve;});
      try {
        child = spawn(backendCommand, backendSpawnArgs(args, options.attach), {
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
    capabilities() {
      return ready?.type === 'ready' ? ready.capabilities : [];
    },
    run(text, task, requestId) {
      if (!ready) throw new Error('Python 后端尚未就绪');
      if (!text.trim()) throw new Error('任务内容不能为空');
      if (task && !(ready.type === 'ready' && ready.capabilities.includes('tasks'))) {
        throw new Error('Python 后端未启用任务运行时');
      }
      if (requestId !== undefined && !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(requestId)) throw new Error('请求身份无效');
      const id = requestId ?? randomUUID();
      send(task ? {v: 1, type: 'run', id, text, task} : {v: 1, type: 'run', id, text});
      return id;
    },
    command(command, args) {
      if (!ready) throw new Error('Python 后端尚未就绪');
      if (!BACKEND_COMMANDS.includes(command)) throw new Error('后端命令无效');
      if (command === 'inspections' && !(ready.type === 'ready' && ready.capabilities.includes('inspection-history'))) throw new Error('后端不支持历史结果查询');
      if (args !== undefined) {
        if (command === 'tasks') {
          if (!(ready.type === 'ready' && ready.capabilities.includes('task-navigation'))) throw new Error('后端不支持任务目录');
          validateTaskCatalogArgs(args);
        }
        else if (command === 'sessions') {
          if (!(ready.type === 'ready' && ready.capabilities.includes('session-navigation'))) throw new Error('后端不支持旧会话目录');
          validateSessionCatalogArgs(args);
        }
        else if (command === 'inspections') validateInspectionArgs(args);
        else if (command === 'skills') validateSkillsArgs(args);
        else if (command === 'autonomy') validateAutonomyArgs(args);
        else if (command === 'plan') {
          validatePlanArgs(args);
          if (args.action === 'list-revisions' &&
              !(ready.type === 'ready' && ready.capabilities.includes('plan-revision-history'))) {
            throw new Error('Python 后端不支持计划修订记录，请更新后端');
          }
          if ((args.action === 'query-revision' || args.action === 'resume-revision' ||
              (args.action === 'request-changes' && 'target' in args)) &&
              !(ready.type === 'ready' && ready.capabilities.includes('plan-revision'))) {
            throw new Error('Python 后端不支持计划反馈修订，请更新后端');
          }
          if (('target' in args || args.action === 'review') &&
              !(ready.type === 'ready' && ready.capabilities.includes('plan-review'))) {
            throw new Error('Python 后端不支持准确计划审阅，请使用已有查看命令');
          }
        }
        else if (command === 'explore') validateExploreArgs(args);
        else if (command === 'notifications') validateNotificationsArgs(args);
        else if (command === 'trace') validateTraceArgs(args);
        else if (command === 'priority') validatePriorityArgs(args);
        else throw new Error('只有 skills/autonomy/plan/explore 命令接受参数');
      }
      const id = randomUUID();
      if (command === 'skills') send({v: 1, type: 'command', id, command, ...(args === undefined ? {} : {args: args as SkillsCommandArgs})});
      else if (command === 'autonomy') send({v: 1, type: 'command', id, command, args: args as AutonomyCommandArgs});
      else if (command === 'explore') send({v: 1, type: 'command', id, command, args: args as ExploreCommandArgs});
      else if (command === 'notifications') send({v: 1, type: 'command', id, command, ...(args === undefined ? {} : {args: args as NotificationsCommandArgs})});
      else if (command === 'trace') send({v: 1, type: 'command', id, command, ...(args === undefined ? {} : {args: args as TraceCommandArgs})});
      else if (command === 'priority') send({v: 1, type: 'command', id, command, args: args as PriorityCommandArgs});
      else if (command === 'tasks') send({v: 1, type: 'command', id, command, ...(args === undefined ? {} : {args: args as TaskCatalogArgs})});
      else if (command === 'sessions') send({v: 1, type: 'command', id, command, ...(args === undefined ? {} : {args: args as TaskCatalogArgs})});
      else if (command === 'inspections') send({v: 1, type: 'command', id, command, ...(args === undefined ? {} : {args: args as InspectionHistoryArgs})});
      else if (command === 'plan') send({v: 1, type: 'command', id, command, ...(args === undefined ? {} : {args: args as PlanCommandArgs})});
      else send({v: 1, type: 'command', id, command});
      return id;
    },
    cancel(target) {
      if (!target || target.length > 128) throw new Error('取消目标无效');
      const id = randomUUID();
      send({v: 1, type: 'cancel', id, target});
      return id;
    },
    steer(target, text) {
      if (!target || target.length > 128) throw new Error('纠偏目标无效');
      if (!text.trim()) throw new Error('纠偏内容不能为空');
      const id = randomUUID();
      send({v: 1, type: 'steer', id, target, text});
      return id;
    },
    approve(approvalId, decision) {
      if (!approvalId || approvalId.length > 128 || !['once', 'session', 'workspace', 'deny'].includes(decision)) throw new Error('审批响应无效');
      const approval = pendingApprovals.get(approvalId);
      if (!approval || !(approval.decisions ?? ['once', 'deny']).includes(decision)) throw new Error('审批已失效或超出允许范围');
      const id = randomUUID();
      if (options.attach) {
        send({v: 1, type: 'approval_decide', id, approval_id: approvalId, decision});
      } else {
        send({v: 1, type: 'approval', id, approval_id: approvalId, decision});
      }
      pendingApprovals.delete(approvalId);
      return id;
    },
    shutdown,
  };
}
