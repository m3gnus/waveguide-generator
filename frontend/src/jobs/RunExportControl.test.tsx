import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { jobsSocket } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import { hydrateJobDesign } from './jobDesign';
import { designForFamily, serializeDesign } from '../stores/design';
import { useRunExportStore } from '../stores/runExports';
import { RunExportControl } from './RunExportControl';

const mocks = vi.hoisted(() => ({
  fetchJobResults: vi.fn(),
  runExportFormat: vi.fn(),
  runExportBundle: vi.fn(),
}));

vi.mock('../api/results', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/results')>(),
  fetchJobResults: mocks.fetchJobResults,
}));

vi.mock('../results/exporters', () => ({
  runExportFormat: mocks.runExportFormat,
  runExportBundle: mocks.runExportBundle,
}));

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

function completeJob(overrides: Partial<JobItem> = {}): JobItem {
  const design = designForFamily('OSSE');
  return {
    id: 'job-export-1', status: 'complete', progress: 1, stage: 'complete', stage_message: null,
    created_at: '2026-08-10T10:00:00Z', queued_at: '2026-08-10T10:00:00Z',
    started_at: '2026-08-10T10:00:01Z', completed_at: '2026-08-10T10:01:00Z',
    config_summary: { formula_type: 'OSSE' }, has_results: true, has_mesh_artifact: true,
    label: 'stored_horn_v07', error_message: null, cancellation_requested: false, mesh_stats: null,
    script_snapshot: { version: 1, design: serializeDesign(design) }, design_revision: 42,
    polar_grid: {}, rating: null, exported_files: ['earlier.csv'], auto_export_completed_at: null,
    auto_export_formats: {}, raw_results_file: 'result.json', mesh_artifact_file: null, log_tail: [],
    ...overrides,
  };
}

