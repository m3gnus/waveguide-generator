import { serializeDesign, useDesignStore, resetDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { useCadReturnStore } from '../stores/cadReturn';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { resultViewStore } from '../stores/resultView';
import { useRunExportStore } from '../stores/runExports';
import { provideExportDestinationPrompt } from './exportDestinationPrompt';
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
    cancellation_requested: false, mesh_stats: null, script_snapshot: { version: 1, design: serializeDesign(useDesignStore.getState().design) },
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
  const digest = 'a'.repeat(64);
  const result = {
    result_kind: 'parametric',
    result_contract_version: 1,
    client_request_id: null,
    client_metadata: {},
    provenance: {
      schema_version: 1,
      wg_version: 'test',
      dependency_shas: {},
      request_sha256: digest,
      geometry_sha256: digest,
      solve_options_sha256: digest,
      request_identity: 'execution',
      execution_request_sha256: digest,
      execution_geometry_sha256: digest,
      execution_solve_options_sha256: digest,
      effective_request_sha256: digest,
      effective_geometry_sha256: digest,
      effective_solve_options_sha256: digest,
      resolved_engine: 'test',
    },
    frequencies,
    metadata: {},
  };
  return new Response(JSON.stringify(ok ? result : { detail: 'result vanished' }), {
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
    resetDesignStore(); workspaceModeStore.setMode('parametric'); importedMeshStore.clear();
    useRunExportStore.getState().resetForTests();
    resultViewStore.resetForTests();
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
    // The export dialog the top bar mounts, answered as the user would.
    provideExportDestinationPrompt(async () => ({
      token: 'destination-handle', directory: '/exports',
    }));
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
    provideExportDestinationPrompt(null);
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    compareSelection.clear();
    useRunExportStore.getState().resetForTests();
    resultViewStore.resetForTests();
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

  // The rail kept showing the previous run after a new solve finished: a
  // result picked by hand pins the primary slot, and the pin outlived every
  // later solve. A submitted run claims the slot at submission time and takes
  // it the moment it has something to show -- not before, so the pinned result
  // stays on screen for the length of the solve.
  it('hands a pinned slot to the run a new solve submitted, once it has results', async () => {
    publishJobs([job('old')]);
    compareSelection.setPrimary('old');
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('old');

    const running = job('fresh');
    Object.assign(running, { status: 'running', progress: .2, has_results: false, completed_at: null });
    await act(async () => {
      compareSelection.awaitRun('fresh');
      publishJobs([running, job('old')]);
      await Promise.resolve();
    });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'old', following: false, awaiting: 'fresh' });
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('old');

    await act(async () => { publishJobs([job('fresh'), job('old')]); await Promise.resolve(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'fresh', following: true, awaiting: null });
    await act(async () => { pending.get('fresh')!(response([200])); });
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('fresh');
  });

  it('leaves the pinned result alone when the run it submitted fails', async () => {
    publishJobs([job('old')]);
    compareSelection.setPrimary('old');
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });

    const failed = job('fresh');
    Object.assign(failed, { status: 'error', has_results: false, error_message: 'solver failed' });
    await act(async () => {
      compareSelection.awaitRun('fresh');
      publishJobs([failed, job('old')]);
      await Promise.resolve();
    });

    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'old', following: false, awaiting: null });
    expect(host.querySelector('.results-panel')?.getAttribute('data-result-primary')).toBe('old');
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
      // The claim a submitted solve leaves behind: nothing else may take the
      // charts off the run on screen, so a streamed run has to be one this
      // window asked for.
      compareSelection.awaitRun('live');
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

  it.each([true, false])('labels adaptive snapshots with acquired frequencies, with provisional metadata=%s', async (withMetadata) => {
    preferencesStore.update({ chartTypes: ['summary', 'phase_response', 'impedance', 'directivity_index'] });
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });
    const liveJob = job('adaptive');
    Object.assign(liveJob, {
      status: 'running', progress: .4, has_results: false, completed_at: null,
      solve_options: { num_frequencies: 48 } as JobItem['solve_options'],
    });
    const frequencies = Array.from({ length: 48 }, (_, i) => 100 + i * 10);
    await act(async () => {
      compareSelection.awaitRun('adaptive');
      publishJobs([liveJob]);
      provisionalResults.apply('adaptive', 1, {
        frequencies,
        frequency_status: frequencies.map((_, i) => i < 8 ? 'solved' : 'interpolated'),
        ...(withMetadata ? { metadata: { provisional: { completed_frequency_count: 8, expected_frequency_count: 48 } } } : {}),
      });
      await Promise.resolve();
    });
    expect(host.textContent).toContain('Live · 8/48 frequencies');
    const cards = [...host.querySelectorAll('.result-card')];
    expect(cards.length).toBeGreaterThan(1);
    for (const card of cards) {
      const disclosure = card.querySelector('[role="note"][title*="Adaptive sampling"]');
      expect(disclosure?.textContent).toContain('reconstructed estimates');
      expect(disclosure?.getAttribute('title')).toContain('8 solved, 40 interpolated');
      expect(disclosure?.getAttribute('title')).toContain('narrow resonances can be missed');
    }
    vi.useFakeTimers();
    try {
      await act(async () => {
        provisionalResults.apply('adaptive', 2, {
          frequencies,
          frequency_status: frequencies.map((_, i) => i < 12 ? 'solved' : 'interpolated'),
          ...(withMetadata ? { metadata: { provisional: { completed_frequency_count: 12, expected_frequency_count: 48 } } } : {}),
        });
        await vi.advanceTimersByTimeAsync(250);
      });
      expect(host.textContent).toContain('Live · 12/48 frequencies');
    } finally {
      vi.useRealTimers();
    }
  });

  it('exports available preferences with skipped reasons and the shared CAD menu in the Results toolbar', async () => {
    vi.spyOn(jobsSocket, 'patchMetadata').mockResolvedValue(undefined);
    preferencesStore.update({ exportFormats: ['csv', 'step'] });
    workspaceModeStore.setImportedInput('cad'); workspaceModeStore.setMode('cad');
    useCadReturnStore.setState({ ingestRecord: { ingest_id: 'wgi_old' } as never });
    importedMeshStore.setCad({ source: 'cad', ingestId: 'wgi_old' } as never);
    publishJobs([{ ...job('old'), script_snapshot: null, cad_source: { ingest_id: 'wgi_old' } as JobItem['cad_source'],
      config_summary: { formula_type: 'cad-import', geometry_type: 'imported' },
      design_availability: { reopenable: false, source: 'cad-import', reason_code: 'imported_geometry',
        reason: 'Solved from a Fusion return; there are no WG parameters to edit.', note: null },
    }]);
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });
    const trigger = host.querySelector<HTMLButtonElement>('.results-panel .action-menu-primary')!;
    expect(trigger.textContent).toBe('Export CSV + STEP');
    expect(trigger.title).toContain('STEP solid (.step)');
    await act(async () => { trigger.click(); });
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('STEP export is not available for imported models.');
    expect(exportMocks.runWorkspaceExportBundle).toHaveBeenCalledWith(expect.any(Object), ['csv'], 'confirm');
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Skipped STEP');
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('1 file written');
    expect(document.querySelector('[role="menu"]')).toBeNull();
    act(() => host.querySelector<HTMLButtonElement>('.action-menu-chevron')!.click());
    const menu = document.querySelector('[role="menu"]')!;
    expect(menu.textContent).toContain('STEP export is not available for imported models.');
    for (const label of ['Send to CAD', 'Parameter config', 'STL mesh', 'Fusion curves']) {
      expect(menu.textContent).not.toContain(label);
    }
  });

  it('forwards the full result envelope and active channel through the shared export control', async () => {
    preferencesStore.update({ exportFormats: ['csv'] });
    vi.spyOn(jobsSocket, 'patchMetadata').mockResolvedValue(undefined);
    const channel = await response([100]).json();
    const envelope = { ...channel, channel_order: ['drive-mf', 'drive-hf'],
      channels: { 'drive-mf': channel, 'drive-hf': { ...channel, frequencies: [200] } },
    };
    resultViewStore.setView('drive-hf');
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(new Response(JSON.stringify(envelope), {
      headers: { 'Content-Type': 'application/json' },
    })); });
    await act(async () => { host.querySelector<HTMLButtonElement>('.action-menu-primary')!.click(); });
    expect(exportMocks.runWorkspaceExportBundle).toHaveBeenCalledWith(
      expect.objectContaining({ result: envelope, channelId: 'drive-hf', jobId: 'old' }), ['csv'], 'confirm',
    );
  });

  it('writes the Results toolbar export through the selected Workspace', async () => {
    preferencesStore.update({ exportFormats: ['csv'] });
    const patchMetadata = vi.spyOn(jobsSocket, 'patchMetadata').mockResolvedValue(undefined);
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { pending.get('old')!(response([100])); });

    const exportButton = [...host.querySelectorAll('button')]
      .find((button) => button.textContent === 'Export CSV');
    expect(exportButton).toBeDefined();
    await act(async () => { exportButton!.click(); });

    expect(exportMocks.runWorkspaceExportBundle).toHaveBeenCalledWith(
      expect.objectContaining({ jobStem: '1_old', destinationToken: 'destination-handle' }),
      ['csv'],
      'confirm',
    );
    expect(patchMetadata).toHaveBeenCalledWith('old', {
      exported_files: ['C:\\Users\\tester\\AppData\\Roaming\\WaveguideGenerator\\workspace\\1_old\\1_old.csv'],
    });
    expect(host.textContent).toContain('1 file written to C:\\Users\\tester\\AppData\\Roaming\\WaveguideGenerator\\workspace\\1_old');
  });
});
