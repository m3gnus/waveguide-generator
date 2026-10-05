import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { serializeDesign, designForFamily, resetDesignStore, useDesignStore, type DesignDocument } from '../stores/design';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import { preferencesStore } from '../prefs/preferences';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { ResultsPanel } from './ResultsPanel';
import { JobsPanel } from './JobsPanel';

function job(id: string, runNumber: number, design: DesignDocument | null, extra: Partial<JobItem> = {}): JobItem {
  return {
    id,
    run_number: runNumber,
    parent_job_id: null,
    status: 'complete',
    progress: 1,
    stage: null,
    stage_message: null,
    created_at: `2026-08-20T00:00:${String(runNumber).padStart(2, '0')}Z`,
    queued_at: '2026-08-20T00:00:00Z',
    started_at: null,
    completed_at: `2026-08-20T00:01:${String(runNumber).padStart(2, '0')}Z`,
    config_summary: { formula_type: 'OSSE' },
    solve_options: {} as JobItem['solve_options'],
    has_results: true,
    has_mesh_artifact: false,
    field_plane_available: false,
    label: id,
    error_message: null,
    cancellation_requested: false,
    mesh_stats: null,
    script_snapshot: design ? { version: 1, design: serializeDesign(design) } : null,
    design_revision: 1,
    polar_grid: {},
    rating: null,
    exported_files: [],
    auto_export_completed_at: null,
    auto_export_formats: {},
    raw_results_file: null,
    mesh_artifact_file: null,
    log_tail: [],
    cad_source: null,
    ...extra,
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

/** The real update path: prune + notify, as a snapshot or metadata patch does. */
function socketUpdate(jobs: JobItem[]): void {
  (jobsSocket as unknown as { update(patch: Partial<JobsSnapshot>): void }).update({ jobs });
}

function finalResult(jobId: string) {
  const digest = 'a'.repeat(64);
  return {
    result_kind: 'parametric',
    result_contract_version: 1,
    client_request_id: null,
    client_metadata: { job: jobId },
    provenance: {
      schema_version: 1, wg_version: 'test', dependency_shas: {},
      request_sha256: digest, geometry_sha256: digest, solve_options_sha256: digest,
      request_identity: 'execution',
      execution_request_sha256: digest, execution_geometry_sha256: digest, execution_solve_options_sha256: digest,
      effective_request_sha256: digest, effective_geometry_sha256: digest, effective_solve_options_sha256: digest,
      resolved_engine: 'test',
    },
    frequencies: [1_000],
    metadata: {},
  };
}

const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };

describe('selecting a run whose results were cleaned up', () => {
  let host: HTMLDivElement;
  let root: Root;
  const fetchLog: string[] = [];

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetCadReturnStore();
    importedMeshStore.clear();
    resetDocumentStore();
    workspaceModeStore.setMode('parametric');
    compareSelection.clear();
    provisionalResults.clear();
    resultsCache.clear();
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['summary'] });
    fetchLog.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      fetchLog.push(url);
      const match = /\/api\/results\/([^/?]+)/.exec(url);
      if (match) {
        const id = decodeURIComponent(match[1]);
        if (id === 'run-1') return new Response(JSON.stringify({ detail: 'Results not found' }), { status: 404, headers: { 'Content-Type': 'application/json' } });
        return new Response(JSON.stringify(finalResult(id)), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
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

  const shownPrimary = () => host.querySelector('[data-result-primary]')?.getAttribute('data-result-primary') ?? null;
  const selectedCard = () => host.querySelector('.job-card.selected .job-title')?.textContent ?? null;
  const dockExport = () => host.querySelector<HTMLButtonElement>('.results-panel button[title^="Export the current result"]');
  function clickCard(label: string): void {
    const button = [...host.querySelectorAll<HTMLButtonElement>('button.job-select')]
      .find((b) => b.getAttribute('aria-label')?.endsWith(` · ${label}`));
    if (!button) throw new Error(`no select button for ${label}`);
    button.click();
  }

  async function mount(): Promise<JobItem[]> {
    const designA = designForFamily('OSSE'); designA.L = 111;
    const designB = designForFamily('OSSE'); designB.L = 222;
    const designC = designForFamily('OSSE'); designC.L = 333;
    useDesignStore.setState({ design: designA });
    const jobs = [
      job('run-3', 3, designA),
      job('run-2', 2, designB),
      job('run-1', 1, designC, { has_results: false, results_discarded_at: '2026-09-01T00:00:00Z' }),
    ];
    publishJobs(jobs);
    compareSelection.followLatest(null);
    await act(async () => { root.render(<><ResultsPanel/><JobsPanel/></>); await flush(); });
    return jobs;
  }

  it('follows the latest run until a run is picked', async () => {
    await mount();
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'run-3', following: true });
    expect(shownPrimary()).toBe('run-3');
    expect(selectedCard()).toContain('run-3');
  });

  it('still selects a run that has results', async () => {
    await mount();
    await act(async () => { clickCard('run-2'); await flush(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'run-2', following: false });
    expect(shownPrimary()).toBe('run-2');
    expect(selectedCard()).toContain('run-2');
    expect(dockExport()?.disabled).toBe(false);
  });

  it('selects a cleaned-up run, drops the stale charts, says why, and keeps Export off', async () => {
    const jobs = await mount();
    await act(async () => { clickCard('run-2'); await flush(); });
    expect(shownPrimary()).toBe('run-2');

    await act(async () => { clickCard('run-1'); await flush(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'run-1', following: false });
    expect(selectedCard()).toContain('run-1');
    expect(shownPrimary()).toBeNull();
    expect(host.querySelector('.results-panel')?.textContent).toMatch(/Run #1's results were cleaned up .*Solve it again to see them\./);
    expect(dockExport()?.disabled).toBe(true);
    expect(fetchLog.some((url) => url.includes('/api/results/run-1'))).toBe(false);
    // The card's own Export is reachable and belongs to this run.
    expect(host.querySelector('.job-card.selected [aria-label="Export run-1"], .job-card.selected [aria-haspopup]')).not.toBeNull();

    // It stays selected through the periodic socket updates that used to release it.
    await act(async () => { socketUpdate([...jobs]); await flush(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'run-1', following: false });
    expect(selectedCard()).toContain('run-1');
    expect(shownPrimary()).toBeNull();
  });

  it('shows a run again once the user picks one with results', async () => {
    await mount();
    await act(async () => { clickCard('run-1'); await flush(); });
    expect(shownPrimary()).toBeNull();
    await act(async () => { clickCard('run-3'); await flush(); });
    expect(shownPrimary()).toBe('run-3');
    expect(host.querySelector('.results-panel')?.textContent).not.toMatch(/cleaned up/);
  });

  it('releases a pinned run whose results are cleaned while it is on screen, so a newer run takes over', async () => {
    const jobs = await mount();
    await act(async () => { clickCard('run-2'); await flush(); });
    expect(shownPrimary()).toBe('run-2');

    // Retention lands on the run being viewed.
    const cleaned = jobs.map((j) => j.id === 'run-2' ? { ...j, has_results: false, results_discarded_at: '2026-10-05T00:00:00Z' } : j);
    await act(async () => { socketUpdate(cleaned); await flush(); });
    // Released, as before: nothing is pinned any more.
    expect(compareSelection.getSnapshot()).toMatchObject({ following: true });
    expect(compareSelection.getSnapshot().primary).not.toBe('run-2');

    // A newer run of the model now on screen completes and is shown.
    const newer = job('run-4', 4, useDesignStore.getState().design);
    await act(async () => { socketUpdate([newer, ...cleaned]); await flush(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'run-4', following: true });
    expect(shownPrimary()).toBe('run-4');
  });

  it('holds a cleaned run the user picks again after retention released it with no newer run to take over', async () => {
    const designA = designForFamily('OSSE'); designA.L = 111;
    useDesignStore.setState({ design: designA });
    const solo = job('solo', 1, designA);
    publishJobs([solo]);
    compareSelection.followLatest(null);
    await act(async () => { root.render(<><ResultsPanel/><JobsPanel/></>); await flush(); });
    await act(async () => { clickCard('solo'); await flush(); });
    expect(shownPrimary()).toBe('solo');

    const cleaned = { ...solo, has_results: false, results_discarded_at: '2026-10-05T00:00:00Z' };
    await act(async () => { socketUpdate([cleaned]); await flush(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ following: true });

    await act(async () => { clickCard('solo'); await flush(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'solo', following: false });
    expect(host.querySelector('.results-panel')?.textContent).toMatch(/Run #1's results were cleaned up/);
  });

  it('holds a run picked after retention cleaned it off screen, even though it was shown earlier', async () => {
    const jobs = await mount();
    await act(async () => { clickCard('run-2'); await flush(); });
    expect(shownPrimary()).toBe('run-2');
    await act(async () => { clickCard('run-1'); await flush(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'run-1', following: false });

    const cleaned = jobs.map((j) => j.id === 'run-2' ? { ...j, has_results: false, results_discarded_at: '2026-10-05T00:00:00Z' } : j);
    await act(async () => { socketUpdate(cleaned); await flush(); });
    await act(async () => { clickCard('run-2'); await flush(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'run-2', following: false });
    expect(host.querySelector('.results-panel')?.textContent).toMatch(/Run #2's results were cleaned up/);
  });

  it('shows the cleanup message in CAD mode when the cleaned run matches the loaded model', async () => {
    const cad = job('cad', 5, null, { has_results: false, results_discarded_at: '2026-09-01T00:00:00Z', config_summary: { geometry_type: 'imported' }, cad_source: {
      ingest_id: 'wgi_cad', design_id: 'wgd_cad', lineage_id: 'wgl_cad', archive_stem: 'cad', manifest_sha256: 'sha256:cad', document_name: 'cad document', return_state_hash: null,
    } as JobItem['cad_source'] });
    publishJobs([cad]);
    useCadReturnStore.setState({ ingestRecord: { ingest_id: 'wgi_cad' } as never });
    importedMeshStore.setCad({ source: 'cad', ingestId: 'wgi_cad' } as never);
    workspaceModeStore.setMode('cad');
    compareSelection.setPrimary('cad');
    await act(async () => { root.render(<ResultsPanel/>); await flush(); });
    const text = host.querySelector('.results-panel')?.textContent ?? '';
    expect(text).toMatch(/Run #5's results were cleaned up/);
    expect(text).not.toMatch(/Not solved yet/);
  });

  it('selects cleaned runs whose design cannot be read, without loading anything', async () => {
    const designA = designForFamily('OSSE'); designA.L = 111;
    useDesignStore.setState({ design: designA });
    const legacy = job('legacy', 2, null, { has_results: false, results_discarded_at: '2026-09-01T00:00:00Z' });
    const imported = job('imported', 1, null, { has_results: false, results_discarded_at: '2026-09-01T00:00:00Z', config_summary: { geometry_type: 'imported' } });
    publishJobs([job('run-3', 3, designA), legacy, imported]);
    compareSelection.followLatest(null);
    await act(async () => { root.render(<><ResultsPanel/><JobsPanel/></>); await flush(); });

    for (const [label, run] of [['legacy', 2], ['imported', 1]] as const) {
      await act(async () => { clickCard(label); await flush(); });
      expect(compareSelection.getSnapshot()).toMatchObject({ primary: label, following: false });
      expect(selectedCard()).toContain(label);
      expect(host.querySelector('.results-panel')?.textContent).toMatch(new RegExp(`Run #${run}'s results were cleaned up`));
      expect(useDesignStore.getState().design.L).toBe(111);
    }
    await act(async () => { clickCard('legacy'); await flush(); });
    expect(host.querySelector('.job-card.selected')?.textContent).toMatch(/cannot be reopened or rerun/);
  });
});
