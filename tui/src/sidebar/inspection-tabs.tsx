import React, {useEffect, useRef} from 'react';
import {Box, Text, measureElement, type DOMElement} from 'ink';
import type {ActionTarget} from '../action-buttons.js';
import type {Palette} from '../theme.js';

export type InspectionPage = 'changes' | 'plan' | 'verification';
export type InspectionAction = InspectionPage | 'refresh';
export function InspectionTabs({page, columns, palette, targets, pending, follow}: {
  page: InspectionPage; columns: number; palette: Palette; targets: React.RefObject<ActionTarget<InspectionAction>[]>; pending: boolean;
  follow: boolean;
}) {
  const elements = useRef(new Map<InspectionAction, DOMElement>());
  const compact = columns < (pending ? 29 : 22);
  const tabs: {action: InspectionAction; label: string}[] = [
    {action: 'changes', label: compact ? '改' : '变更'},
    {action: 'plan', label: compact ? '计' : '计划'},
    {action: 'verification', label: compact ? '验' : '验证'},
    ...(pending ? [{action: 'refresh' as const, label: follow ? compact ? '随' : '跟随' : compact ? '新' : '接入'}] : []),
  ];
  useEffect(() => {
    targets.current = tabs.flatMap(({action}) => {
      const element = elements.current.get(action); if (!element) return [];
      const {x, y, width, height} = measureElement(element);
      return [{action, left: x + 1, right: x + width, top: y + 1, bottom: y + height}];
    });
    return () => {targets.current = [];};
  });
  return <Box width={columns} height={1} paddingX={1} gap={columns < 15 ? 0 : 1} flexShrink={0}>
    {tabs.map(({action, label}) => <Box key={action} width={compact ? page === action ? 3 : 2 : 6} height={1} justifyContent="center"
      ref={element => {if (element) elements.current.set(action, element); else elements.current.delete(action);}}
      backgroundColor={page === action ? palette.focus : palette.surface}>
      <Text bold={page === action} color={page === action ? palette.foreground : palette.secondary}>{page === action ? `›${label}` : label}</Text>
    </Box>)}
  </Box>;
}
