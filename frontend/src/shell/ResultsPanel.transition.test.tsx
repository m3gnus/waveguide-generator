import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { ResultsPanel } from './ResultsPanel';

const exportMocks = vi.hoisted(() => ({
  runWorkspaceExportBundle: vi.fn(),
}));
vi.mock('../results/exporters', () => ({
  runWorkspaceExportBundle: exportMocks.runWorkspaceExportBundle,
}));

function job(id: string): JobItem {
  return {
    id, run_number: 1, parent_job_id: null,
    label: id, status: 'complete', progress: 1, stage: null,
    stage_message: null, created_at: '2026-08-08T00:00:00Z',
    queued_at: '2026-08-08T00:00:00Z', started_at: null,
    completed_at: '2026-08-08T00:00:01Z', config_summary: {}, solve_options: {} as JobItem['solve_options'],
    has_results: true, has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null, script_snapshot: null,
    design_revision: 1, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [],
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as {
    snapshot: JobsSnapshot;
    listeners: Set<() => void>;
  };
  manager.snapshot = {
    connection: 'connected', epoch: 1, cursor: 1, jobs, error: null,
  };
  manager.listeners.forEach((listener) => listener());
}

function response(frequencies: number[], ok = true): Response {
  return new Response(JSON.stringify(ok ? { frequencies, metadata: {} } : { detail: 'result vanished' }), {
    status: ok ? 200 : 500,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('atomic results display transitions', () => {
  let host: HTMLDivElement;
  let root: Root;
  let pending: Map<string, (value: Response) => void>;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resultsCache.clear();
    provisionalResults.clear();
    compareSelection.clear();
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['summary'] });
    exportMocks.runWorkspaceExportBundle.mockReset().mockResolvedValue({
      directory: 'C:\\Users\\tester\\AppData\\Roaming\\WaveguideGenerator\\workspace\\1_old',
      files: ['C:\\Users\\tester\\AppData\\Roaming\\WaveguideGenerator\\workspace\\1_old\\1_old.csv'],
      failures: [],
    });
    pending = new Map();
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => new Promise<Response>((resolve) => {
      pending.set(String(input).split('/').at(-1)!, resolve);
    })));
    publishJobs([
      job('old'), job('old-overlay'), job('new'), job('new-overlay'), job('broken'),
    ]);
    compareSelection.setPrimary('old');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    compareSelection.clear();
    resultsCache.clear();
    provisionalResults.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('keeps one complete previous set until the new set swaps atomically', async () => {
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('old');

    act(() => compareSelection.setPrimary('new'));
    expect(host.textContent).toContain('Showing previous results');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('old');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-set')).toBe('old');

    await act(async () => { pending.get('new')!(response([200, 400])); });
    expect(host.textContent).not.toContain('Showing previous results');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('new');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-set')).toBe('new');
  });

  it('clears the previous charts if the incoming result fetch fails', async () => {
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });
    act(() => compareSelection.setPrimary('broken'));
    expect(host.textContent).toContain('Showing previous results');

    await act(async () => { pending.get('broken')!(response([], false)); });
    expect(host.textContent).toContain('Results unavailable');
    expect(host.textContent).toContain('result vanished');
    expect(host.querySelector('.results-panel')?.hasAttribute('data-result-primary')).toBe(false);
    expect(host.querySelector('.result-grid')).toBeNull();

    // The old failure is keyed to `broken`; it must not flash under a new
    // selection during the render before that selection's effect clears it.
    act(() => compareSelection.setPrimary('new'));
    expect(host.textContent).not.toContain('Results unavailable');
    expect(host.textContent).not.toContain('result vanished');
    expect(host.textContent).toContain('Loading results');
    // Settle the module-level request dedupe before the next test.
    await act(async () => { pending.get('new')!(response([200])); });
  });

  it('retries a failed pinned result without changing the selection', async () => {
    compareSelection.setPrimary('broken');
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('broken')!(response([], false)); });

    expect(host.textContent).toContain('Results unavailable');
    const retry = [...host.querySelectorAll('button')].find((button) => button.textContent === 'Retry');
    expect(retry).toBeDefined();

    await act(async () => { retry!.click(); });
    expect(fetch).toHaveBeenCalledTimes(2);
    await act(async () => { pending.get('broken')!(response([315])); });

    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'broken', following: false });
    expect(host.textContent).not.toContain('Results unavailable');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('broken');
  });

  it('retries a failed result while following the latest solve', async () => {
    publishJobs([job('broken'), job('old')]);
    compareSelection.followLatest('broken');
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('broken')!(response([], false)); });

    expect(host.textContent).toContain('Results unavailable');
    const retry = [...host.querySelectorAll('button')].find((button) => button.textContent === 'Retry');
    expect(retry).toBeDefined();

    await act(async () => { retry!.click(); });
    expect(fetch).toHaveBeenCalledTimes(2);
    await act(async () => { pending.get('broken')!(response([400])); });

    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'broken', following: true });
    expect(host.textContent).not.toContain('Results unavailable');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('broken');
  });

  it('does not mix a resolved new primary with a still-loading overlay', async () => {
    act(() => compareSelection.toggleOverlay('old-overlay'));
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });
    expect(host.querySelector('.result-grid')).toBeNull();
    await act(async () => { pending.get('old-overlay')!(response([125])); });
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('old');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-set')).toContain('old-overlay');

    await act(async () => {
      compareSelection.toggleOverlay('old-overlay');
      compareSelection.setPrimary('new');
      compareSelection.toggleOverlay('new-overlay');
      await Promise.resolve();
    });
    expect(host.textContent).toContain('Showing previous results');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('old');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-set')).toContain('old-overlay');

    await act(async () => { pending.get('new')!(response([200])); });
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('old');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-set')).toContain('old-overlay');

    await act(async () => { pending.get('new-overlay')!(response([250])); });
    expect(host.textContent).not.toContain('Showing previous results');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('new');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-set')).toContain('new-overlay');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-set')).not.toContain('old');
  });

  it('follows the first streamed frequency, then swaps to the canonical result', async () => {
    publishJobs([job('old')]);
    compareSelection.followLatest('old');
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });

    const liveJob = job('live');
    Object.assign(liveJob, {
      status: 'running', progress: .4, has_results: false, completed_at: null,
      solve_options: { num_frequencies: 2 } as JobItem['solve_options'],
    });
    await act(async () => {
      publishJobs([liveJob, job('old')]);
      provisionalResults.apply('live', 1, {
        frequencies: [200],
        metadata: { provisional: { completed_frequency_count: 1, expected_frequency_count: 2 } },
      });
      await Promise.resolve();
    });

    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'live', following: true });
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('live');
    expect(host.textContent).toContain('Live · 1/2 frequencies');
    expect(pending.has('live')).toBe(false);
    expect([...host.querySelectorAll('button')].find((button) => button.textContent?.startsWith('Export'))?.disabled).toBe(true);

    const completeLive = job('live');
    await act(async () => {
      publishJobs([completeLive, job('old')]);
      await Promise.resolve();
    });
    expect(pending.has('live')).toBe(true);
    await act(async () => { pending.get('live')!(response([200, 400])); });
    expect(host.textContent).not.toContain('Live ·');
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('live');
    expect(provisionalResults.get('live')).toBeUndefined();
  });

  it('writes the Results toolbar export through the selected Workspace', async () => {
    preferencesStore.update({ exportFormats: ['csv'] });
    const patchMetadata = vi.spyOn(jobsSocket, 'patchMetadata').mockResolvedValue(undefined);
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });

    const exportButton = [...host.querySelectorAll('button')]
      .find((button) => button.textContent === 'Export (1)');
    expect(exportButton).toBeDefined();
    await act(async () => { exportButton!.click(); });

    expect(exportMocks.runWorkspaceExportBundle).toHaveBeenCalledWith(
      expect.objectContaining({ jobStem: '1_old' }),
      ['csv'],
    );
    expect(patchMetadata).toHaveBeenCalledWith('old', {
      exported_files: ['C:\\Users\\tester\\AppData\\Roaming\\WaveguideGenerator\\workspace\\1_old\\1_old.csv'],
    });
    expect(host.textContent).toContain('1 file written to C:\\Users\\tester\\AppData\\Roaming\\WaveguideGenerator\\workspace\\1_old');
  });
});
