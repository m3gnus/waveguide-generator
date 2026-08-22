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
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { ResultsPanel } from './ResultsPanel';

const modelMocks = vi.hoisted(() => ({ showJobModel: vi.fn() }));
vi.mock('../jobs/showJobModel', () => ({ showJobModel: modelMocks.showJobModel }));

function job(id: string, runNumber: number, design: DesignDocument | null, imported = false): JobItem {
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

/** The design the editor is holding right now, as a run would have stored it. */
function liveDesign(): DesignDocument {
  return useDesignStore.getState().design;
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

function toolbarButton(host: HTMLElement, text: string): HTMLButtonElement | undefined {
  return [...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === text);
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
    const parametric = job('parametric', 1, liveDesign());
    const imported = job('cad', 2, null, true);
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

  it('marks an edited-since run on its chip and offers restore and solve', async () => {
    const solved = designForFamily('OSSE');
    solved.L = 321;
    const older = job('archive', 4, solved);
    useDesignStore.getState().updateField('R', 180);
    publishJobs([older]);
    compareSelection.setPrimary(older.id);
    const run = vi.fn().mockResolvedValue(undefined);
    (jobsCoordinatorBridge.getSnapshot() as unknown as { run: typeof run }).run = run;

    await act(async () => { root.render(<ResultsPanel/>); });
    const chip = host.querySelector('.result-chip.stale')!;
    expect(chip.getAttribute('title')).toContain('The design has been edited since this run was solved.');
    const marker = host.querySelector<HTMLButtonElement>('button.result-context-marker.stale')!;
    expect(marker.textContent).toBe('edited since');
    expect(document.querySelector('.result-coherence-popover')).toBeNull();

    await act(async () => { marker.click(); });
    const popover = document.querySelector<HTMLElement>('.result-coherence-popover')!;
    expect(popover.textContent).toContain('The design has been edited since this run was solved.');

    await act(async () => { toolbarButton(popover, "Restore this run's design")!.click(); });
    expect(useDesignStore.getState().design.L).toBe(321);
    // Restoring the design answers the marker, so both it and its menu go.
    expect(document.querySelector('.result-coherence-popover')).toBeNull();
    expect(host.querySelector('button.result-context-marker.stale')).toBeNull();

    await act(async () => { useDesignStore.getState().updateField('R', 181); });
    await act(async () => { host.querySelector<HTMLButtonElement>('button.result-context-marker.stale')!.click(); });
    await act(async () => { toolbarButton(document.body, 'Solve current design')!.click(); });
    expect(run).toHaveBeenCalledWith(useDesignStore.getState().design, useDesignStore.getState().designRevision);
  });

  it('marks an other-model run and wires model recall and newest-run recovery', async () => {
    const latest = job('live', 5, liveDesign());
    const imported = job('archive-cad', 6, null, true);
    publishJobs([imported, latest]);
    compareSelection.setPrimary(imported.id);

    await act(async () => { root.render(<ResultsPanel/>); });
    const marker = host.querySelector<HTMLButtonElement>('button.result-context-marker.stale')!;
    expect(marker.textContent).toBe('other model');
    expect(marker.getAttribute('title')).toContain("This run's model is not the one in the viewport.");

    await act(async () => { marker.click(); });
    await act(async () => { toolbarButton(document.body, 'Show this model')!.click(); });
    expect(modelMocks.showJobModel).toHaveBeenCalledWith(imported, expect.any(Function));

    await act(async () => { host.querySelector<HTMLButtonElement>('button.result-context-marker.stale')!.click(); });
    await act(async () => { toolbarButton(document.body, 'Show newest run')!.click(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: latest.id, following: true });
  });

  it('states provenance only when it differs from the workspace mode', async () => {
    const solved = designForFamily('OSSE');
    const current = job('current', 7, liveDesign());
    const stale = job('stale', 8, solved);
    const imported = job('imported', 9, null, true);
    const staleOption = job('stale-option', 10, solved);
    const importedOption = job('imported-option', 11, null, true);
    useDesignStore.getState().updateField('R', 180);
    publishJobs([current, stale, imported, staleOption, importedOption]);
    compareSelection.setPrimary(stale.id);
    compareSelection.toggleOverlay(imported.id);

    await act(async () => { root.render(<ResultsPanel/>); });
    expect(host.querySelector('button.result-context-marker.stale')!.textContent).toBe('edited since');
    const markers = [...host.querySelectorAll('.result-chip > .result-context-marker')].map((item) => item.textContent);
    expect(markers).toEqual(['CAD']);
    const options = [...host.querySelectorAll<HTMLOptionElement>('.result-compare-add option')].map((option) => option.textContent);
    expect(options).toContain('#10 · stale-option · edited since');
    expect(options).toContain('#11 · imported-option · CAD');

    // In CAD mode the pill would be on every run, so it moves to the exception.
    await act(async () => {
      useCadReturnStore.setState({ ingestRecord: { ingest_id: 'wgi_imported' } as never });
      workspaceModeStore.setMode('cad');
      await Promise.resolve();
    });
    const cadOptions = [...host.querySelectorAll<HTMLOptionElement>('.result-compare-add option')].map((option) => option.textContent);
    expect(cadOptions).toContain('#11 · imported-option · other model');
    expect(cadOptions).toContain('#7 · current · Parametric');
  });

  it('offers a run this window did not ask for instead of showing it', async () => {
    const shown = job('shown', 12, liveDesign());
    publishJobs([shown]);
    compareSelection.setPrimary(shown.id);
    await act(async () => { root.render(<ResultsPanel/>); await Promise.resolve(); });
    expect(toolbarButton(host, 'New · #13 → Show')).toBeUndefined();

    const elsewhere = job('elsewhere', 13, liveDesign());
    await act(async () => { publishJobs([elsewhere, shown]); await Promise.resolve(); });
    expect(compareSelection.getSnapshot().primary).toBe('shown');
    const offer = toolbarButton(host, 'New · #13 → Show')!;
    expect(offer).toBeDefined();

    await act(async () => { offer.click(); await Promise.resolve(); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'elsewhere' });
    expect(toolbarButton(host, 'New · #13 → Show')).toBeUndefined();
  });
});
