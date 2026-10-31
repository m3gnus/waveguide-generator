import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';
import { Notice } from './Notice';
import { EmptyState } from './EmptyState';

const host = document.createElement('div');
document.body.append(host);
const root = createRoot(host);
afterEach(() => { act(() => root.render(null)); });

it('renders title and detail in separate blocks with a text separator', () => {
  act(() => root.render(<Notice title="Solve failed">Could not build mesh.</Notice>));
  const notice = host.querySelector('.notice')!;
  expect(notice.querySelector('.notice-title')?.tagName).toBe('DIV');
  expect(notice.querySelector('.notice-detail')?.tagName).toBe('DIV');
  expect(notice.querySelector('.notice-title')?.nextElementSibling).toBe(notice.querySelector('.notice-detail'));
  expect(notice.textContent).toBe('Solve failed\nCould not build mesh.');
});

it.each(['info', 'warning', 'error'] as const)('uses the correct role for %s', (tone) => {
  act(() => root.render(<Notice tone={tone}>Message</Notice>));
  expect(host.querySelector(`.notice-${tone}`)?.getAttribute('role')).toBe(tone === 'error' ? 'alert' : 'status');
});

it('keeps actions in a separate wrapping row after detail', () => {
  act(() => root.render(<Notice actions={<button className="btn">Retry</button>}>Message</Notice>));
  const detail = host.querySelector('.notice-detail')!;
  const actions = host.querySelector('.notice-actions')!;
  expect(detail.nextElementSibling).toBe(actions);
  expect(actions.querySelector('button')?.textContent).toBe('Retry');
  expect(host.textContent).toBe('Message\nRetry');
});

it('renders a sentence-case empty state with separate detail and one action', () => {
  act(() => root.render(<EmptyState title="No charts open" detail="Add a chart." action={<button className="btn-primary">+ Add chart</button>}/>));
  expect(host.querySelector('.empty-state')?.getAttribute('role')).toBe('status');
  expect(host.querySelector('.empty-state > b')?.textContent).toBe('No charts open');
  expect(host.querySelector('.empty-state-detail')?.textContent).toBe('Add a chart.');
  expect(host.querySelectorAll('.empty-state-action button')).toHaveLength(1);
  expect(host.textContent).toBe('No charts open\nAdd a chart.\n+ Add chart');
});

it('preserves explicit live-region roles and card focus/data attributes', () => {
  act(() => root.render(<Notice tone="warning" role={null} tabIndex={-1} data-operation-id="held">Waiting</Notice>));
  const card = host.querySelector('.notice')!;
  expect(card.getAttribute('role')).toBeNull();
  expect(card.getAttribute('tabindex')).toBe('-1');
  expect(card.getAttribute('data-operation-id')).toBe('held');
  act(() => root.render(<Notice tone="warning" role="alert" aria-live="assertive">Restart will stop a solve.</Notice>));
  expect(host.querySelector('.notice')?.getAttribute('role')).toBe('alert');
  expect(host.querySelector('.notice')?.getAttribute('aria-live')).toBe('assertive');
});

it('composes an empty viewport mark before the shared title and preserves its live region', () => {
  act(() => root.render(<EmptyState title="Waiting for geometry" detail="Enable live updates." aria-live="polite">
    <i className="viewport-empty-mark" aria-hidden="true" />
  </EmptyState>));
  expect(host.querySelector('.empty-state')?.firstElementChild?.className).toBe('viewport-empty-mark');
  expect(host.querySelector('.empty-state')?.getAttribute('aria-live')).toBe('polite');
});
