import {existsSync, mkdirSync, readFileSync, renameSync, writeFileSync} from 'node:fs';
import {homedir} from 'node:os';
import path from 'node:path';

export type UserPreferenceStatus = 'confirmed' | 'proposed';
export type UserPreference = {text: string; source: string; status: UserPreferenceStatus; updated_at?: string};
export type UserProfile = {enabled: boolean; entries: UserPreference[]};

export function userProfileFile(): string {
  const configured = process.env.VIBE_USER_PROFILE;
  return configured ? configured : path.join(homedir(), '.vibe', 'user-profile.json');
}

function valid(entry: unknown): entry is UserPreference {
  if (!entry || typeof entry !== 'object') return false;
  const value = entry as Record<string, unknown>;
  return typeof value.text === 'string' && value.text.trim().length > 0
    && (value.status === 'confirmed' || value.status === 'proposed');
}

export function loadUserProfile(file = userProfileFile()): UserProfile {
  if (!existsSync(file)) return {enabled: true, entries: []};
  try {
    const payload = JSON.parse(readFileSync(file, 'utf8')) as {version?: unknown; enabled?: unknown; entries?: unknown};
    if (payload.version !== 1 || !Array.isArray(payload.entries)) return {enabled: true, entries: []};
    const entries = payload.entries.filter(valid).map(entry => ({
      text: entry.text.trim(),
      source: typeof entry.source === 'string' && entry.source.trim() ? entry.source.trim() : 'user',
      status: entry.status,
      ...(entry.updated_at ? {updated_at: entry.updated_at} : {}),
    }));
    return {enabled: payload.enabled !== false, entries};
  } catch {
    return {enabled: true, entries: []};
  }
}

export function saveUserProfile(file: string, profile: UserProfile): void {
  mkdirSync(path.dirname(file), {recursive: true});
  const temporary = `${file}.tmp`;
  writeFileSync(temporary, JSON.stringify({version: 1, enabled: profile.enabled, entries: profile.entries}, null, 2), 'utf8');
  renameSync(temporary, file);
}
