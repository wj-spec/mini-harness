import {existsSync, mkdirSync, readFileSync, renameSync, writeFileSync} from 'node:fs';
import path from 'node:path';

export type ProfileEntry = {text: string; source: string; updated_at?: string};

export function projectProfilePath(workspace: string): string {
  return path.join(workspace, '.vibe', 'project-profile.json');
}

function valid(entry: unknown): entry is ProfileEntry {
  if (!entry || typeof entry !== 'object') return false;
  const value = entry as Record<string, unknown>;
  return typeof value.text === 'string' && value.text.trim().length > 0 && (value.source === undefined || typeof value.source === 'string');
}

export function loadProfile(file: string): ProfileEntry[] {
  if (!existsSync(file)) return [];
  try {
    const payload = JSON.parse(readFileSync(file, 'utf8')) as {version?: unknown; entries?: unknown};
    if (payload.version !== 1 || !Array.isArray(payload.entries)) return [];
    return payload.entries.filter(valid).map(entry => ({
      text: entry.text.trim(),
      source: (entry.source ?? 'user').trim() || 'user',
      ...(entry.updated_at ? {updated_at: entry.updated_at} : {}),
    }));
  } catch {
    return [];
  }
}

export function saveProfile(file: string, entries: ProfileEntry[]): void {
  mkdirSync(path.dirname(file), {recursive: true});
  const temporary = `${file}.tmp`;
  writeFileSync(temporary, JSON.stringify({version: 1, entries}, null, 2), 'utf8');
  renameSync(temporary, file);
}
