import React from 'react';
import {Box, Text} from 'ink';
import {clipCells} from './editor.js';
import {COMMAND_DESCRIPTIONS} from './protocol.js';

type CommandMenuProps = {
  matches: readonly string[];
  index: number;
  rows: number;
  columns: number;
  accentColor?: string;
  foregroundColor?: string;
  secondaryColor?: string;
  surfaceColor?: string;
  descriptions?: Record<string, string>;
};

// 命令菜单：带说明、可滚动；无框线，沿用 ▌/› 工作台视觉语言。
export function CommandMenu({matches, index, rows, columns, accentColor, foregroundColor, secondaryColor, surfaceColor, descriptions}: CommandMenuProps) {
  const total = matches.length;
  const visibleRows = Math.max(1, Math.min(rows, total));
  const top = Math.max(0, Math.min(index - Math.floor(visibleRows / 2), total - visibleRows));
  const visible = matches.slice(top, top + visibleRows);
  const width = Math.max(4, columns - 2);
  const labelWidth = Math.max(8, Math.min(Math.max(...visible.map(command => command.length)), Math.floor(width / 2)));
  return <Box flexDirection="column" height={visibleRows} width={columns} paddingX={2} flexShrink={0} backgroundColor={surfaceColor}>
    {visible.map((command, offset) => {
      const position = top + offset;
      const active = position === index;
      const description = COMMAND_DESCRIPTIONS[command] ?? descriptions?.[command] ?? '';
      const head = active ? '› ' : '  ';
      const label = command.padEnd(labelWidth, ' ');
      return <Text key={command} wrap="truncate-end">
        <Text bold color={active ? accentColor : foregroundColor}>{head}{label}</Text>
        <Text color={active ? foregroundColor : secondaryColor}>{clipCells(`  ${description}`, width - labelWidth - 2)}</Text>
      </Text>;
    })}
  </Box>;
}
