import {chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync} from 'node:fs';
import {homedir} from 'node:os';
import path from 'node:path';

export type UserPreferenceStatus = 'confirmed' | 'proposed' | 'stale';
export type UserPreference = {
  text: string;
  source: string;
  status: UserPreferenceStatus;
  id?: string;
  kind?: 'preference' | 'constraint' | 'goal' | 'fact';
  scope?: 'global' | 'project' | 'workflow';
  triggers?: string[];
  evidence?: string[];
  confidence?: number;
  created_at?: string;
  updated_at?: string;
  last_confirmed_at?: string;
  expires_at?: string;
};
export type UserProfile = {enabled: boolean; entries: UserPreference[]};
export const PROFILE_CAPACITY = 50;

const STATUSES: UserPreferenceStatus[] = ['confirmed', 'proposed', 'stale'];
const KINDS = ['preference', 'constraint', 'goal', 'fact'];
const SCOPES = ['global', 'project', 'workflow'];
const SENSITIVE = [
  /sk-[A-Za-z0-9]{16,}/,
  /gh[pousr]_[A-Za-z0-9]{20,}/,
  /AKIA[0-9A-Z]{16}/,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  /(?:api[_-]?key|password|secret|token)\s*[:=]\s*\S{8,}/iu,
  /Bearer\s+[A-Za-z0-9._-]{20,}/u,
];

/** 明显密钥/凭据不应进入偏好或长期记忆。 */
export function looksSensitive(text: string): boolean {
  return SENSITIVE.some(pattern => pattern.test(text));
}

export function userProfileFile(): string {
  const configured = process.env.VIBE_USER_PROFILE;
  return configured ? configured : path.join(homedir(), '.vibe', 'user-profile.json');
}

function strings(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out = value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0).map(item => item.trim());
  return out.length ? out.slice(0, 20) : undefined;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function valid(entry: unknown): entry is UserPreference {
  if (!entry || typeof entry !== 'object') return false;
  const value = entry as Record<string, unknown>;
  return typeof value.text === 'string' && value.text.trim().length > 0
    && typeof value.status === 'string' && STATUSES.includes(value.status as UserPreferenceStatus);
}

export function loadUserProfile(file = userProfileFile()): UserProfile {
  if (!existsSync(file)) return {enabled: true, entries: []};
  try {
    const payload = JSON.parse(readFileSync(file, 'utf8')) as {version?: unknown; enabled?: unknown; entries?: unknown};
    if (![1, 2].includes(Number(payload.version)) || !Array.isArray(payload.entries)) return {enabled: true, entries: []};
    const entries = payload.entries.filter(valid).map(entry => {
      const result: UserPreference = {
        text: entry.text.trim(),
        source: optionalString(entry.source) ?? 'user',
        status: entry.status,
      };
      if (entry.id) result.id = entry.id;
      if (typeof entry.kind === 'string' && KINDS.includes(entry.kind)) result.kind = entry.kind;
      if (typeof entry.scope === 'string' && SCOPES.includes(entry.scope)) result.scope = entry.scope;
      const triggers = strings(entry.triggers);
      if (triggers) result.triggers = triggers;
      const evidence = strings(entry.evidence);
      if (evidence) result.evidence = evidence;
      if (typeof entry.confidence === 'number' && Number.isFinite(entry.confidence)) result.confidence = Math.min(1, Math.max(0, entry.confidence));
      const created = optionalString(entry.created_at);
      if (created) result.created_at = created;
      const updated = optionalString(entry.updated_at);
      if (updated) result.updated_at = updated;
      const confirmed = optionalString(entry.last_confirmed_at);
      if (confirmed) result.last_confirmed_at = confirmed;
      const expires = optionalString(entry.expires_at);
      if (expires) result.expires_at = expires;
      return result;
    });
    return {enabled: payload.enabled !== false, entries};
  } catch {
    return {enabled: true, entries: []};
  }
}

export function saveUserProfile(file: string, profile: UserProfile): void {
  mkdirSync(path.dirname(file), {recursive: true});
  const temporary = `${file}.tmp`;
  writeFileSync(temporary, JSON.stringify({version: 2, enabled: profile.enabled, entries: profile.entries}, null, 2), {encoding: 'utf8', mode: 0o600});
  renameSync(temporary, file);
  try {chmodSync(file, 0o600);} catch { /* 非 POSIX 平台忽略 */ }
}


function expired(entry: UserPreference, now: Date): boolean {
  if (!entry.expires_at) return false;
  const value = Date.parse(entry.expires_at);
  return Number.isFinite(value) && value <= now.getTime();
}

export type PruneResult = {profile: UserProfile; removed: number; stale: number; overCapacity: number};

/** 读取路径维护：过期 proposed 清理，过期 confirmed 标记 stale；容量优先淘汰低价值 proposed，绝不静默删 confirmed。 */
export function pruneUserProfile(profile: UserProfile, now = new Date()): PruneResult {
  let removed = 0;
  let stale = 0;
  let entries = profile.entries.filter(entry => {
    if (entry.status === 'proposed' && expired(entry, now)) {removed += 1; return false;}
    return true;
  });
  entries = entries.map(entry => {
    if (entry.status === 'confirmed' && expired(entry, now)) {
      stale += 1;
      return {...entry, status: 'stale' as const, updated_at: now.toISOString()};
    }
    return entry;
  });
  const overCapacity = Math.max(0, entries.length - PROFILE_CAPACITY);
  if (!overCapacity) return {profile: {...profile, entries}, removed, stale, overCapacity: 0};
  const removable = entries
    .map((entry, index) => ({entry, index}))
    .filter(item => item.entry.status === 'proposed')
    .sort((left, right) => {
      const confidence = (left.entry.confidence ?? 0) - (right.entry.confidence ?? 0);
      if (confidence) return confidence;
      return (left.entry.updated_at ?? '').localeCompare(right.entry.updated_at ?? '');
    })
    .slice(0, overCapacity);
  const drop = new Set(removable.map(item => item.index));
  entries = entries.filter((_, index) => !drop.has(index));
  return {profile: {...profile, entries}, removed: removed + drop.size, stale, overCapacity: overCapacity - drop.size};
}


export function exportUserProfile(profile: UserProfile, file = userProfileFile()): {path: string; count: number} {
  const directory = path.join(path.dirname(file), 'exports');
  mkdirSync(directory, {recursive: true});
  const target = path.join(directory, `user-profile-${new Date().toISOString().replace(/[:.]/gu, '-')}.json`);
  const entries = profile.entries.filter(entry => !looksSensitive(entry.text));
  writeFileSync(target, JSON.stringify({version: 2, enabled: profile.enabled, entries}, null, 2), {encoding: 'utf8', mode: 0o600});
  return {path: target, count: entries.length};
}
