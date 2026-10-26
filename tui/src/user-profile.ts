import {existsSync, mkdirSync, readFileSync, renameSync, writeFileSync} from 'node:fs';
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

const STATUSES: UserPreferenceStatus[] = ['confirmed', 'proposed', 'stale'];
const KINDS = ['preference', 'constraint', 'goal', 'fact'];
const SCOPES = ['global', 'project', 'workflow'];

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
  writeFileSync(temporary, JSON.stringify({version: 2, enabled: profile.enabled, entries: profile.entries}, null, 2), 'utf8');
  renameSync(temporary, file);
}
