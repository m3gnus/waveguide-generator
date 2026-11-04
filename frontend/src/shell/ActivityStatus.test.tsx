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

it.each(['export', 'design-open'] as const)('%s stays hidden at 999 ms and appears at 1,000 ms through a 1,500 ms wait', (kind) => {
  const host = mount();
  let handle!: ReturnType<typeof activityStore.start>;
  act(() => { handle = activityStore.start({ kind, label: 'Working' }); });
  expect(host.textContent).toBe('');
  act(() => { vi.advanceTimersByTime(999); });
  expect(host.textContent).toBe('');
  act(() => { vi.advanceTimersByTime(1); });
  expect(host.textContent).toContain('Working');
  expect(host.textContent).toContain('0:01');
  act(() => { vi.advanceTimersByTime(500); });
  expect(host.textContent).toContain('Working');
  act(() => handle.end());
  expect(host.textContent).toBe('');
});

it('appears at once when this kind usually takes at least one second, and says how long', async () => {
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
  expect(activityVisible(activity({ background: true }), 999, 9_000)).toBe(false);
  expect(activityVisible(activity({ background: true }), 1_000, null)).toBe(true);
  expect(activityVisible(activity(), 0, 1_000)).toBe(true);
  expect(activityVisible(activity(), 999, 999)).toBe(false);
});

it('shows a wait that starts between shared clock ticks at its own boundary', () => {
  const host = mount();
  act(() => { activityStore.start({ kind: 'mesh-count', label: 'Counting triangles', background: true }); });
  act(() => { vi.advanceTimersByTime(350); });
  act(() => { activityStore.start({ kind: 'design-open', label: 'Opening the design' }); });
  act(() => { vi.advanceTimersByTime(999); });
  expect(host.textContent).not.toContain('Opening the design');
  act(() => { vi.advanceTimersByTime(1); });
  expect(host.querySelector('[role="progressbar"]')?.getAttribute('aria-label')).toBe('Opening the design');
});

it('disposes visibility timers on completion, reset and status unmount', () => {
  const host = mount();
  let handle!: ReturnType<typeof activityStore.start>;
  act(() => { handle = activityStore.start({ kind: 'export', label: 'Exporting STEP' }); });
  expect(vi.getTimerCount()).toBeGreaterThan(0);
  act(() => { handle.end(false); });
  expect(host.textContent).toBe('');
  expect(vi.getTimerCount()).toBe(0);
  act(() => { activityStore.start({ kind: 'export', label: 'Exporting STEP' }); });
  act(() => activityStore.reset());
  unmount.splice(0).forEach(stop => stop());
  expect(vi.getTimerCount()).toBe(0);
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

it('takes time left from the usual duration when stages are uneven, and never claims 0:00 while working', () => {
  // Resolving took 0.2 s; meshing will take the rest of a usual 10 s.
  const staged = activity({ progress: { done: 1, total: 4 }, progressAt: 200 });
  expect(activityMeta(staged, 2_000, 10_000)).toBe('25% · 0:08 left · 0:02');
  // Without a usual duration, the stage rate has run out: no time is claimed.
  expect(activityMeta(staged, 2_000, null)).toBe('25% · 0:02');
  expect(activityMeta(staged, 12_000, 10_000)).toBe('25% · 0:12');
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

it('gives the foreground export its bar and Stop while an older background count runs', () => {
  const host = mount();
  let background!: ReturnType<typeof activityStore.start>;
  let exporting!: ReturnType<typeof activityStore.start>;
  const cancel = vi.fn();
  act(() => {
    background = activityStore.start({ kind: 'mesh-count', label: 'Counting triangles', background: true });
    vi.advanceTimersByTime(3_000);
    exporting = activityStore.start({ kind: 'export', label: 'Exporting STEP', cancel });
    exporting.progress(1, 4);
    vi.advanceTimersByTime(2_000);
  });
  expect(host.querySelector('[role="progressbar"]')?.getAttribute('aria-label')).toBe('Exporting STEP');
  expect(host.textContent).toContain('+1 more');
  act(() => host.querySelector('button')!.click());
  expect(cancel).toHaveBeenCalledOnce();
  act(() => exporting.end(false));
  expect(host.querySelector('[role="progressbar"]')?.getAttribute('aria-label')).toBe('Counting triangles');
  act(() => background.end());
});
