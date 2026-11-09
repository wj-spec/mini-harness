import React, {useLayoutEffect, useRef} from 'react';
import {Box, Text, measureElement, type DOMElement} from 'ink';
import stringWidth from 'string-width';
import type {Palette} from './theme.js';
import type {BodyBounds} from './use-transcript-scroll.js';

export type ActionTarget<T extends string> = BodyBounds & {action: T};

export function ActionButtons<T extends string>({buttons, selected, columns, height, palette, targets, paddingX = 1}: {
  buttons: {action: T; label: string; warning?: boolean}[]; selected: T | null; columns: number; height: number; palette: Palette;
  paddingX?: number;
  targets: React.RefObject<ActionTarget<T>[]>;
}) {
  const elements = useRef(new Map<T, DOMElement>());
  useLayoutEffect(() => {
    targets.current = buttons.flatMap(button => {
      const element = elements.current.get(button.action);
      if (!element) return [];
      const {x, y, width, height} = measureElement(element);
      return [{action: button.action, top: y + 1, bottom: y + height, left: x + 1, right: x + width}];
    });
    return () => {targets.current = [];};
  });
  return <Box width={columns} height={height} paddingX={paddingX} gap={1} flexShrink={0}>
    {buttons.map(({action, label, warning}) => <Box key={action}
      ref={element => {if (element) elements.current.set(action, element); else elements.current.delete(action);}}
      width={stringWidth(label) + 4} height={height} alignItems="center" justifyContent="center"
      backgroundColor={selected === action ? palette.focus : palette.surface}>
      <Text bold={selected === action} color={warning ? palette.warning : palette.foreground}>
        {selected === action ? `› ${label} ‹` : `[ ${label} ]`}
      </Text>
    </Box>)}
  </Box>;
}
