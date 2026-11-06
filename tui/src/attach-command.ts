export interface AttachCommand {
  taskId: string | null;
  now: boolean;
}

export function parseAttachCommand(text: string, fallbackTaskId: string | null): AttachCommand {
  const parts = text.trim().split(/\s+/);
  const now = parts[1] === 'now' || parts[1] === '--now';
  const explicit = parts[1] && !now ? parts[1] : null;
  return {taskId: explicit ?? fallbackTaskId, now};
}
