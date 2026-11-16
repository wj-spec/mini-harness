import React, {useCallback, useRef} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {fitText, textLines} from './markdown.js';
import type {Palette} from './theme.js';

// 配置文件损坏时的修复页：只说明位置与修法，不提供保存、不覆盖原文件。
export function ConfigProblemPanel({workspace, problem, configPath, columns, rows, palette, onQuit}: {
  workspace: string; problem: string; configPath: string;
  columns: number; rows: number; palette: Palette; onQuit: () => void;
}) {
  const handler = useRef<(input: string, key: Key) => void>(() => {});
  handler.current = (input, key) => {
    if (key.eventType === 'release') return;
    if (key.escape) onQuit();
  };
  useInput(useCallback((input, key) => handler.current(input, key), []));
  const name = workspace.split(/[\\/]/).filter(Boolean).at(-1) ?? workspace;
  const lines = textLines([
    '1. 打开上面的文件，检查每一行是否为 KEY=value 形式；值含空格或 # 时请使用引号。',
    '2. 修正后重新运行 ./start.sh；程序不会自动改写或删除原文件。',
    '3. 需要重新配置时，可先修好文件再运行 ./start.sh --configure。',
    '4. 想跳过真实模型，可运行 ./start.sh --demo（独立测试配置，不影响该文件）。',
  ].join('\n'), Math.max(1, columns - 4));
  const detail = textLines(problem || '配置文件无法解析。', Math.max(1, columns - 4));
  return <Box width={columns} height={rows} flexDirection="column" overflow="hidden" backgroundColor={palette.background}>
    <Text bold color={palette.warning}>{fitText('配置需要修复', columns)}</Text>
    {detail.slice(0, Math.min(3, Math.max(1, rows - 8))).map((line, index) =>
      <Text key={index} color={palette.warning}>{line.spans.map(span => span.text).join('')}</Text>)}
    <Text color={palette.foreground}>{fitText('项目  ' + name, columns)}</Text>
    <Text color={palette.secondary}>{fitText('配置文件  ' + configPath, columns)}</Text>
    <Box height={1}/>
    {lines.slice(0, Math.max(1, rows - 9)).map((line, index) =>
      <Text key={index} color={palette.secondary}>{line.spans.map(span => span.text).join('')}</Text>)}
    <Box flexGrow={1}/>
    <Text color={palette.secondary}>{fitText('原文件未修改；本页不会保存或覆盖配置。', columns)}</Text>
    <Text color={palette.secondary}>{fitText('Esc 或 Ctrl+Q 退出', columns)}</Text>
  </Box>;
}
