import React, {useCallback, useLayoutEffect, useRef, useState} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import type {PermissionPanel as PermissionPanelState} from './use-permission-panel.js';
import type {PermissionGrant, PermissionPersistent} from './protocol.js';

type Action = 'previous' | 'next' | 'back' | 'revoke' | 'refresh' | 'retry';
type Revocable = {kind: 'grant'; grant: PermissionGrant} | {kind: 'rule'; entry: PermissionPersistent};
const sources: Record<string, string> = {cli: '本进程 CLI', 'user-policy': '用户策略默认', default: '默认 ask'};

export function PermissionPanel({panel, columns, rows, palette, active, status, onReturn}: {
  panel: PermissionPanelState; columns: number; rows: number; palette: Palette; active: boolean; status: string; onReturn?: () => void;
}) {
  const [selected, setSelected] = useState(0);
  const [confirmRule, setConfirmRule] = useState(false);
  const [button, setButton] = useState<Action | null>(null);
  const [top, setTop] = useState(0);
  const targets = [useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([])];
  const press = useRef<string | null>(null);
  const grants = panel.overview?.grants ?? [];
  const persistent = panel.overview?.persistent ?? [];
  const revocable: Revocable[] = [
    ...grants.map(grant => ({kind: 'grant' as const, grant})),
    ...persistent.filter(entry => entry.source === 'user' && entry.revocable).map(entry => ({kind: 'rule' as const, entry})),
  ];
  useLayoutEffect(() => {
    setSelected(value => Math.max(0, Math.min(revocable.length - 1, value)));
    if (!revocable.length) setConfirmRule(false);
  }, [revocable.length]);
  const item = revocable[selected];
  const buttons: {action: Action; label: string}[][] = [
    [{action: 'previous', label: '前一条'}, {action: 'next', label: '后一条'}, {action: 'back', label: '返回'}],
    [
      ...(item && !panel.loading ? [{action: 'revoke' as const,
        label: panel.revoking ? '撤销中…' : item.kind === 'rule' ? (confirmRule ? '确认撤销规则' : '撤销所选') : '撤销所选'}] : []),
      {action: panel.problem ? 'retry' as const : 'refresh' as const, label: panel.problem ? '重试' : '刷新'},
    ],
  ];
  const bodyHeight = Math.max(4, rows - 4 - buttons.length);
  const lines: string[] = [];
  const autonomy = panel.overview?.autonomy;
  if (autonomy) {
    lines.push(`档位：${autonomy.level}（${autonomy.label}） · 来源：${sources[autonomy.source] ?? autonomy.source}${autonomy.cap ? ` · 项目上限 ${autonomy.cap}` : ' · 无项目上限'}`);
    if (autonomy.requested !== autonomy.level) lines.push(`请求档位：${autonomy.requested}（受项目上限收紧为 ${autonomy.level}）`);
  } else if (!panel.loading) lines.push('尚未读到权限信息；刷新重试。');
  lines.push('');
  lines.push(`进程临时授权 · ${grants.length} 条 · 本进程内有效，退出后失效`);
  if (!grants.length) lines.push('  （没有进程内临时授权）');
  grants.forEach((grant, index) => lines.push(`${item?.kind === 'grant' && item.grant.id === grant.id ? '›' : ' '} ${index + 1}. ${grant.kind}${grant.tool ? ` · ${grant.tool}` : ''} · ${grant.target}`));
  lines.push('');
  lines.push(`持久策略 · ${persistent.length} 条 · 文件生效；user 可撤销，project 只能收紧`);
  if (!persistent.length) lines.push('  （没有策略规则）');
  for (const entry of persistent.slice(0, 20)) {
    const marked = item?.kind === 'rule' && item.entry.id === entry.id ? '›' : ' ';
    const tag = entry.revocable ? '可撤销' : '不可撤销';
    lines.push(`${marked} ${entry.rule} · ${tag}${entry.revocable ? (confirmRule && item?.kind === 'rule' && item.entry.id === entry.id ? ' · 待确认' : '') : '（修改仓库文件）'}`);
  }
  if (persistent.length > 20) lines.push(`  … 另有 ${persistent.length - 20} 条`);
  const files = [...new Set(persistent.map(entry => `[${entry.source}] ${entry.path}`).filter(Boolean))];
  if (files.length) {
    lines.push(`文件：${files.join('；')}`);
    lines.push('撤销方式：user 规则可在此撤销（写入用户策略文件）；project 规则需修改仓库文件；reset 只撤销本进程授权。');
  }
  if (panel.overview?.error) {lines.push(''); lines.push(`策略错误（保留上一份策略）：${panel.overview.error}`);}
  const body = textLines(lines.join('\n'), Math.max(1, columns - 5));
  const statusText = panel.loading ? (panel.revoking ? '正在撤销并刷新剩余权限…' : '正在读取权限来源…')
    : panel.problem || panel.flash || (confirmRule ? '再次确认：撤销会写入用户策略文件并立即重载；Esc 取消' : status);
  const scroll = (delta: number) => setTop(value => Math.max(0, Math.min(Math.max(0, body.length - bodyHeight + 3), value + delta)));
  const move = (delta: number) => {setConfirmRule(false); setSelected(value => Math.max(0, Math.min(revocable.length - 1, value + delta)));};
  const act = (action: Action) => {
    setButton(null);
    if (action === 'back') (onReturn ?? panel.close)();
    else if (action === 'previous' || action === 'next') move(action === 'previous' ? -1 : 1);
    else if (action === 'revoke' && item && !panel.loading) {
      if (item.kind === 'grant') panel.revoke(item.grant.id);
      else if (confirmRule) {setConfirmRule(false); panel.revokeRule(item.entry.id);}
      else setConfirmRule(true);
    }
    else if (!panel.loading && (action === 'refresh' || action === 'retry')) {setConfirmRule(false); panel.refresh();}
  };
  const layout = `${columns}:${rows}:${selected}:${revocable.length}:${confirmRule}:${panel.revoking ?? ''}:${buttons.flat().map(entry => entry.action).join()}`;
  useTranscriptMouse({enabled: active, top: 3, bottom: rows - buttons.length - 1, left: 1, right: columns,
    onScroll: scroll, onPointer: event => {
      const hit = targets.flatMap(ref => ref.current).find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
      const key = hit ? `${layout}:${hit.action}` : null;
      if (event.type === 'press' && event.button === 0) press.current = key;
      else if (event.type === 'release') {const prior = press.current; press.current = null; if (hit && prior === key) act(hit.action);}
      else press.current = null;
      return Boolean(hit);
    }});
  const handle = useRef<(input: string, key: Key) => void>(() => {});
  handle.current = (_input, key) => {
    if (!active || key.eventType === 'release' || key.ctrl || key.meta) return;
    if (key.escape) {if (confirmRule) setConfirmRule(false); else (onReturn ?? panel.close)(); return;}
    if (key.tab) {
      const actions = buttons.flat().map(entry => entry.action); const at = button ? actions.indexOf(button) : -1;
      setButton(actions[(at + (key.shift ? actions.length - 1 : 1) + actions.length) % actions.length] ?? null); return;
    }
    if (key.return) {
      if (button) act(button);
      else if (item) act('revoke');
      return;
    }
    if (key.upArrow || key.downArrow) move(key.upArrow ? -1 : 1);
    if (key.pageUp || key.pageDown) scroll((key.pageUp ? -1 : 1) * (bodyHeight - 3));
  };
  useInput(useCallback((input, key) => handle.current(input, key), []));
  return <Box width={columns} height={rows} flexDirection="column" backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.foreground} bold>{fitText(`权限 · ${grants.length} 条进程授权 · ${persistent.filter(entry => entry.revocable).length} 条可撤销策略`, columns)}</Text>
    <Text color={palette.warning}>{fitText(statusText, columns)}</Text>
    <ReadingPane title="来源与有效期" lines={body} top={top} height={bodyHeight} columns={columns} palette={palette}/>
    {buttons.map((group, index) => <ActionButtons key={index} buttons={group} selected={button} columns={columns} height={1} palette={palette} targets={targets[index]!}/>)}
    <Text color={palette.secondary}>{fitText('↑↓ 选可撤销项 · Tab 选动作 · 撤销后立即影响后续工具调用', columns)}</Text>
    <Text color={palette.secondary}>{fitText('持久 user 规则撤销需二次确认 · /permissions reset 只撤销本进程授权', columns)}</Text>
  </Box>;
}
