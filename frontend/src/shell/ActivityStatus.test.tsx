import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { activityStore, activityVisible, type Activity } from './activity';
import { ActivityStatus, activityMeta } from './ActivityStatus';

const unmount: (() => void)[] = [];

function mount() {
  const host = document.createElement('div');
  const root = createRoot(host);
  act(() => root.render(<ActivityStatus/>));
  unmount.push(() => act(() => root.unmount()));
  return host;
}

const activity = (change: Partial<Activity> = {}): Activity => ({
  id: 1, kind: 'export', label: 'Exporting STL', startedAt: 0,
  progress: null, progressAt: null, cancel: null, background: false, ...change,
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(100_000);
  localStorage.clear();
  activityStore.reset();
});
afterEach(() => {
  unmount.splice(0).forEach((stop) => stop());
  activityStore.reset();
  vi.useRealTimers();
});

it('stays hidden for a short wait and appears once it has run two seconds', () => {
  const host = mount();
  let handle!: ReturnType<typeof activityStore.start>;
  act(() => { handle = activityStore.start({ kind: 'export', label: 'Exporting STL' }); });
  expect(host.textContent).toBe('');
  act(() => { vi.advanceTimersByTime(2_000); });
  expect(host.textContent).toContain('Exporting STL');
  expect(host.textContent).toContain('0:02');
  act(() => handle.end());
  expect(host.textContent).toBe('');
});

it('appears at once when this kind usually takes longer than two seconds, and says how long', async () => {
  await activityStore.track({ kind: 'assembly-build', label: 'Building the mesh' }, async () => {
    vi.advanceTimersByTime(7_000);
  });
  const host = mount();
  act(() => { activityStore.start({ kind: 'assembly-build', label: 'Building the mesh' }); });
  expect(host.textContent).toContain('Building the mesh');
  expect(host.textContent).toContain('usually 0:07');
  expect(host.querySelector('.progress.indeterminate')).not.toBeNull();
});

it('never shows background work early, whatever it usually takes', () => {
  expect(activityVisible(activity({ background: true }), 1_000, 9_000)).toBe(false);
  expect(activityVisible(activity({ background: true }), 2_000, null)).toBe(true);
  expect(activityVisible(activity(), 1_000, 9_000)).toBe(true);
});

it('fills the bar and gives the percentage and time left when the work is counted', () => {
  const host = mount();
  let handle!: ReturnType<typeof activityStore.start>;
  act(() => { handle = activityStore.start({ kind: 'cad-import', label: 'Importing the CAD model' }); });
  act(() => { vi.advanceTimersByTime(4_000); handle.progress(1, 4); });
  expect(host.textContent).toContain('25%');
  expect(host.textContent).toContain('0:12 left');
  expect(host.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('25');
  act(() => { vi.advanceTimersByTime(2_000); });
  expect(host.textContent).toContain('0:10 left');
});

it('keeps failed or cancelled runs out of the usual duration', async () => {
  await expect(activityStore.track({ kind: 'export', label: 'Exporting STL' }, async () => {
    vi.advanceTimersByTime(500);
    throw new Error('refused');
  })).rejects.toThrow('refused');
  expect(activityStore.typicalMs('export')).toBeNull();
  expect(activityStore.getSnapshot()).toHaveLength(0);
});

it('uses the median of recent runs', async () => {
  for (const ms of [3_000, 30_000, 4_000]) {
    await activityStore.track({ kind: 'export', label: 'Exporting STL' }, async () => { vi.advanceTimersByTime(ms); });
  }
  expect(activityStore.typicalMs('export')).toBe(4_000);
});

it('offers Stop when the operation can be stopped, and counts further waits', () => {
  const cancel = vi.fn();
  const host = mount();
  act(() => {
    activityStore.start({ kind: 'export', label: 'Exporting STL', cancel });
    activityStore.start({ kind: 'send-to-cad', label: 'Sending to CAD' });
    vi.advanceTimersByTime(2_000);
  });
  expect(host.textContent).toContain('+1 more');
  const stop = [...host.querySelectorAll('button')].find((button) => button.textContent === 'Stop');
  act(() => stop!.click());
  expect(cancel).toHaveBeenCalledTimes(1);
});

it('reads the elapsed clock alone when nothing is known', () => {
  expect(activityMeta(activity(), 5_000, null)).toBe('0:05');
});
