import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { collapsedResultsMessages, orderResultsMessages, ResultsMessageArea, type ResultsMessage } from './ResultsMessages';

const host = document.createElement('div');
document.body.append(host);
const root = createRoot(host);
afterEach(() => { act(() => root.render(null)); });

const message = (key: string, severity: ResultsMessage['severity'], extra: Partial<ResultsMessage> = {}): ResultsMessage =>
  ({ key, severity, content: `${key} text`, ...extra });
const keys = (messages: readonly ResultsMessage[]) => messages.map(item => item.key);
const rows = () => [...host.querySelectorAll('.results-message')].map(row => row.getAttribute('data-severity'));

it('orders errors, warnings, progress, then information, keeping the given order within each', () => {
  const ordered = orderResultsMessages([message('i1', 'info'), message('p', 'progress'), message('w', 'warning'), message('e1', 'error'), message('i2', 'info'), message('e2', 'error')]);
  expect(keys(ordered)).toEqual(['e1', 'e2', 'w', 'p', 'i1', 'i2']);
});

it('keeps two lines collapsed: the most severe message and the progress line when one runs', () => {
  expect(keys(collapsedResultsMessages(orderResultsMessages([message('e', 'error'), message('w', 'warning'), message('p', 'progress')])))).toEqual(['e', 'p']);
  expect(keys(collapsedResultsMessages(orderResultsMessages([message('w1', 'warning'), message('w2', 'warning'), message('i', 'info')])))).toEqual(['w1', 'w2']);
  expect(keys(collapsedResultsMessages([message('i', 'info')]))).toEqual(['i']);
});

it('renders nothing without messages, so the charts keep the whole panel', () => {
  act(() => root.render(<ResultsMessageArea messages={[]}/>));
  expect(host.innerHTML).toBe('');
});

it('shows each message as one line with its severity, its own action and the rest counted', () => {
  const retry = vi.fn();
  act(() => root.render(<ResultsMessageArea messages={[
    message('i', 'info'),
    message('e', 'error', { actions: <button type="button" className="btn" onClick={retry}>Retry</button> }),
    message('w', 'warning'),
    message('p', 'progress'),
  ]}/>));
  expect(rows()).toEqual(['error', 'progress']);
  const error = host.querySelector('.results-message-error')!;
  expect(error.getAttribute('role')).toBe('alert');
  expect(error.querySelector('.results-message-glyph')?.getAttribute('aria-label')).toBe('Error');
  act(() => error.querySelector<HTMLButtonElement>('.results-message-actions button')!.click());
  expect(retry).toHaveBeenCalledTimes(1);
  const toggle = host.querySelector<HTMLButtonElement>('.results-messages-toggle')!;
  expect(toggle.textContent).toBe('+2 more');
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  act(() => toggle.click());
  expect(host.querySelector('.results-messages')?.classList.contains('expanded')).toBe(true);
  expect(rows()).toEqual(['error', 'warning', 'progress', 'info']);
  act(() => toggle.click());
  expect(rows()).toEqual(['error', 'progress']);
});

it('dismisses only messages that block nothing, until their key changes', () => {
  const onDismiss = vi.fn();
  const render = (warning: string) => act(() => root.render(<ResultsMessageArea messages={[
    message('e', 'error'),
    message(warning, 'warning', { dismissible: true, onDismiss }),
  ]}/>));
  render('stale:1');
  expect(host.querySelector('.results-message-error .results-message-dismiss')).toBeNull();
  act(() => host.querySelector<HTMLButtonElement>('.results-message-warning .results-message-dismiss')!.click());
  expect(onDismiss).toHaveBeenCalledTimes(1);
  expect(rows()).toEqual(['error']);
  render('stale:1');
  expect(rows()).toEqual(['error']);
  render('stale:2');
  expect(rows()).toEqual(['error', 'warning']);
});
