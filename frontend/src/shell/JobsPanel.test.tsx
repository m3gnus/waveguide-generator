import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { preferencesStore } from '../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { jobCardPropsEqual, JobsPanel, selectJob, type JobCardProps } from './JobsPanel';
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
    // The file the design saves as follows the same edit -- this is the whole
    // point of the field.
    expect(useDocumentStore.getState()).toMatchObject({ designName: 'winner', filename: 'winner.cfg' });

    // Editing the geometry does not rename anything any more.
    act(() => useDesignStore.getState().updateField('R', 141));
    expect(host.querySelector('.run-name-preview')?.textContent).toContain('next · winner1');
  });

  it('keeps the name typed in CAD mode when an ingest arrives', async () => {
    workspaceModeStore.setMode('cad');
    publishJobs([]);
    await act(async () => root.render(<JobsPanel/>));
    const input = host.querySelector<HTMLInputElement>('[aria-label="Design name"]')!;
    act(() => input.focus());
    act(() => enter(input, 'named-before-ingest'));
    act(() => input.blur());

    act(() => useCadReturnStore.setState({
      ingestRecord: readyCadRecord('wgi_first'), needsIngest: false,
      driveChannels: [{ id: 'drive', source_ids: ['source'], motion: 'normal' }],
      sourceSizesMm: { source: 2 }, rigidSizeMm: 5, transitionMm: 5,
    }));

    expect(host.querySelector('.run-name-preview')?.textContent).toContain('next · named-before-ingest1');
  });

  it('previews the same design name in CAD mode, advancing only with the counter', async () => {
    useCadReturnStore.setState({
      ingestRecord: readyCadRecord('wgi_first'), needsIngest: false,
      driveChannels: [{ id: 'drive', source_ids: ['source'], motion: 'normal' }],
      sourceSizesMm: { source: 2 }, rigidSizeMm: 5, transitionMm: 5,
    });
    workspaceModeStore.setMode('cad');
    act(() => useDocumentStore.getState().setDesignName('cad-run'));
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
