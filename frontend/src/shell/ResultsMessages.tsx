import { useCallback, useLayoutEffect, useRef, useState, type HTMLAttributes, type ReactNode } from 'react';
import { Icon } from './icons';

/**
 * The Results panel's one message area (Magnus, 2026-11-03: "a small window at
 * the top of the results panel where all the different error messages, etc.,
 * appear"). Every results-related notice, warning, error and the solve
 * progress line is a row here, never a box stacked above the charts.
 *
 * Collapsed, the area is one line per message and at most two lines: the most
 * severe message, and the solve progress line when one runs (else the next
 * message). The rest are counted on the toggle, which opens every message
 * in full over the charts, so the charts keep their height either way. Each
 * row keeps its own actions on its line; only the text is shortened.
 */
export type ResultsMessageSeverity = 'error' | 'warning' | 'progress' | 'info';

export interface ResultsMessage {
  /** Identity: a dismissed message stays hidden until its key changes. */
  key: string;
  severity: ResultsMessageSeverity;
  content: ReactNode;
  /** The message's own fix, beside it on its line. */
  actions?: ReactNode;
  /** Only for a message that blocks nothing; an error that blocks an action stays. */
  dismissible?: boolean;
  onDismiss?: () => void;
  /** `null` leaves the role to the content, which may carry its own. */
  role?: HTMLAttributes<HTMLDivElement>['role'] | null;
  className?: string;
}

const SEVERITY_RANK: Record<ResultsMessageSeverity, number> = { error: 0, warning: 1, progress: 2, info: 3 };
const SEVERITY_LABEL: Record<ResultsMessageSeverity, string> = { error: 'Error', warning: 'Warning', progress: 'In progress', info: 'Information' };

/** Most severe first; equal severities keep the order they were given in. */
export function orderResultsMessages(messages: readonly ResultsMessage[]): ResultsMessage[] {
  return messages
    .map((message, index) => ({ message, index }))
    .sort((a, b) => SEVERITY_RANK[a.message.severity] - SEVERITY_RANK[b.message.severity] || a.index - b.index)
    .map(({ message }) => message);
}

/** The collapsed rows: the most severe message, then the progress line if a
 * solve is running, else the next message. */
export function collapsedResultsMessages(ordered: readonly ResultsMessage[]): ResultsMessage[] {
  if (ordered.length <= 2) return [...ordered];
  const progress = ordered.slice(1).find((message) => message.severity === 'progress');
  return [ordered[0], progress ?? ordered[1]];
}

function Glyph({ severity }: { severity: ResultsMessageSeverity }) {
  return <span className="results-message-glyph" role="img" aria-label={SEVERITY_LABEL[severity]} title={SEVERITY_LABEL[severity]}>
    {severity === 'info' ? <Icon name="info"/> : severity === 'progress' ? <i/> : <b aria-hidden="true">!</b>}
  </span>;
}

export function ResultsMessageArea({ messages, label = 'Results messages' }: { messages: readonly ResultsMessage[]; label?: string }) {
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  const [expanded, setExpanded] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const [collapsedHeight, setCollapsedHeight] = useState<number | null>(null);
  const area = useRef<HTMLElement>(null);
  const live = orderResultsMessages(messages.filter((message) => !message.dismissible || !dismissed.has(message.key)));
  const rows = expanded ? live : collapsedResultsMessages(live);
  const hidden = live.length - rows.length;
  const open = expanded && live.length > 0;

  // Whether a collapsed line cuts its text, so the toggle can offer the rest.
  const measure = useCallback(() => {
    const element = area.current;
    if (!element || open) return;
    setCollapsedHeight(element.offsetHeight);
    setTruncated([...element.querySelectorAll<HTMLElement>('.results-message-body, .results-message-body *')]
      .some((node) => node.scrollWidth > node.clientWidth + 1 && getComputedStyle(node).overflowX === 'hidden'));
  }, [open]);
  useLayoutEffect(measure);
  useLayoutEffect(() => {
    const element = area.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => measure());
    observer.observe(element);
    return () => observer.disconnect();
  }, [measure, live.length > 0]);
  useLayoutEffect(() => { if (!live.length && expanded) setExpanded(false); }, [expanded, live.length]);

  if (!live.length) return null;
  const toggle = hidden > 0 || truncated || open;
  return <section
    ref={area}
    className={`results-messages${open ? ' expanded' : ''}`}
    aria-label={label}
    style={open && collapsedHeight ? { height: collapsedHeight } : undefined}
  >
    <div className="results-messages-list">
      {rows.map((message) => <div
        key={message.key}
        className={`results-message results-message-${message.severity}${message.className ? ` ${message.className}` : ''}`}
        data-severity={message.severity}
        role={message.role === null ? undefined : message.role ?? (message.severity === 'error' ? 'alert' : 'status')}
      >
        <Glyph severity={message.severity}/>
        <div className="results-message-body">{message.content}</div>
        {message.actions && <div className="results-message-actions">{message.actions}</div>}
        {message.dismissible && <button
          type="button"
          className="results-message-dismiss"
          aria-label="Dismiss message"
          title="Dismiss"
          onClick={() => {
            setDismissed((current) => new Set(current).add(message.key));
            message.onDismiss?.();
          }}
        ><Icon name="close"/></button>}
      </div>)}
    </div>
    {toggle && <button
      type="button"
      className="results-messages-toggle"
      aria-expanded={open}
      title={open ? 'Show one line per message' : 'Show every message in full'}
      onClick={() => setExpanded((value) => !value)}
    >{open ? 'Less' : hidden > 0 ? `+${hidden} more` : 'More'}<Icon name="caret"/></button>}
  </section>;
}
