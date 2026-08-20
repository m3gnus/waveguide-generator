import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { serializeDesign, designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import { preferencesStore } from '../prefs/preferences';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { ResultsPanel } from './ResultsPanel';

const modelMocks = vi.hoisted(() => ({ showJobModel: vi.fn() }));
vi.mock('../jobs/showJobModel', () => ({ showJobModel: modelMocks.showJobModel }));

function job(id: string, runNumber: number, revision: number, imported = false): JobItem {
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
    completed_at: '2026-08-20T00:01:00Z',
    config_summary: imported ? { geometry_type: 'imported' } : {},
    solve_options: {} as JobItem['solve_options'],
    has_results: true,
    has_mesh_artifact: imported,
    field_plane_available: false,
    label: id,
    error_message: null,
    cancellation_requested: false,
    mesh_stats: null,
    script_snapshot: null,
    design_revision: revision,
    polar_grid: {},
    rating: null,
    exported_files: [],
    auto_export_completed_at: null,
    auto_export_formats: {},
    raw_results_file: null,
    mesh_artifact_file: null,
    log_tail: [],
    cad_source: imported ? {
      ingest_id: `wgi_${id}`,
      design_id: `wgd_${id}`,
      lineage_id: `wgl_${id}`,
      archive_stem: id,
      manifest_sha256: `sha256:${id}`,
      document_name: `${id} document`,
      return_state_hash: null,
    } : null,
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

describe('results run coherence', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetCadReturnStore();
    resetDocumentStore();
    workspaceModeStore.setMode('parametric');
    compareSelection.clear();
    provisionalResults.clear();
    resultsCache.clear();
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['summary'] });
    modelMocks.showJobModel.mockReset().mockResolvedValue(true);
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ frequencies: [1_000], metadata: {} }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )));
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
    workspaceModeStore.setMode('parametric');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('auto-follows only runs from the active workspace model family', async () => {
    const parametric = job('parametric', 1, useDesignStore.getState().designRevision);
    const imported = job('cad', 2, 0, true);
    publishJobs([imported, parametric]);
    compareSelection.followLatest(null);

    await act(async () => { root.render(<ResultsPanel/>); await Promise.resolve(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'parametric', following: true });

    await act(async () => {
      useCadReturnStore.setState({ ingestRecord: { ingest_id: 'wgi_cad' } as never });
      workspaceModeStore.setMode('cad');
      await Promise.resolve();
    });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'cad', following: true });
  });

  it('names an older-revision mismatch and offers restore and solve actions', async () => {
    useDesignStore.getState().updateField('R', 180);
    const older = job('archive', 4, 1);
    const solved = designForFamily('OSSE');
    solved.L = 321;
    older.script_snapshot = { version: 1, design: serializeDesign(solved) };
    publishJobs([older]);
    compareSelection.setPrimary(older.id);
    const run = vi.fn().mockResolvedValue(undefined);
    (jobsCoordinatorBridge.getSnapshot() as unknown as { run: typeof run }).run = run;

    await act(async () => { root.render(<ResultsPanel/>); });
    const notice = host.querySelector('.result-coherence-notice')!;
    expect(notice.textContent).toContain('Showing #4 · archive — the design has changed since this run.');

    await act(async () => { [...notice.querySelectorAll('button')].find((button) => button.textContent === "Restore this run's design")!.click(); });
    expect(useDesignStore.getState().design.L).toBe(321);
    await act(async () => { [...host.querySelectorAll('button')].find((button) => button.textContent === 'Solve current design')!.click(); });
    expect(run).toHaveBeenCalledWith(useDesignStore.getState().design, useDesignStore.getState().designRevision);
  });

  it('names an other-model mismatch and wires model recall and latest recovery', async () => {
    const latest = job('live', 5, useDesignStore.getState().designRevision);
    const imported = job('archive-cad', 6, 0, true);
    publishJobs([imported, latest]);
    compareSelection.setPrimary(imported.id);

    await act(async () => { root.render(<ResultsPanel/>); });
    const notice = host.querySelector('.result-coherence-notice')!;
    expect(notice.textContent).toContain('Showing #6 · archive-cad (CAD import) — not the model in the viewport.');

    await act(async () => { [...notice.querySelectorAll('button')].find((button) => button.textContent === 'Show this model')!.click(); });
    expect(modelMocks.showJobModel).toHaveBeenCalledWith(imported, expect.any(Function));

    await act(async () => { [...notice.querySelectorAll('button')].find((button) => button.textContent === 'Back to latest')!.click(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: latest.id, following: true });
  });

  it('marks imported and stale-revision chips and compare options', async () => {
    useDesignStore.getState().updateField('R', 180);
    const current = job('current', 7, useDesignStore.getState().designRevision);
    const stale = job('stale', 8, 1);
    const imported = job('imported', 9, 0, true);
    const staleOption = job('stale-option', 10, 1);
    const importedOption = job('imported-option', 11, 0, true);
    publishJobs([current, stale, imported, staleOption, importedOption]);
    compareSelection.setPrimary(imported.id);
    compareSelection.toggleOverlay(stale.id);

    await act(async () => { root.render(<ResultsPanel/>); });
    const markers = [...host.querySelectorAll('.result-context-marker')].map((item) => item.textContent);
    expect(markers).toEqual(expect.arrayContaining(['CAD import', 'rev 1']));
    const options = [...host.querySelectorAll<HTMLOptionElement>('.result-compare-add option')].map((option) => option.textContent);
    expect(options).toContain('#7 · current');
    expect(options).toContain('#10 · stale-option · rev 1');
    expect(options).toContain('#11 · imported-option · CAD import');
  });
});
