// Demo 使用独立 fake 后端与测试会话；移除会让它指向真实模型或旧会话的参数。
const droppedFlags = new Set(['--continue', '--list-sessions', '--test-session', '--fake']);
const droppedWithValue = new Set(['--session', '--session-identity', '--provider', '--model', '--base-url']);

export function demoArguments(args: readonly string[]): string[] {
  const kept: string[] = [];
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index]!;
    if (droppedFlags.has(arg) || droppedWithValue.has(arg)) {
      if (droppedWithValue.has(arg)) index += 1;
      continue;
    }
    kept.push(arg);
  }
  return [...kept, '--fake', '--test-session'];
}
