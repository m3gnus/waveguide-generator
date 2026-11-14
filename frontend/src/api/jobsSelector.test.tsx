import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from './jobsSocket';
import { sameJobsApartFromProgress, useJobsApartFromProgress, useJobsSelector } from './jobsSelector';

function job(id: string, patch: Partial<JobItem> = {}): JobItem {
  return {
    id, run_number: 1, parent_job_id: null, label: id, status: 'running', progress: 0.1, stage: 'solving',
    stage_message: 'Frequency 1 of 10', frequency_progress: { done: 1, total: 10 }, created_at: '2026-08-08T00:00:00Z',
    queued_at: '2026-08-08T00:00:00Z', started_at: null, completed_at: null, config_summary: {},
    solve_options: {} as JobItem['solve_options'], has_results: false, has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null, script_snapshot: null, design_revision: 1, polar_grid: {},
    rating: null, exported_files: [], auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [], ...patch,
  };
}

describe('sameJobsApartFromProgress', () => {
  const a = job('a');
  const b = job('b');

  it('ignores a change in progress fields only', () => {
    const advanced = { ...a, progress: 0.5, stage: 'meshing', stage_message: 'Frequency 5 of 10',
      frequency_progress: { done: 5, total: 10 }, log_tail: ['line'] };
    expect(sameJobsApartFromProgress([a, b], [advanced, b])).toBe(true);
  });

  it('sees any other field change', () => {
    expect(sameJobsApartFromProgress([a], [{ ...a, status: 'complete' }])).toBe(false);
    expect(sameJobsApartFromProgress([a], [{ ...a, has_results: true }])).toBe(false);
  });

  it('sees a key added or removed', () => {
    expect(sameJobsApartFromProgress([a], [{ ...a, cad_state: null }])).toBe(false);
    const { mesh_stats: _removed, ...withoutKey } = a;
    expect(sameJobsApartFromProgress([a], [withoutKey as JobItem])).toBe(false);
    // Adding or removing a progress field alone is still only progress.
    const { frequency_progress: _progress, ...withoutProgress } = a;
    expect(sameJobsApartFromProgress([a], [withoutProgress as JobItem])).toBe(true);
  });

  it('compares nested values by identity', () => {
    expect(sameJobsApartFromProgress([a], [{ ...a }])).toBe(true);
    expect(sameJobsApartFromProgress([a], [{ ...a, config_summary: { ...a.config_summary } }])).toBe(false);
  });

  it('sees runs added, removed or reordered', () => {
    expect(sameJobsApartFromProgress([a, b], [b, a])).toBe(false);
    expect(sameJobsApartFromProgress([a, b], [a])).toBe(false);
    expect(sameJobsApartFromProgress([a], [a, b])).toBe(false);
  });
});

describe('useJobsSelector', () => {
  let host: HTMLDivElement;
  let root: Root;
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  const publish = (jobs: JobItem[], cursor = 1) => act(() => {
    manager.snapshot = { connection: 'connected', epoch: 1, cursor, jobs, error: null };
    manager.listeners.forEach((listener) => listener());
  });
  let renders = 0;
  let seen: JobItem[] = [];
  function Settled() {
    renders += 1;
    seen = useJobsApartFromProgress();
    return null;
  }
  const selectCount = (snapshot: JobsSnapshot) => snapshot.jobs.length;
  let counted = 0;
  function Count() {
    renders += 1;
    counted = useJobsSelector(selectCount);
    return null;
  }

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    renders = 0;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publish([]);
  });

  it('keeps the same list through progress-only changes and follows any other change', () => {
    const first = [job('a'), job('b')];
    publish(first);
    act(() => root.render(<Settled/>));
    expect(seen).toBe(first);
    renders = 0;
    publish([{ ...first[0], progress: 0.6, stage_message: 'Frequency 6 of 10' }, first[1]], 2);
    publish([{ ...first[0], log_tail: ['solved 6'] }, first[1]], 3);
    expect(renders).toBe(0);
    expect(seen).toBe(first);
    const completed = [{ ...first[0], status: 'complete' as const }, first[1]];
    publish(completed, 4);
    expect(renders).toBe(1);
    expect(seen).toBe(completed);
  });

  it('re-renders only when the selected value changes', () => {
    publish([job('a')]);
    act(() => root.render(<Count/>));
    expect(counted).toBe(1);
    renders = 0;
    publish([job('a', { status: 'complete' })], 2);
    expect(renders).toBe(0);
    publish([job('a'), job('b')], 3);
    expect(renders).toBe(1);
    expect(counted).toBe(2);
  });
});
