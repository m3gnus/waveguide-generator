import { Children, isValidElement, type ReactNode } from 'react';
import { HelpTipRow } from './HelpTip';

const INLINE_OPTION_LIMIT = 16;

/** Long option names need the rail's full width, rather than the numeric column. */
export function selectRowLayout(options: readonly string[]): 'inline' | 'stacked' {
  return options.some((label) => label.length > INLINE_OPTION_LIMIT) ? 'stacked' : 'inline';
}

function textContent(node: ReactNode): string {
  return Children.toArray(node).map((child) => isValidElement<{ children?: ReactNode }>(child)
    ? textContent(child.props.children) : String(child)).join('');
}

function optionLabels(node: ReactNode): string[] {
  return Children.toArray(node).flatMap((child) => {
    if (!isValidElement<{ children?: ReactNode; label?: string }>(child)) return [];
    return child.type === 'option'
      ? [child.props.label ?? textContent(child.props.children)]
      : optionLabels(child.props.children);
  });
}

/** Read the actual rendered option labels, including dynamic backend suffixes. */
export function SelectRow({ text, className, children, revealId }: {
  text?: string; className?: string; revealId?: string; children: ReactNode;
}) {
  const stacked = selectRowLayout(optionLabels(children)) === 'stacked';
  return <HelpTipRow text={text} revealId={revealId} className={`select-row${stacked ? ' stacked-select-row' : ''}${className ? ` ${className}` : ''}`}>{children}</HelpTipRow>;
}
