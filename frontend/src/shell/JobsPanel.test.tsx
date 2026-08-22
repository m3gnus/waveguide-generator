import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { preferencesStore } from '../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { jobCardPropsEqual, JobsPanel, selectJob, type JobCardProps } from './JobsPanel';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import { currentJobLabel } from './JobsCoordinator';

const designMocks = vi.hoisted(() => ({ replaceWithJobDesign: vi.fn() }));
vi.mock('../jobs/jobDesign', () => ({
  canLoadJobDesign: () => true,
  hydrateJobDesign: () => ({ formula: 'OSSE' }),
  jobDesignAvailability: () => ({ reopenable: true, source: 'v2-snapshot', reason_code: 'ok', reason: null, note: null }),
  jobRerunState: () => ({ enabled: true, reason: null }),
  replaceWithJobDesign: designMocks.replaceWithJobDesign,
}));

function job(runNumber: number, label: string | null, formula = 'OSSE', rating = 0): JobItem {
  return {
    id: `${runNumber.toString(16).padStart(6, '0')}abcdef`, run_number: runNumber, parent_job_id: null,
    label, rating, status: 'complete', progress: 1, stage: null, stage_message: null,
    created_at: '2026-08-08T00:00:00Z', queued_at: '2026-08-08T00:00:00Z', started_at: '2026-08-08T00:00:00Z', completed_at: '2026-08-08T00:00:01Z',
    config_summary: { formula_type: formula }, solve_options: {} as JobItem['solve_options'], has_results: true,
    has_mesh_artifact: false, error_message: null, cancellation_requested: false, mesh_stats: null,
    script_snapshot: {}, design_revision: 1, polar_grid: {}, exported_files: [], auto_export_completed_at: null,
    auto_export_formats: {}, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

function enter(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
}

function cadBundle(documentName: string): CadReturnBundle {
  return {
    name: `${documentName}.wgreturn`, bundlePath: `/cad/${documentName}.wgreturn`,
    modifiedAt: '2026-08-19T12:00:00Z', readable: true, documentName,
    requestId: null, sourceCount: 1, instanceCount: 1, sources: [],
  };
}

function readyCadRecord(ingestId: string): CadReturnIngestRecord {
  return {
    ingest_id: ingestId, manifest_sha256: `manifest:${ingestId}`, artifact_sha256: `artifact:${ingestId}`, report_sha256: `report:${ingestId}`,
    findings: [], evidence: { fem_air_volumes: [] }, polar_grid_derivation: {},
  } as unknown as CadReturnIngestRecord;
}

describe('jobs panel run list', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    preferencesStore.resetForTests();
    resetCadReturnStore();
    resetDesignStore();
    resetDocumentStore();
    resetSolveOptionsStore();
    importedMeshStore.clear();
    compareSelection.setPrimary(null);
    workspaceModeStore.setMode('parametric');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    act(() => publishJobs([]));
    vi.restoreAllMocks();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
  });

  it('limits one-second clock invalidation to active run cards', () => {
    const completed = job(1, 'Finished');
    const stable = vi.fn();
    const props: JobCardProps = {
      job: completed,
      now: 1_000,
      selected: false,
      retryJob: async () => undefined,
      onError: stable,
      onRemove: stable,
      onOpenExportSettings: stable,
    };
    expect(jobCardPropsEqual(props, { ...props, now: 2_000 })).toBe(true);

    const running = { ...completed, status: 'running' as const };
    const runningProps = { ...props, job: running };
    expect(jobCardPropsEqual(runningProps, { ...runningProps, now: 2_000 })).toBe(false);
    expect(jobCardPropsEqual(props, { ...props, job: { ...completed } })).toBe(false);
  });

  it('commits rename on Enter and blur, preserves Unicode drafts, and reverts on Escape', async () => {
    const first = job(123, 'Shared title');
    const second = job(124, 'Shared title');
    publishJobs([first, second]);
    compareSelection.setPrimary(first.id);
    const patch = vi.spyOn(jobsSocket, 'patchMetadata').mockResolvedValue(undefined);
    await act(async () => root.render(<JobsPanel/>));
    expect(host.textContent).toContain('#123 · Shared title');
    expect(host.textContent).toContain('#124 · Shared title');
    expect(host.querySelectorAll('.job-rename')).toHaveLength(1);

    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Rename #123 · Shared title"]')!.click());
    let input = host.querySelector<HTMLInputElement>('[aria-label="Title for run #123"]')!;
    act(() => enter(input, '  Þröstur – horn  '));
    expect(input.value).toBe('  Þröstur – horn  ');
    await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect(patch).toHaveBeenLastCalledWith(first.id, { label: '  Þröstur – horn  ' });

    act(() => compareSelection.setPrimary(second.id));
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Rename #124 · Shared title"]')!.click());
    input = host.querySelector<HTMLInputElement>('[aria-label="Title for run #124"]')!;
    act(() => enter(input, 'Blur title'));
    await act(async () => input.blur());
    expect(patch).toHaveBeenLastCalledWith(second.id, { label: 'Blur title' });

    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Rename #124 · Blur title"]')!.click());
    input = host.querySelector<HTMLInputElement>('[aria-label="Title for run #124"]')!;
    act(() => enter(input, 'Discard me'));
    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(patch).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain('#124 · Blur title');
  });

  it('marks imported jobs without presenting a formula name', async () => {
    const imported = job(12, 'Returned speaker', 'cad-import');
    imported.config_summary = { geometry_type: 'imported', ingest_id: 'wgi_example' };
    publishJobs([imported]);
    await act(async () => root.render(<JobsPanel/>));
    expect(host.textContent).toContain('CAD import');
    expect(host.textContent).not.toContain('cad-import');
  });

  it('recalls a CAD run ingestion into the CAD workspace and viewport', async () => {
    const imported = job(12, 'Returned speaker', 'cad-import');
    imported.config_summary = { geometry_type: 'imported' };
    imported.cad_source = {
      ingest_id: 'wgi_archived', design_id: 'wgd_archived', lineage_id: 'wgl_archived',
      archive_stem: 'returned-speaker', manifest_sha256: 'sha256:manifest',
      document_name: 'Returned speaker', return_state_hash: null,
    };
    const record = {
      ingest_id: 'wgi_archived', created_at: '2026-08-08T00:00:00Z', return_id: 'return-1',
      manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact', report_sha256: 'sha256:report',
      acoustic_domain: 'free-space', scope: { status: 'clean', degraded_skip_count: 0 },
      sources: [{ id: 'source-hf', role: 'HF', required: true, instance_id: null, default_drive_channel_id: 'drive-hf', suggested_resolution_mm: 2 }],
      mesh_sizes: { rigid_size_mm: 4, transition_mm: 3, source_size_mm: { 'source-hf': 2 } },
      skipped_source_ids: [], freshness: { verdict: 'per-instance', instances: [] }, findings: [],
      symmetry: { cut_planes: [], planes: {} }, healing: {}, sizing_estimate: {}, polar_grid_derivation: {}, tag_map: {},
    } satisfies CadReturnIngestRecord;
    const mesh = ['$MeshFormat', '2.2 0 8', '$EndMeshFormat', '$Nodes', '3', '1 0 0 0', '2 1 0 0', '3 0 1 0', '$EndNodes', '$Elements', '1', '1 2 2 1 1 1 2 3', '$EndElements', ''].join('\n');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/viewport-mesh')
      ? new Response(mesh, { status: 200 })
      : new Response(JSON.stringify(record), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    publishJobs([imported]);
    await act(async () => root.render(<JobsPanel/>));

    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="Select #12 · Returned speaker"]')!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_archived');
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_archived');
    expect(importedMeshStore.getSnapshot().cad?.name).toBe('Returned speaker');
  });

  it('reports when a selected CAD run no longer has archived ingestion artifacts', async () => {
    const imported = job(13, 'Missing speaker', 'cad-import');
    imported.config_summary = { geometry_type: 'imported' };
    imported.cad_source = {
      ingest_id: 'wgi_missing', design_id: null, lineage_id: null, archive_stem: null,
      manifest_sha256: null, document_name: 'Missing speaker', return_state_hash: null,
    };
    const reportStatus = vi.spyOn(cadLinkCoordinatorBridge.getSnapshot(), 'reportStatus');
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'Unknown ingestion record wgi_missing' }),
      { status: 404, headers: { 'Content-Type': 'application/json' } },
    )));
    publishJobs([imported]);
    await act(async () => root.render(<JobsPanel/>));

    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="Select #13 · Missing speaker"]')!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(reportStatus).toHaveBeenLastCalledWith(expect.stringContaining('archived CAD ingestion and mesh artifacts are no longer available'));
  });

  // Setting .value in a test replaces the whole string no matter where the
  // caret is, so the rename tests above pass either way. A real keystroke does
  // not: with the caret merely placed at the end, the first character typed
  // appends to the old title instead of replacing it.
  it('selects the existing title when rename opens, so typing replaces it', async () => {
    const selected = job(42, 'horn_v12');
    publishJobs([selected]);
    compareSelection.setPrimary(selected.id);
    await act(async () => root.render(<JobsPanel/>));

    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Rename #42 · horn_v12"]')!.click());
    const input = host.querySelector<HTMLInputElement>('[aria-label="Title for run #42"]')!;

    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe('horn_v12'.length);
  });

  it('shows a failed rename and restores the server title', async () => {
    const selected = job(7, 'Original');
    publishJobs([selected]);
    compareSelection.setPrimary(selected.id);
    vi.spyOn(jobsSocket, 'patchMetadata').mockRejectedValue(new Error('offline'));
    await act(async () => root.render(<JobsPanel/>));
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Rename #7 · Original"]')!.click());
    const input = host.querySelector<HTMLInputElement>('[aria-label="Title for run #7"]')!;
    act(() => enter(input, 'Stale draft'));
    await act(async () => input.blur());
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('offline');
    expect(host.textContent).toContain('#7 · Original');
    expect(host.textContent).not.toContain('Stale draft');
  });

  it('offers the radiation-impedance download only while the run still has one', async () => {
    const radiationLink = () => [...host.querySelectorAll<HTMLAnchorElement>('.job-card footer a')]
      .find((link) => link.textContent === 'Radiation Z');
    const plain = job(1, 'No campaign');
    const withArtifact = { ...job(2, 'Cardioid'), has_radiation_impedance_artifact: true };
    publishJobs([plain, withArtifact]);
    compareSelection.setPrimary(plain.id);
    await act(async () => root.render(<JobsPanel/>));
    expect(radiationLink()).toBeUndefined();

    act(() => compareSelection.setPrimary(withArtifact.id));
    expect(radiationLink()?.getAttribute('href')).toBe(`/api/radiation-impedance/${withArtifact.id}`);
    expect(radiationLink()?.hasAttribute('download')).toBe(true);
    expect(radiationLink()?.hasAttribute('target')).toBe(false);

    // Retention can clean the archive up under a run that once had one.
    act(() => publishJobs([plain, { ...withArtifact, has_radiation_impedance_artifact: false }]));
    expect(radiationLink()).toBeUndefined();
  });

  it('opens job logs in the in-app text dialog', async () => {
    const completed = job(8, 'Logged run');
    publishJobs([completed]);
    compareSelection.setPrimary(completed.id);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    const fetchMock = vi.fn(async () => new Response('solver output\nfinished\n'));
    vi.stubGlobal('fetch', fetchMock);
    await act(async () => root.render(<JobsPanel/>));

    const log = [...host.querySelectorAll<HTMLButtonElement>('.job-card footer button')]
      .find((button) => button.textContent === 'Log')!;
    await act(async () => {
      log.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(open).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/jobs/${completed.id}/log`,
      { signal: expect.any(AbortSignal) },
    );
    expect(document.querySelector('[role="dialog"] pre')?.textContent).toContain('solver output');
  });

  it('says why a passive-cardioid run pauses on its extra campaign stage', async () => {
    const running: JobItem = {
      ...job(9, 'Cardioid'),
      status: 'running', progress: 0.2, stage: 'radiation_impedance',
      stage_message: 'Solving passive-cardioid radiation impedance 32/160 at 400 Hz',
    };
    publishJobs([running]);
    await act(async () => root.render(<JobsPanel/>));
    expect(host.textContent).toContain('Solving passive-cardioid radiation impedance 32/160 at 400 Hz');
    expect(host.textContent).toContain('own radiation-impedance matrix');

    act(() => publishJobs([{ ...running, stage: 'solve', stage_message: 'Solving 4/24' }]));
    expect(host.textContent).not.toContain('own radiation-impedance matrix');
  });

  it('filters by title, numbered handle, bare number, and formula with distinct empty states', async () => {
    publishJobs([job(123, 'Tritonia', 'OSSE'), job(456, 'Other', 'Le Cleac’h')]);
    await act(async () => root.render(<JobsPanel/>));
    const filter = host.querySelector<HTMLInputElement>('[aria-label="Filter runs"]')!;
    for (const query of ['trit', '#123', '123', 'osse']) {
      act(() => enter(filter, query));
      expect(host.textContent).toContain('#123 · Tritonia');
      expect(host.textContent).not.toContain('#456 · Other');
    }
    act(() => enter(filter, 'missing'));
    expect(host.textContent).toContain('No runs match the filter');
    expect(host.textContent).toContain('Clear the search');
    act(() => publishJobs([]));
    expect(host.textContent).toContain('No runs yet');
    expect(host.textContent).not.toContain('No runs match the filter');
  });

  it('toggles the kept-only list and restores all runs', async () => {
    publishJobs([job(1, 'Ordinary'), job(2, 'Kept', 'OSSE', 3)]);
    await act(async () => root.render(<JobsPanel/>));
    const toggle = host.querySelector<HTMLButtonElement>('[aria-label="Show kept runs only"]')!;
    act(() => toggle.click());
    expect(host.textContent).toContain('#2 · Kept');
    expect(host.textContent).not.toContain('#1 · Ordinary');
    act(() => toggle.click());
    expect(host.textContent).toContain('#1 · Ordinary');
    expect(host.textContent).toContain('#2 · Kept');
  });

  it('shows export controls only on the selected run', async () => {
    const first = job(1, 'First');
    const second = job(2, 'Second');
    publishJobs([first, second]);
    compareSelection.setPrimary(first.id);
    await act(async () => root.render(<JobsPanel/>));

    let exports = host.querySelectorAll<HTMLButtonElement>('button[aria-label^="More export options for"]');
    expect(exports).toHaveLength(1);
    expect(exports[0].ariaLabel).toBe('More export options for First');

    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Select #2 · Second"]')!.click());
    exports = host.querySelectorAll<HTMLButtonElement>('button[aria-label^="More export options for"]');
    expect(exports).toHaveLength(1);
    expect(exports[0].ariaLabel).toBe('More export options for Second');
  });

  it('identifies the run history and keeps current-row and button state aligned', async () => {
    const first = job(1, 'First');
    const second = job(2, 'Second');
    publishJobs([first, second]);
    compareSelection.setPrimary(first.id);
    await act(async () => root.render(<JobsPanel/>));

    const list = host.querySelector<HTMLElement>('[role="list"][aria-label="Run history"]')!;
    const items = [...list.querySelectorAll<HTMLElement>('[role="listitem"]')];
    const firstButton = list.querySelector<HTMLButtonElement>('[aria-label="Select #1 · First"]')!;
    const secondButton = list.querySelector<HTMLButtonElement>('[aria-label="Select #2 · Second"]')!;
    const firstItem = firstButton.closest<HTMLElement>('[role="listitem"]')!;
    const secondItem = secondButton.closest<HTMLElement>('[role="listitem"]')!;
    expect(items).toHaveLength(2);
    expect([firstItem.getAttribute('aria-current'), secondItem.getAttribute('aria-current')]).toEqual(['true', null]);
    expect([firstButton.getAttribute('aria-pressed'), secondButton.getAttribute('aria-pressed')]).toEqual(['true', 'false']);

    act(() => secondButton.click());

    expect([firstItem.getAttribute('aria-current'), secondItem.getAttribute('aria-current')]).toEqual([null, 'true']);
    expect([firstButton.getAttribute('aria-pressed'), secondButton.getAttribute('aria-pressed')]).toEqual(['false', 'true']);
  });

  it('restores the configured kept threshold and never hides active runs', async () => {
    preferencesStore.update({ minRating: 4 });
    const active = { ...job(3, 'Active'), status: 'running' as const, rating: 0 };
    publishJobs([job(1, 'Low', 'OSSE', 2), job(2, 'High', 'OSSE', 5), active]);
    await act(async () => root.render(<JobsPanel/>));
    const toggle = host.querySelector<HTMLButtonElement>('[aria-label="Show kept runs only"]')!;

    expect(host.textContent).toContain('#2 · High');
    expect(host.textContent).toContain('#3 · Active');
    expect(host.textContent).not.toContain('#1 · Low');
    act(() => toggle.click());
    expect(preferencesStore.getSnapshot().minRating).toBe(0);
    act(() => toggle.click());
    expect(preferencesStore.getSnapshot().minRating).toBe(4);
  });

  it('confirms the global failed count including failures hidden by search', async () => {
    const visible = { ...job(1, 'Visible'), status: 'error' as const };
    const hidden = { ...job(2, 'Hidden'), status: 'error' as const };
    publishJobs([visible, hidden]);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const clear = vi.spyOn(jobsSocket, 'clearFailed').mockResolvedValue(undefined);
    await act(async () => root.render(<JobsPanel/>));
    const filter = host.querySelector<HTMLInputElement>('[aria-label="Filter runs"]')!;
    act(() => enter(filter, 'Visible'));

    const clearFailed = host.querySelector<HTMLButtonElement>('.panel-text-action--danger')!;
    expect(clearFailed.closest('.run-name-field')).not.toBeNull();
    expect(host.querySelector('.panel-meta .panel-text-action--danger')).toBeNull();
    await act(async () => clearFailed.click());

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Remove all 2 failed runs'));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('1 failed run hidden'));
    expect(clear).toHaveBeenCalledOnce();
  });

  it('presents cancelled runs separately and only shows retention copy with provenance', async () => {
    const cancelled = { ...job(3, 'Stopped'), status: 'cancelled' as const, has_results: false };
    publishJobs([cancelled]);
    await act(async () => root.render(<JobsPanel/>));

    const card = host.querySelector('.job-card')!;
    expect(card.classList.contains('cancelled')).toBe(true);
    expect(card.textContent).toContain('Cancelled.');
    expect(card.textContent).toContain('cancelled after');
    expect(card.textContent).not.toContain('cleaned up');

    act(() => publishJobs([{ ...job(4, 'Pruned'), has_results: false, results_discarded_at: '2026-08-11T00:00:00Z' }]));
    expect(host.textContent).toContain('Results were cleaned up to save space.');
  });

  it('does not rename the design when an older run is selected', () => {
    act(() => useDocumentStore.getState().setDesignName('next-design'));
    selectJob(job(9, '260808_old-design_v03'));
    expect(useDocumentStore.getState().designName).toBe('next-design');
    expect(designMocks.replaceWithJobDesign).toHaveBeenCalledOnce();
  });

  it('renames the whole document from the one name field', async () => {
    publishJobs([]);
    await act(async () => root.render(<JobsPanel/>));
    const input = host.querySelector<HTMLInputElement>('[aria-label="Design name"]')!;
    act(() => input.focus());
    act(() => enter(input, 'winner'));
    act(() => input.blur());

    expect(host.querySelector('.run-name-preview')?.textContent).toContain('next · winner1');
    expect(input.title).toContain('Download a copy');
    expect(input.title).not.toContain('saves as');
    // The Download a copy filename follows the same edit -- this is the whole
    // point of the field.
    expect(useDocumentStore.getState()).toMatchObject({ designName: 'winner', filename: 'winner.cfg' });

    // Editing the geometry does not rename anything any more.
    act(() => useDesignStore.getState().updateField('R', 141));
    expect(host.querySelector('.run-name-preview')?.textContent).toContain('next · winner1');
  });

  it('reports the CAD document name in CAD mode instead of offering an edit', async () => {
    // The name of a CAD Link run belongs to the Fusion document, and WG cannot
    // write a name back into Fusion -- so the field states it rather than
    // pretending to own it.
    useCadReturnStore.setState({ selectedBundle: cadBundle('Tritonia V') });
    workspaceModeStore.setMode('cad');
    act(() => useDocumentStore.getState().setDesignName('a leftover design'));
    publishJobs([]);
    await act(async () => root.render(<JobsPanel/>));

    expect(host.querySelector<HTMLInputElement>('[aria-label="Design name"]')).toBeNull();
    const input = host.querySelector<HTMLInputElement>('[aria-label="CAD document name"]')!;
    expect(input.value).toBe('Tritonia V');
    expect(input.readOnly).toBe(true);
    expect(input.title).toContain('Rename the document in Fusion 360');
    // Renaming in Fusion and sending again is what changes it.
    act(() => useCadReturnStore.setState({ selectedBundle: cadBundle('Tritonia V2') }));
    expect(host.querySelector<HTMLInputElement>('[aria-label="CAD document name"]')!.value).toBe('Tritonia V2');
  });

  it('previews the Fusion document name in CAD mode, advancing only with the counter', async () => {
    useCadReturnStore.setState({
      selectedBundle: cadBundle('cad-run'),
      ingestRecord: readyCadRecord('wgi_first'), needsIngest: false,
      driveChannels: [{ id: 'drive', source_ids: ['source'], motion: 'normal' }],
      sourceSizesMm: { source: 2 }, rigidSizeMm: 5, transitionMm: 5,
    });
    workspaceModeStore.setMode('cad');
    act(() => useDocumentStore.getState().setDesignName('a leftover design'));
    publishJobs([]);
    await act(async () => root.render(<JobsPanel/>));

    expect(host.querySelector('.run-name-preview')?.textContent).toContain('next · cad-run1');
    act(() => useDesignStore.getState().updateField('R', 141));
    expect(host.querySelector('.run-name-preview')?.textContent).toContain('next · cad-run1');
    act(() => useCadReturnStore.setState({ ingestRecord: readyCadRecord('wgi_second') }));
    expect(host.querySelector('.run-name-preview')?.textContent).toContain('next · cad-run1');
  });

  it('shows the exact dated label that submission will use while editing only the name', async () => {
    const now = new Date(2026, 7, 12, 12);
    preferencesStore.update({ runNameDatePosition: 'prefix' });
    act(() => useDocumentStore.getState().setDesignName('horn'));
    publishJobs([]);
    await act(async () => root.render(<JobsPanel namingNow={now}/>));

    expect(host.querySelector<HTMLInputElement>('[aria-label="Design name"]')?.value).toBe('horn');
    expect(host.querySelector('.run-name-preview')?.textContent)
      .toBe(`next · ${currentJobLabel(undefined, now)}`);
  });
});
