import {readFileSync} from 'node:fs';
import path from 'node:path';
import {looksSensitive, type UserPreference, type UserProfile} from './user-profile.js';

const CUES = [
  /以后/u, /今后/u, /下次/u, /每次/u, /记住/u, /始终/u, /一向/u, /习惯/u,
  /偏好/u, /我喜欢/u, /我倾向/u, /不要/u, /务必/u, /统一/u,
  /\bfrom now on\b/iu, /\balways\b/iu, /\bremember\b/iu, /\bprefer\b/iu, /\bnever\b/iu, /\bdon't\b/iu,
];
const MAX_PROPOSALS = 2;
const MAX_TEXT = 200;
const PROPOSAL_TTL_DAYS = 30;

/** 只读取用户消息；工具/助手/系统消息不作为偏好证据。 */
export function readSessionUserMessages(sessionPath: string): {text: string; index: number}[] {
  const file = path.join(sessionPath, 'events.jsonl');
  let raw: string;
  try {raw = readFileSync(file, 'utf8');} catch {return [];}
  const messages: {text: string; index: number}[] = [];
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue;
    try {
      const event = JSON.parse(line) as {type?: string; message?: {role?: string; content?: unknown}};
      if (event.type !== 'message' || event.message?.role !== 'user' || typeof event.message.content !== 'string') continue;
      messages.push({text: event.message.content, index: messages.length});
    } catch {continue;}
  }
  return messages;
}

function normalize(text: string): string {
  return text.replace(/[\s，。！？、；：,.!?;:（）()【】[\]"'“”‘’]/gu, '').toLowerCase();
}

function triggersFor(text: string): string[] {
  const groups = [
    ['解释', '说明', '回答', '结论', '分析'],
    ['修改', '实现', '重构', '修复', '增加', '新增', '方案', '编辑'],
    ['测试', '验证', '诊断', '复现', '失败', '调试'],
    ['中文', '简洁', '回复', '语气', '风格', '注释'],
  ];
  const found: string[] = [];
  for (const group of groups) {
    if (group.some(word => text.includes(word))) found.push(...group);
  }
  return [...new Set(found)].slice(0, 8);
}

function cueSentence(text: string): string | null {
  for (const sentence of text.split(/[。！？!?；;\n]+/u)) {
    const trimmed = sentence.trim();
    if (trimmed.length < 8 || trimmed.length > MAX_TEXT) continue;
    if (CUES.some(cue => cue.test(trimmed))) return trimmed;
  }
  return null;
}

function proposalId(now: Date, text: string): string {
  return `pref-${now.getTime().toString(36)}-${Math.random().toString(36).slice(2, 8)}-${normalize(text).slice(0, 4)}`;
}

/** 从会话中的显式长期偏好句生成 proposed 条目；不自动注入，需 /about confirm。 */
export function proposePreferences(
  profile: UserProfile, messages: {text: string}[], sessionId = '', now = new Date(),
): UserPreference[] {
  const seen = new Set(profile.entries.map(entry => normalize(entry.text)));
  const proposals: UserPreference[] = [];
  for (const message of messages) {
    const text = cueSentence(message.text);
    if (!text || looksSensitive(text) || seen.has(normalize(text))) continue;
    seen.add(normalize(text));
    proposals.push({
      text,
      source: 'observation',
      status: 'proposed',
      id: proposalId(now, text),
      kind: 'preference',
      scope: 'global',
      triggers: triggersFor(text),
      evidence: [sessionId ? `session:${sessionId}` : 'session'],
      confidence: 0.6,
      created_at: now.toISOString(),
      updated_at: now.toISOString(),
      expires_at: new Date(now.getTime() + PROPOSAL_TTL_DAYS * 24 * 60 * 60 * 1000).toISOString(),
    });
    if (proposals.length >= MAX_PROPOSALS) break;
  }
  return proposals;
}


export type MergeResult = {
  entries: UserPreference[];
  added: number;
  updated: number;
  skipped: number;
  conflicts: number;
};

/** 只自动合并 proposed：confirmed/stale 不静默改写；相同文本 NOOP。 */
export function mergeProposals(profile: UserProfile, proposals: UserPreference[], now = new Date()): MergeResult {
  const entries = [...profile.entries];
  let added = 0;
  let updated = 0;
  let skipped = 0;
  let conflicts = 0;
  for (const proposal of proposals) {
    const normalized = normalize(proposal.text);
    if (!normalized) {skipped += 1; continue;}
    const exact = entries.findIndex(entry => normalize(entry.text) === normalized);
    if (exact >= 0) {skipped += 1; continue;}
    const similar = entries.findIndex(entry => {
      if (entry.status !== 'proposed') return false;
      const current = normalize(entry.text);
      return current.includes(normalized) || normalized.includes(current);
    });
    if (similar >= 0) {
      entries[similar] = {
        ...entries[similar]!,
        text: proposal.text,
        triggers: [...new Set([...(entries[similar]!.triggers ?? []), ...(proposal.triggers ?? [])])].slice(0, 20),
        evidence: [...new Set([...(entries[similar]!.evidence ?? []), ...(proposal.evidence ?? [])])].slice(0, 20),
        updated_at: now.toISOString(),
      };
      updated += 1;
      continue;
    }
    const confirmedConflict = entries.some(entry => {
      if (entry.status === 'proposed') return false;
      const current = normalize(entry.text);
      return current === normalized || current.includes(normalized) || normalized.includes(current);
    });
    if (confirmedConflict) {
      conflicts += 1;
      skipped += 1;
      continue;
    }
    entries.push(proposal);
    added += 1;
  }
  return {entries, added, updated, skipped, conflicts};
}
