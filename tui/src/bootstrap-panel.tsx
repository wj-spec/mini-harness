import React, {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react';
import {Box, Text, measureElement, useInput, type DOMElement, type Key} from 'ink';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import type {ModelAction, ModelDraft, ModelOverview} from './model-settings.js';

type Field = 'service' | 'api_key' | 'base_url' | 'model' | 'trust_env';
type Action = 'save-test' | 'save' | 'cancel' | 'activate' | 'quit' | 'enter' | 'back' | 'later' | 'demo';
const defaultFields: Field[] = ['service', 'api_key'];
const customFields: Field[] = ['service', 'api_key', 'base_url', 'model', 'trust_env'];
const labels: Record<Field, string> = {
  service: '模型服务', api_key: 'API Key', base_url: '服务地址', model: '模型名', trust_env: '环境代理',
};
const descriptions: Record<Field, string> = {
  service: '默认服务通常只需填写 Key；需要其他兼容服务时选择“自定义”。',
  api_key: 'Key 保存在本机，不会显示在日志和审批预览中。',
  base_url: '兼容 Chat Completions 的服务根地址，通常以 /v1 结尾。',
  model: '按服务商实际提供的名称填写，并选择支持工具调用的模型。',
  trust_env: '开启后使用环境代理；默认关闭。',
};

// 首次启动页：1 连接模型 → 2 确认项目；隐藏任务侧栏与技术仪表。
export function BootstrapPanel({workspace, defaults, overview, problem, problemField, loading, activating,
  active, columns, rows, palette, onRequest, onCancel, onActivate, onQuit,
  onLater, onDemo, demoStarting = false, demoProblem = ''}: {
  workspace: string; defaults: {base_url: string; model: string};
  overview: ModelOverview | null; problem: string; problemField: string; loading: boolean; activating: boolean;
  active: boolean; columns: number; rows: number; palette: Palette;
  onRequest: (action: ModelAction | 'list', draft: ModelDraft) => void;
  onCancel: () => void; onActivate: () => void; onQuit: () => void;
  onLater: () => void; onDemo?: () => void; demoStarting?: boolean; demoProblem?: string;
}) {
  const [step, setStep] = useState<'connect' | 'project'>('connect');
  const [mode, setMode] = useState<'default' | 'custom'>('default');
  const modeReady = useRef(false);
  const [draft, setDraft] = useState<ModelDraft | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [tested, setTested] = useState(false);
  const [testFailed, setTestFailed] = useState(false);
  const [phase, setPhase] = useState<'idle' | 'saving' | 'testing'>('idle');
  const [index, setIndex] = useState(0);
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState<'skip' | 'quit' | null>(null);
  const [notice, setNotice] = useState('');
  const [errorField, setErrorField] = useState('');
  const [top, setTop] = useState(0);
  const targets = useRef<ActionTarget<Action>[]>([]);
  const secondaryTargets = useRef<ActionTarget<Action>[]>([]);
  const fieldElements = useRef(new Map<Field, DOMElement>());
  const fieldTargets = useRef<ActionTarget<Field>[]>([]);
  const fieldPress = useRef<Field | null>(null);
  const press = useRef<Action | null>(null);
  const pendingSave = useRef<string | null>(null);
  const chainTest = useRef(false);
  const value = draft ?? {model: overview?.model ?? '', base_url: overview?.base_url ?? '',
    api_key: '', trust_env: overview?.trust_env ?? false, revision: overview?.revision};
  const valueRef = useRef(value);
  valueRef.current = value;
  const requestRef = useRef(onRequest);
  requestRef.current = onRequest;
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;
  const signature = (d: ModelDraft) => JSON.stringify([d.model, d.base_url, d.api_key, d.trust_env]);
  useEffect(() => {
    if (!overview || modeReady.current) return;
    modeReady.current = true;
    const same = overview.base_url.replace(/\/$/, '') === defaults.base_url.replace(/\/$/, '') &&
      overview.model === defaults.model;
    setMode(same ? 'default' : 'custom');
  }, [overview, defaults.base_url, defaults.model]);
  useEffect(() => {
    const message = overview?.message ?? '';
    if (message.startsWith('已保存') && pendingSave.current) {
      setSaved(pendingSave.current);
      pendingSave.current = null;
      setTested(false); setTestFailed(false); setPhase('idle');
      if (chainTest.current) {
        chainTest.current = false;
        setPhase('testing');
        requestRef.current('test', {...valueRef.current, revision: overview!.revision});
      }
    }
    if (message.startsWith('连接检测通过')) {setTested(true); setTestFailed(false); setPhase('idle');}
    else if (message.startsWith('检测未通过') || message.startsWith('连接检测失败') || message.startsWith('服务返回格式')) {
      setTested(false); setTestFailed(true); setPhase('idle');
    }
  }, [overview]);
  useEffect(() => {if (problem) {setPhase('idle'); setEditing(false);}}, [problem]);
  const fields = mode === 'default' ? defaultFields : customFields;
  const currentField: Field | undefined = fields[Math.min(index, fields.length - 1)];
  useEffect(() => {
    if (!problemField) return;
    if ((['base_url', 'model', 'trust_env'] as string[]).includes(problemField) && mode === 'default') {
      setMode('custom');
      return;
    }
    const position = fields.indexOf(problemField as Field);
    if (position >= 0) setIndex(position);
  }, [problemField, mode, fields]);
  const mainButtons: {action: Action; label: string}[] = loading
    ? [{action: 'cancel', label: '取消操作'}]
    : step === 'project'
      ? [{action: 'enter', label: columns >= 48 ? '进入项目' : '进入'},
        {action: 'back', label: columns >= 48 ? '返回模型配置' : '返回'}]
      : columns >= 60
        ? [{action: 'save-test', label: '保存并检测'}, {action: 'save', label: '仅保存'}, {action: 'activate', label: '进入项目'}, {action: 'quit', label: '退出'}]
        : columns >= 48
          ? [{action: 'save-test', label: '保存检测'}, {action: 'save', label: '仅保存'}, {action: 'activate', label: '进入'}, {action: 'quit', label: '退出'}]
          : [{action: 'save-test', label: '保存检测'}, {action: 'save', label: '仅存'}, {action: 'activate', label: '进入'}];
  const secondaryButtons: {action: Action; label: string}[] = step === 'connect' && !loading
    ? [{action: 'later', label: '稍后配置'}, ...(onDemo ? [{action: 'demo' as const, label: '先体验 Demo'}] : [])]
    : [];
  const buttons = [...mainButtons, ...secondaryButtons];
  const shown = (field: Field) => {
    if (field === 'service') return mode === 'default' ? '默认 · DeepSeek' : '自定义 OpenAI 兼容';
    if (field === 'api_key') return value.api_key ? '已填写（隐藏）' : overview?.key_present ? '留空保留已保存的 Key' : '尚未填写';
    if (field === 'trust_env') return value.trust_env ? '开启' : '关闭';
    return String(value[field]);
  };
  const dirty = Boolean(draft) && signature(value) !== saved;
  const setField = (patch: Partial<ModelDraft>) => {
    setDraft({...value, ...patch}); setTested(false); setTestFailed(false); setConfirm(null);
    setNotice(''); setErrorField('');
  };
  const change = (text: string) => setField({[currentField!]: text} as Partial<ModelDraft>);
  const toggleService = () => {
    if (mode === 'default') {setMode('custom'); setIndex(0); return;}
    setMode('default');
    setDraft({...value, base_url: defaults.base_url, model: defaults.model});
    setIndex(0); setTested(false); setTestFailed(false); setConfirm(null); setNotice(''); setErrorField('');
  };
  const activateField = (field: Field) => {
    const position = fields.indexOf(field);
    if (position < 0) return;
    setIndex(position); setConfirm(null); setNotice('');
    if (field === 'service') {toggleService(); return;}
    if (field === 'trust_env') {setField({trust_env: !value.trust_env}); return;}
    if (overview) setEditing(true);
  };
  useLayoutEffect(() => {
    fieldTargets.current = [...fieldElements.current.entries()].flatMap(([action, element]) => {
      const {x, y, width, height} = measureElement(element);
      return [{action, top: y + 1, bottom: y + height, left: x + 1, right: x + width}];
    });
  });
  const focus = (field: string) => {
    if (field === 'base_url' || field === 'model' || field === 'trust_env') setMode('custom');
    const next = (field === 'base_url' || field === 'model' || field === 'trust_env' ? customFields : fields).indexOf(field as Field);
    if (next >= 0) setIndex(next);
  };
  const validate = (): {field?: string; message: string} | null => {
    if (!overview) return {message: '正在读取配置，请稍候'};
    if (!value.api_key.trim() && !overview.key_present) return {field: 'api_key', message: '请填写 API Key'};
    if (mode === 'custom') {
      if (!value.model.trim()) return {field: 'model', message: '请填写模型名'};
      const base = value.base_url.trim();
      if (!/^https?:\/\//i.test(base)) return {field: 'base_url', message: '服务地址需要以 http:// 或 https:// 开头'};
      if (/[?#]/.test(base) || /^https?:\/\/[^/]*@/.test(base)) return {field: 'base_url', message: '地址不能包含账号密码、查询参数或片段'};
      if (!value.api_key.trim() && base.replace(/\/$/, '') !== overview.base_url.replace(/\/$/, '')) {
        return {field: 'api_key', message: '服务地址变化，请重新填写该服务的 API Key'};
      }
    }
    return null;
  };
  const send = (action: 'save') => {
    if (!overview) {setNotice('正在读取配置，请稍候'); return;}
    const failure = validate();
    if (failure) {
      setNotice(failure.message); setErrorField(failure.field ?? '');
      if (failure.field) focus(failure.field);
      return;
    }
    pendingSave.current = signature(value);
    setPhase('saving'); setNotice(''); setErrorField(''); setConfirm(null);
    requestRef.current(action, {...value, revision: overview.revision});
  };
  const enterProject = () => {
    if (loading || activating) return;
    if (!overview) {setNotice('正在读取配置，请稍候'); return;}
    if (tested) {setStep('project'); setIndex(fields.length); setConfirm(null); setNotice(''); return;}
    if (saved) {
      if (confirm !== 'skip') {
        setConfirm('skip');
        setNotice('已保存但尚未检测：再次选择“进入项目”将跳过检测，首个真实请求失败后可返回修复');
        return;
      }
      setConfirm(null); setNotice(''); setStep('project'); setIndex(fields.length); return;
    }
    setNotice('先保存配置（推荐保存并检测）再进入项目');
  };
  const act = (action: Action) => {
    if (!active || activating) return;
    if (action === 'cancel') {cancelRef.current(); return;}
    if (action === 'later') {onLater(); return;}
    if (action === 'demo') {onDemo?.(); return;}
    if (action === 'quit') {
      if (dirty && confirm !== 'quit') {setConfirm('quit'); setNotice('有未保存编辑；再次选择退出将丢弃'); return;}
      onQuit(); return;
    }
    if (step === 'project') {
      if (action === 'enter' || action === 'activate') onActivate();
      else if (action === 'back') {setStep('connect'); setIndex(fields.length); setNotice('');}
      return;
    }
    if (loading) return;
    if (action === 'activate') {enterProject(); return;}
    if (action === 'save' || action === 'save-test') {
      chainTest.current = action === 'save-test';
      send('save');
    }
  };
  const handler = useRef<(input: string, key: Key) => void>(() => {});
  handler.current = (input, key) => {
    if (!active || key.eventType === 'release' || activating) return;
    if (key.escape) {
      if (editing) setEditing(false);
      else if (confirm) {setConfirm(null); setNotice('');}
      else if (step === 'project') {setStep('connect'); setIndex(fields.length);}
      else if (loading) cancelRef.current();
      else setNotice('');
      return;
    }
    if (loading) return;
    if (editing) {
      if (key.ctrl && input === 'u') {change(''); return;}
      if (key.ctrl || key.meta) return;
      if (key.return) {setEditing(false); return;}
      if (key.tab) {setEditing(false); setIndex((index + 1) % (fields.length + buttons.length)); return;}
      const text = currentField !== 'service' && currentField !== 'trust_env' ? value[currentField] : '';
      if (key.backspace || key.delete) {change(Array.from(text).slice(0, -1).join('')); return;}
      if (!key.upArrow && !key.downArrow && !key.leftArrow && !key.rightArrow && input) {
        change((text + input.replace(/[\x00-\x1f\x7f]/g, '')).slice(0, 4096));
      }
      return;
    }
    if (key.ctrl || key.meta) return;
    if (key.tab || key.leftArrow || key.rightArrow) {
      setConfirm(null); setNotice('');
      const count = fields.length + buttons.length;
      setIndex((index + (key.shift || key.leftArrow ? -1 : 1) + count) % count); return;
    }
    if (key.upArrow || key.downArrow) {setTop(current => Math.max(0, current + (key.upArrow ? -1 : 1))); return;}
    if (key.return) {
      if (index < fields.length) {
        activateField(currentField!);
      } else {
        const button = buttons[index - fields.length];
        if (button) act(button.action);
      }
    }
  };
  useInput(useCallback((input, key) => handler.current(input, key), []), {isActive: active});
  useTranscriptMouse({enabled: active, top: 1, bottom: rows, left: 1, right: columns,
    onScroll: delta => setTop(current => Math.max(0, current + delta)), onPointer: event => {
      if (step !== 'project') {
        const fieldHit = fieldTargets.current.find(target => event.row >= target.top && event.row <= target.bottom &&
          event.column >= target.left && event.column <= target.right);
        if (event.type === 'press' && event.button === 0) fieldPress.current = fieldHit?.action ?? null;
        else if (event.type === 'release') {
          const prior = fieldPress.current; fieldPress.current = null;
          if (fieldHit && prior === fieldHit.action) activateField(fieldHit.action);
        } else fieldPress.current = null;
        if (fieldHit) return true;
      } else fieldPress.current = null;
      const hit = [...targets.current, ...secondaryTargets.current].find(target => event.row >= target.top && event.row <= target.bottom &&
        event.column >= target.left && event.column <= target.right);
      if (event.type === 'press' && event.button === 0) press.current = hit?.action ?? null;
      else if (event.type === 'release') {
        const prior = press.current; press.current = null;
        if (hit && hit.action === prior) {
          const position = buttons.findIndex(button => button.action === hit.action);
          setIndex(fields.length + position); act(hit.action);
        }
      }
      return Boolean(hit);
    }});
  const compact = rows < 20;
  const projectName = workspace.split(/[\\/]/).filter(Boolean).at(-1) ?? workspace;
  const shortWorkspace = workspace.length > Math.max(12, columns - 8)
    ? '…' + workspace.slice(-(Math.max(12, columns - 8) - 1)) : workspace;
  const stepLine = ['1 连接模型', '2 确认项目', '3 开始任务'];
  const details = step === 'project'
    ? [
        '连接：' + (tested ? '检测通过' : '已保存，尚未检测'),
        '项目：' + projectName, '目录：' + workspace,
        '我可以阅读项目、提出修改，并在需要时请求你审批操作。',
        '确认目录不等于授权读取全部文件或执行命令。',
        '进入项目会初始化真实模型、工具与会话；失败会留在配置页。',
      ].join('\n')
    : overview ? [
        '当前：' + overview.provider + ' / ' + overview.model + ' · ' + overview.base_url,
        '来源：模型 ' + overview.sources.model + '；地址 ' + overview.sources.base_url + '；Key ' + overview.sources.api_key + '；代理 ' + overview.sources.trust_env,
        '保存位置（用户配置 · 所有项目通用）：' + overview.path + '；文件中的模型：' + (overview.saved_model || '尚未保存'),
        'Key ' + (overview.key_present ? '已配置，留空保留；不会回传原值。' : '尚未配置；首次连接通常只需填写 API Key。'),
        '保存用户配置与独立凭据并经审批；检测只发送固定 Hi 消息，可能产生少量费用，不发送代码或对话。',
        '检测通过只代表这一次最小请求成功；进入项目后才会初始化真实模型与工具。',
        'Tab 在字段与操作间切换；Enter 编辑/确认；Ctrl+U 清空；Esc 结束编辑。',
      ].join('\n') : '正在读取当前配置…';
  const fixedRows = 9 + (compact ? 1 : fields.length) + (secondaryButtons.length ? 1 : 0);
  const paneRows = Math.max(0, rows - fixedRows);
  const lines = textLines(details, Math.max(1, columns - 5));
  const fieldError = problemField && problemField === currentField ? problem
    : errorField && errorField === currentField ? notice : '';
  const status = demoProblem || (demoStarting ? '正在启动 Demo（本地模型，不调用真实服务）…'
    : activating ? '正在初始化真实运行环境…（可能需要几秒）'
    : problem || notice ||
      (loading ? (phase === 'testing' ? '正在检测…按 Esc 取消' : '正在保存…按 Esc 取消')
        : confirm === 'skip' ? '已保存但尚未检测：再次选择“进入项目”确认跳过检测'
          : confirm === 'quit' ? '再次选择“退出”确认丢弃未保存编辑'
            : overview?.message
              ? (testFailed && saved ? '已保存；' + overview.message : overview.message)
              : dirty ? '未保存的编辑保留在本次界面' : '保存与检测仍分别经过审批'));
  const hint = step === 'project'
    ? (columns < 48 ? 'Enter确认 Esc返回' : 'Tab 选择 · Enter 确认 · Esc 返回模型配置 · Ctrl+Q 退出')
    : loading ? 'Esc 取消当前操作'
      : columns < 48 ? 'Tab换项 Enter确认 Ctrl+Q退出' : 'Tab 切换 · Enter 确认 · Esc 返回 · Ctrl+Q 退出';
  return <Box width={columns} height={rows} flexDirection="column" overflow="hidden" backgroundColor={palette.background}>
    <Text bold color={palette.foreground}>{fitText('Mini · 陪你完成第一个编码任务', columns)}</Text>
    <Text color={palette.secondary}>{compact
      ? '步骤 ' + (step === 'project' ? '2/2 · 确认项目' : '1/2 · 连接模型')
      : stepLine.map((text, position) =>
        <Text key={text} color={position === (step === 'project' ? 1 : 0) ? palette.accent : palette.secondary}>
          {text}{position < 2 ? '  →  ' : ''}</Text>)}</Text>
    <Text color={palette.secondary}>{fitText('项目  ' + projectName + '  ·  ' + shortWorkspace, columns)}</Text>
    {step === 'project'
      ? <Text color={palette.foreground}>{fitText('准备进入：' + projectName, columns)}</Text>
      : compact
        ? (index < fields.length ? <Box height={1} ref={element => {
              if (element) fieldElements.current.set(fields[index]!, element); else fieldElements.current.delete(fields[index]!);
            }}><Text color={palette.accent}>{fitText((editing ? '编辑中 · ' : '') +
              labels[fields[index]!] + '  ' + shown(fields[index]!), columns)}</Text></Box>
          : <Text color={palette.accent}>{fitText('操作 · ' +
              buttons[Math.min(index - fields.length, buttons.length - 1)]!.label, columns)}</Text>)
        : <>{fields.map((field, position) => <Box key={field} height={1}
            ref={element => {if (element) fieldElements.current.set(field, element); else fieldElements.current.delete(field);}}>
            <Text color={position === index ? palette.accent : palette.foreground}>
            {fitText((position === index ? '› ' : '  ') + labels[field] + '  ' + shown(field), columns)}</Text></Box>)}</>}
    <Text color={fieldError ? palette.warning : palette.secondary}>{fitText(fieldError ? '  ' + fieldError
      : compact && step !== 'project' ? descriptions[fields[Math.min(index, fields.length - 1)]!]
        : step === 'project' ? '确认目录不等于授权读取全部文件或执行命令。'
          : descriptions[currentField ?? 'api_key'], columns)}</Text>
    {paneRows >= 3 ? <ReadingPane title={step === 'project' ? '项目确认' : '配置说明与来源'} lines={lines}
      top={Math.min(top, Math.max(0, lines.length - paneRows + 3))} height={paneRows} columns={columns} palette={palette}/> : null}
    <ActionButtons buttons={mainButtons} selected={index >= fields.length && index - fields.length < mainButtons.length
      ? mainButtons[index - fields.length]!.action : null}
      columns={columns} height={2} palette={palette} targets={targets} paddingX={columns < 48 ? 0 : 1}/>
    {secondaryButtons.length ? <ActionButtons buttons={secondaryButtons} selected={index - fields.length >= mainButtons.length
      ? secondaryButtons[Math.min(index - fields.length - mainButtons.length, secondaryButtons.length - 1)]!.action : null}
      columns={columns} height={1} palette={palette} targets={secondaryTargets} paddingX={columns < 48 ? 0 : 1}/> : null}
    <Box height={2} flexDirection="column"><Text color={problem || notice || confirm || testFailed ? palette.warning : palette.secondary}>
      {textLines(status, columns).slice(0, 2).map(line => line.spans.map(span => span.text).join('')).join('\n')}</Text></Box>
    <Text color={palette.secondary}>{fitText(hint, columns)}</Text>
  </Box>;
}
