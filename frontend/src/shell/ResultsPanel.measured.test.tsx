import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { MAX_MEASURED_OVERLAYS, useMeasuredOverlayStore } from '../stores/measuredOverlays';
import { ResultsPanel } from './ResultsPanel';

/**
 * The measured-overlay loop through the panel itself: pick a file, get a row,
 * get a rejection message when the file is not a response curve.
 */

function job(id: string): JobItem {
  return {
    id, run_number: 1, parent_job_id: null,
    label: id, status: 'complete', progress: 1, stage: null,
    stage_message: null, created_at: '2026-08-18T00:00:00Z',
    queued_at: '2026-08-18T00:00:00Z', started_at: null,
    completed_at: '2026-08-18T00:00:01Z', config_summary: {}, solve_options: {} as JobItem['solve_options'],
    has_results: true, has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null, script_snapshot: null,
    design_revision: 1, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [],
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

describe('measured overlay controls in the results panel', () => {
  let host: HTMLDivElement;
  let root: Root;

  async function choose(...files: File[]): Promise<void> {
    const input = host.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, 'files', { configurable: true, value: files });
    await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });
  }

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resultsCache.clear();
    provisionalResults.clear();
    compareSelection.clear();
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['summary'] });
    useMeasuredOverlayStore.getState().clear();
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ frequencies: [500, 1_000], metadata: {} }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )));
    publishJobs([job('run-1')]);
    compareSelection.setPrimary('run-1');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    await act(async () => { root.render(<ResultsPanel/>); });
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    compareSelection.clear();
    resultsCache.clear();
    useMeasuredOverlayStore.getState().clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('offers the affordance beside the export action and hides the strip until one is loaded', () => {
    const trigger = [...host.querySelectorAll('button')].find((button) => button.textContent === 'Overlay measured…');
    expect(trigger).toBeDefined();
    expect(host.querySelector('input[type="file"]')?.getAttribute('accept')).toBe('.frd,.txt,.csv');
    expect(host.querySelector('.result-measured')).toBeNull();
  });

  it('loads a picked file into a row with an offset, a toggle and a remove', async () => {
    await choose(new File(['* Freq(Hz) SPL(dB)\n200 86.4\n400 90.0\n'], 'bench 1m.frd'));

    const strip = host.querySelector('.result-measured')!;
    expect(strip).not.toBeNull();
    expect(strip.textContent).toContain('bench 1m');
    expect(useMeasuredOverlayStore.getState().overlays).toHaveLength(1);

    const offset = strip.querySelector<HTMLInputElement>('.result-measured-offset input')!;
    expect(offset.value).toBe('0');
    const toggle = strip.querySelector<HTMLButtonElement>('.result-measured-toggle')!;
    await act(async () => { toggle.click(); });
    expect(useMeasuredOverlayStore.getState().overlays[0].visible).toBe(false);

    const remove = [...strip.querySelectorAll('button')].find((button) => button.textContent === '×')!;
    await act(async () => { remove.click(); });
    expect(useMeasuredOverlayStore.getState().overlays).toHaveLength(0);
    expect(host.querySelector('.result-measured')).toBeNull();
  });

  it('names the file that could not be read and keeps the ones that could', async () => {
    await choose(
      new File(['* Freq(Hz) SPL(dB)\n200 86.4\n400 90.0\n'], 'good.frd'),
      new File(['not a response curve at all\n'], 'bad.txt'),
    );

    expect(useMeasuredOverlayStore.getState().overlays.map(({ label }) => label)).toEqual(['good']);
    const alert = host.querySelector('[role="alert"]')!;
    expect(alert.textContent).toContain('bad');
    expect(alert.textContent).toContain('usable measurement point');
  });

  it('disables the affordance once the overlay limit is reached', async () => {
    const files = Array.from({ length: MAX_MEASURED_OVERLAYS + 1 }, (_, index) =>
      new File(['100 90\n200 91\n'], `bench-${index}.frd`));
    await choose(...files);

    expect(useMeasuredOverlayStore.getState().overlays).toHaveLength(MAX_MEASURED_OVERLAYS);
    const trigger = [...host.querySelectorAll('button')].find((button) => button.textContent === 'Overlay measured…')!;
    expect(trigger.hasAttribute('disabled')).toBe(true);
    expect(host.querySelector('[role="alert"]')?.textContent).toContain(`at most ${MAX_MEASURED_OVERLAYS}`);
  });
});
