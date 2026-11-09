import React, {useEffect, useRef, useState} from 'react';
import {Box, Text} from 'ink';
import {randomUUID} from 'node:crypto';
import {ActionButtons, type ActionTarget} from '../action-buttons.js';
import {useTranscriptMouse} from '../use-transcript-scroll.js';
import {fitText} from '../markdown.js';
import {copyToClipboard} from '../clipboard.js';
import type {Palette} from '../theme.js';
import {readChannel, sameOwner, writeChannel, type SidebarOwner} from './channel.js';
import {readSidebarSignal} from './signal.js';
import {isProcessAlive} from './lifecycle.js';
import {draftPath, formatExcerpt, type DraftRequest, type EvidenceExcerpt} from './draft-channel.js';

export const excerptBarRows = (columns: number, plan = false) => 1 + Math.ceil((plan ? 4 : 3) / Math.max(1, Math.min(3, Math.floor((columns - 1) / 9))));
export type ScopedExcerpt = EvidenceExcerpt & {sessionId: string | null; requestId: string | null};
export function ExcerptBar({excerpt, workspace, owner, columns, palette, onClear, copyText = copyToClipboard, extending = false, onExtend, active = true}: {
  excerpt: ScopedExcerpt; workspace: string; owner?: SidebarOwner; columns: number; palette: Palette; onClear: () => void;
  extending?: boolean; onExtend?: () => void; active?: boolean;
  copyText?: (text: string) => string | null;
}) {
  const [message, setMessage] = useState('已选内容 · 附回主区草稿');
  const [pending, setPending] = useState(false);
  const request = useRef<DraftRequest | null>(null);
  type Action = 'copy' | 'append' | 'clear' | 'extend';
  const targets = [useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([])];
  const buttons: {action: Action; label: string}[] = [{action: 'copy', label: '复制'}, {action: 'append', label: '附回'}, ...(onExtend ? [{action: 'extend' as const, label: '续选'}] : []), {action: 'clear', label: '清除'}];
  const perRow = Math.max(1, Math.min(3, Math.floor((columns - 1) / 9)));
  const groups = Array.from({length: Math.ceil(buttons.length / perRow)}, (_, index) => buttons.slice(index * perRow, (index + 1) * perRow));
  const pressed = useRef<string | null>(null);
  useEffect(() => {
    if (!pending || !request.current) return;
    const value = request.current; const deadline = Date.now() + 10_000;
    const timer = setInterval(() => {
      const reply = readChannel(draftPath(workspace, value.owner, true));
      if (reply?.id === value.id && reply.workspace === workspace && sameOwner(reply.owner, value.owner) && reply.sessionId === value.sessionId &&
          (reply.outcome === 'appended' || reply.outcome === 'error') && typeof reply.message === 'string') {
        setMessage(reply.message); setPending(false);
      } else if (Date.now() > deadline || !isProcessAlive(value.owner.parentPid)) {
        setMessage('未确认附回 · 选区保留，可重试或复制'); setPending(false);
      }
    }, 50);
    return () => clearInterval(timer);
  }, [pending, workspace]);
  useEffect(() => {
    if (!owner) return;
    let reported = false;
    const timer = setInterval(() => {
      if (!reported && !isProcessAlive(owner.parentPid)) {reported = true; setPending(false); setMessage('主区已退出 · 选区保留，可复制');}
    }, 300);
    return () => clearInterval(timer);
  }, [owner?.parentPid]);
  const activate = (action: Action) => {
    if (action === 'extend') {if (!pending) onExtend?.(); return;}
    if (action === 'copy') {setMessage(copyText(formatExcerpt(excerpt)) ? '已复制来源与选区' : '复制失败 · 选区保留'); return;}
    if (action === 'clear') {if (!pending) onClear(); else setMessage('正在等待附回回执 · 确认后可清除'); return;}
    if (pending) return;
    if (extending) {setMessage('请先拖选终点或取消续选'); return;}
    const live = owner ? readSidebarSignal(workspace, owner) : null;
    if (!owner || !isProcessAlive(owner.parentPid) || !live || live.scope?.sessionId !== excerpt.sessionId) {setMessage('主区未连接或会话已变化 · 可复制'); return;}
    if (Buffer.byteLength(excerpt.text) > 32_000) {setMessage('选区超过 32KB · 请缩小选区或复制'); return;}
    if (request.current && Date.now() - request.current.createdAt > 30_000) {setMessage('引用已过期 · 请重新选择或复制'); return;}
    request.current ??= {...excerpt, kind: 'append-excerpt', id: randomUUID(), workspace, owner, sessionId: excerpt.sessionId!, createdAt: Date.now()};
    try {writeChannel(draftPath(workspace, owner), request.current); setPending(true); setMessage('正在附回 · 尚未发送');}
    catch {setMessage('附回请求未送达 · 选区保留，可复制');}
  };
  useTranscriptMouse({enabled: active, top: 0, bottom: 0, left: 0, right: 0, onScroll: () => {}, onPointer: event => {
    const hit = targets.flatMap(group => group.current).find(item => event.row >= item.top && event.row <= item.bottom && event.column >= item.left && event.column <= item.right);
    if (event.type === 'wheel' || event.button !== 0) {pressed.current = null; return false;}
    const key = `${hit?.action}:${columns}`;
    if (event.type === 'press') pressed.current = hit ? key : null;
    if (event.type === 'release') {if (hit && pressed.current === key) activate(hit.action); pressed.current = null;}
    return Boolean(hit);
  }});
  return <Box flexDirection="column" height={excerptBarRows(columns, Boolean(onExtend))}>
    <Box paddingX={1}><Text>{fitText(extending ? '续选中 · 翻页后拖选终点' : message, columns - 2)}</Text></Box>
    {groups.map((buttons, index) => <ActionButtons key={index} buttons={buttons}
      selected={extending ? 'extend' : null} columns={columns} height={1} palette={palette} targets={targets[index]!}/>)}
  </Box>;
}