function menuItem(label: string): HTMLButtonElement {
  const item = [...document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
    .find((button) => button.textContent?.includes(label));
  if (!item) throw new Error(`Missing menu item: ${label}`);
  return item;
}

describe('RunExportControl', () => {
  let host: HTMLDivElement;
  let root: Root;
  let patchMetadata: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    preferencesStore.resetForTests();
    useRunExportStore.getState().resetForTests();
    mocks.fetchJobResults.mockResolvedValue({ frequencies: [1000], spl_on_axis: { spl: [90] } });
    mocks.runExportFormat.mockResolvedValue(['stored_horn_v07_1.csv']);
    mocks.runExportBundle.mockResolvedValue({ files: ['stored_horn_v07_1.csv'], failures: [] });
    patchMetadata = vi.spyOn(jobsSocket, 'patchMetadata').mockResolvedValue(undefined);
    vi.stubGlobal('fetch', vi.fn());
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    document.querySelectorAll('.action-menu-popover, .design-menu-status').forEach((element) => element.remove());
    useRunExportStore.getState().resetForTests();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  const render = (job = completeJob(), onOpenExportSettings = vi.fn()) => act(() => {
    root.render(<RunExportControl job={job} onOpenExportSettings={onOpenExportSettings}/>);
  });

  const openMenu = () => act(() => {
    host.querySelector<HTMLButtonElement>('.action-menu-chevron')!.click();
  });

  it('does no network work or result fetch on hover', () => {
    vi.useFakeTimers();
    render();
    const control = host.querySelector('.action-menu')!;
    act(() => {
      control.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, pointerType: 'mouse' }));
      vi.advanceTimersByTime(180);
    });

    expect(document.querySelector('[role="menu"]')).not.toBeNull();
    expect(mocks.fetchJobResults).not.toHaveBeenCalled();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('fetches results once for a result item and exports with the job design snapshot', async () => {
    const job = completeJob();
    render(job);
    openMenu();
    await act(async () => { menuItem('Frequency data').click(); await Promise.resolve(); });

    expect(mocks.fetchJobResults).toHaveBeenCalledOnce();
    expect(mocks.fetchJobResults).toHaveBeenCalledWith(job.id);
    expect(mocks.runExportFormat).toHaveBeenCalledOnce();
    const [format, context] = mocks.runExportFormat.mock.calls[0];
    expect(format).toBe('csv');
    expect(context.result).toEqual(await mocks.fetchJobResults.mock.results[0].value);
    expect(context.design).toEqual(hydrateJobDesign(job));
    expect(context.designRevision).toBe(42);
    expect(context.preferences.outputName).toBe('stored_horn_v07');
    expect(patchMetadata).toHaveBeenCalledWith(job.id, { exported_files: ['earlier.csv', 'stored_horn_v07_1.csv'] });
  });

  it('opens the menu instead of exporting when preferred formats are empty', () => {
    preferencesStore.update({ exportFormats: [] });
    render();
    act(() => { host.querySelector<HTMLButtonElement>('.action-menu-primary')!.click(); });

    expect(document.querySelector('[role="menu"]')).not.toBeNull();
    expect(mocks.runExportBundle).not.toHaveBeenCalled();
    expect(mocks.fetchJobResults).not.toHaveBeenCalled();
  });

  it('runs the configured preferred-format bundle from the primary action', async () => {
    preferencesStore.update({ exportFormats: ['csv', 'step'] });
    render();
    await act(async () => { host.querySelector<HTMLButtonElement>('.action-menu-primary')!.click(); await Promise.resolve(); });

    expect(mocks.fetchJobResults).toHaveBeenCalledOnce();
    expect(mocks.runExportBundle).toHaveBeenCalledOnce();
    expect(mocks.runExportBundle.mock.calls[0][1]).toEqual(['csv', 'step']);
    expect(mocks.runExportBundle.mock.calls[0][0].design).toEqual(hydrateJobDesign(completeJob()));
  });

  it('disables design formats with the stored reason while leaving result formats enabled', () => {
    const job = completeJob({ script_snapshot: null }) as JobItem & { design_availability: unknown };
    job.design_availability = {
      reopenable: false, source: 'none', reason_code: 'no_stored_design',
      reason: 'This historical run has no stored design.', note: null,
    };
    render(job);
    openMenu();

    expect(menuItem('STEP solid').getAttribute('aria-disabled')).toBe('true');
    expect(menuItem('STEP solid').textContent).toContain('no stored design');
    expect(menuItem('Parameter config').getAttribute('aria-disabled')).toBe('true');
    expect(menuItem('Frequency data').getAttribute('aria-disabled')).toBeNull();
  });

  it('reports partial bundle success with the failed format and reason', async () => {
    preferencesStore.update({ exportFormats: ['csv', 'step'] });
    mocks.runExportBundle.mockResolvedValue({
      files: ['stored_horn_v07_1.csv'],
      failures: [{ format: 'step', reason: 'geometry service unavailable' }],
    });
    render();
    await act(async () => { host.querySelector<HTMLButtonElement>('.action-menu-primary')!.click(); await Promise.resolve(); });

    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('1 file exported');
    expect(alert?.textContent).toContain('STEP solid (.step)');
    expect(alert?.textContent).toContain('geometry service unavailable');
  });

  it('keeps an in-flight export in the store after the control unmounts', async () => {
    const pending = deferred<string[]>();
    mocks.runExportFormat.mockReturnValue(pending.promise);
    const job = completeJob();
    render(job);
    openMenu();
    await act(async () => { menuItem('Frequency data').click(); await Promise.resolve(); });
    expect(useRunExportStore.getState().jobs[job.id]).toMatchObject({ busy: true, busyFormats: ['csv'] });

    act(() => root.unmount());
    expect(useRunExportStore.getState().jobs[job.id]).toMatchObject({ busy: true, busyFormats: ['csv'] });

    await act(async () => { pending.resolve(['after-unmount.csv']); await pending.promise; await Promise.resolve(); });
    expect(useRunExportStore.getState().jobs[job.id]).toMatchObject({ busy: false, busyFormats: [] });
    expect(patchMetadata).toHaveBeenCalledWith(job.id, { exported_files: ['earlier.csv', 'after-unmount.csv'] });
    root = createRoot(host);
  });

  it.each(['running', 'error'] as const)('does not render for a %s job', (status) => {
    render(completeJob({ status }));
    expect(host.querySelector('.run-export-control')).toBeNull();
  });
});
