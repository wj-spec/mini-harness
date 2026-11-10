import React, {useCallback, useRef, useState} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import type {Palette} from './theme.js';

export function WorkbenchLogPanel({notices, columns, rows, palette, active, onReturn}: {
  notices: readonly string[]; columns: number; rows: number; palette: Palette; active: boolean; onReturn: () => void;
}) {
  const width = Math.max(1, columns - 5);
  const lines = notices.flatMap((notice, index) => {
    const [first, ...rest] = notice.split('\n');
    return [
      {spans: [{text: fitText(`#${index + 1} ${first ?? ''}`, width), bold: true}]},
      ...textLines(rest.join('\n'), width),
    ];
  });
  const bodyHeight = Math.max(3, rows - 5);
  const maximum = Math.max(0, lines.length - bodyHeight);
  const [top, setTop] = useState(maximum);
  const handle = useRef<(input: string, key: Key) => void>(() => {});
  const scroll = (delta: number) => setTop(value => Math.max(0, Math.min(maximum, value + delta)));
  handle.current = (_input, key) => {
    if (!active || key.eventType === 'release' || key.ctrl || key.meta) return;
    if (key.escape || key.return) {onReturn(); return;}
    if (key.upArrow) scroll(-1);
    else if (key.downArrow) scroll(1);
    else if (key.pageUp) scroll(-(bodyHeight - 2));
    else if (key.pageDown) scroll(bodyHeight - 2);
    else if (key.home || key.ctrl && _input === 'a') setTop(0);
    else if (key.end || key.ctrl && _input === 'e') setTop(maximum);
  };
  useInput(useCallback((input, key) => handle.current(input, key), []));
  return <Box width={columns} height={rows} flexDirection="column" backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.foreground} bold>{fitText(`工作台记录 · ${notices.length} 条 · 最新在底部`, columns)}</Text>
    <Text color={palette.secondary}>{fitText(notices.length ? '只读记录 · 不插入对话主区 · Esc/Enter 返回' : '还没有工作台记录；操作提示与命令结果会记在这里。', columns)}</Text>
    <ReadingPane title="记录" lines={lines.length ? lines : [{spans: [{text: fitText('（空）', width)}]}]} top={lines.length ? top : 0} height={bodyHeight} columns={columns} palette={palette}/>
    <Text color={palette.secondary}>{fitText('↑↓ 滚动 · PgUp/PgDn 翻页 · Esc 返回输入 · 草稿与光标保留', columns)}</Text>
  </Box>;
}
