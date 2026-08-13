import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobItem, JobsSnapshot } from '../api/jobsSocket';
import { jobsSocket } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { hydrateJobDesign } from './jobDesign';
import { designForFamily, serializeDesign } from '../stores/design';
import { useRunExportStore } from '../stores/runExports';
import { RunExportControl } from './RunExportControl';
import { JobsPanel } from '../shell/JobsPanel';

const mocks = vi.hoisted(() => ({
  fetchJobResults: vi.fn(),
  runExportFormat: vi.fn(),
  runWorkspaceExportBundle: vi.fn(),
  writeWorkspaceFiles: vi.fn(),
}));

vi.mock('../api/results', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/results')>(),
  fetchJobResults: mocks.fetchJobResults,
}));

vi.mock('../results/exporters', () => ({
  runExportFormat: mocks.runExportFormat,
  runWorkspaceExportBundle: mocks.runWorkspaceExportBundle,
  writeWorkspaceFiles: mocks.writeWorkspaceFiles,
}));

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

function completeJob(overrides: Partial<JobItem> = {}): JobItem {
  const design = designForFamily('OSSE');
  return {
    id: 'job-export-1', run_number: 1, parent_job_id: null,
    status: 'complete', progress: 1, stage: 'complete', stage_message: null,
    created_at: '2026-08-10T10:00:00Z', queued_at: '2026-08-10T10:00:00Z',
    started_at: '2026-08-10T10:00:01Z', completed_at: '2026-08-10T10:01:00Z',
    config_summary: { formula_type: 'OSSE' }, solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: true,
    label: 'stored_horn_v07', error_message: null, cancellation_requested: false, mesh_stats: null,
    script_snapshot: { version: 1, design: serializeDesign(design) }, design_revision: 42,
    polar_grid: {}, rating: null, exported_files: ['earlier.csv'], auto_export_completed_at: null,
    auto_export_formats: {}, raw_results_file: 'result.json', mesh_artifact_file: null, log_tail: [],
    ...overrides,
  };
}

function directivityResult() {
  return {
    frequencies: [1000],
    spl_on_axis: { frequencies: [1000], spl: [90], phase_degrees: [5] },
    directivity: {
      horizontal: [[[-30, -6], [0, 0], [30, -6]]],
      vertical: [[[-20, -5], [0, 0], [20, -5]]],
    },
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as {
    snapshot: JobsSnapshot;
    listeners: Set<() => void>;
  };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

async function settle() {
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
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
    compareSelection.clear();
    publishJobs([]);
    useRunExportStore.getState().resetForTests();
    mocks.fetchJobResults.mockResolvedValue({ frequencies: [1000], spl_on_axis: { spl: [90] } });
    mocks.runExportFormat.mockResolvedValue(['1_stored_horn_v07.csv']);
    mocks.runWorkspaceExportBundle.mockResolvedValue({
      directory: 'C:/chosen/1_stored_horn_v07',
      files: ['C:/chosen/1_stored_horn_v07/1_stored_horn_v07.csv'],
      failures: [],
    });
    mocks.writeWorkspaceFiles.mockResolvedValue({
      directory: 'C:/chosen/1_stored_horn_v07',
      files: ['C:/chosen/1_stored_horn_v07/1_stored_horn_v07.frd'],
    });
    patchMetadata = vi.spyOn(jobsSocket, 'patchMetadata').mockResolvedValue(undefined);
    vi.stubGlobal('fetch', vi.fn());
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    compareSelection.clear();
    publishJobs([]);
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

  it('shows export only on the selected run and keeps export separate from selection', () => {
    preferencesStore.update({ exportFormats: [] });
    const exportableOne = completeJob({ id: 'exportable-one', run_number: 1, label: 'exportable_one' });
    const exportableTwo = completeJob({ id: 'exportable-two', run_number: 2, label: 'exportable_two' });
    const running = completeJob({ id: 'running-one', run_number: 3, label: 'running_one', status: 'running' });
    const failed = completeJob({ id: 'failed-one', run_number: 4, label: 'failed_one', status: 'error' });
    publishJobs([exportableOne, exportableTwo, running, failed]);
    compareSelection.setPrimary(exportableOne.id);
    const selectSpy = vi.spyOn(compareSelection, 'setPrimary');

    act(() => { root.render(<JobsPanel/>); });

    const selected = host.querySelector<HTMLElement>('.job-card.selected')!;
    const collapsed = [...host.querySelectorAll<HTMLElement>('.job-card.collapsed')];
    expect(selected.querySelector('[aria-label="Select #1 · exportable_one"]')).not.toBeNull();
    expect(selected.querySelector('.run-export-control')).not.toBeNull();
    expect(collapsed).toHaveLength(1);
    expect(collapsed[0].querySelector('[aria-label="Select #2 · exportable_two"]')).not.toBeNull();
    expect(collapsed[0].querySelector('.run-export-control')).toBeNull();
    expect(host.querySelectorAll('.run-export-control')).toHaveLength(1);

    act(() => { selected.querySelector<HTMLButtonElement>('.action-menu-primary')!.click(); });

    expect(selectSpy).not.toHaveBeenCalled();
    expect(compareSelection.getSnapshot().primary).toBe(exportableOne.id);
    expect(document.querySelector('[role="menu"]')).not.toBeNull();
  });

  it('fetches results once for a result item and exports with the job design snapshot', async () => {
    const job = completeJob();
    render(job);
    openMenu();
    await act(async () => { menuItem('Frequency data').click(); await Promise.resolve(); });

    expect(mocks.fetchJobResults).toHaveBeenCalledOnce();
    expect(mocks.fetchJobResults).toHaveBeenCalledWith(job.id);
    expect(mocks.runWorkspaceExportBundle).toHaveBeenCalledOnce();
    const [context, formats] = mocks.runWorkspaceExportBundle.mock.calls[0];
    expect(formats).toEqual(['csv']);
    expect(context.result).toEqual(await mocks.fetchJobResults.mock.results[0].value);
    expect(context.design).toEqual(hydrateJobDesign(job));
    expect(context.designRevision).toBe(42);
    expect(context.jobStem).toBe('1_stored_horn_v07');
    expect(context.preferences.outputName).toBe('horn');
    expect(patchMetadata).toHaveBeenCalledWith(job.id, {
      exported_files: ['earlier.csv', 'C:/chosen/1_stored_horn_v07/1_stored_horn_v07.csv'],
    });
  });

  it('keeps design-only exports after result retention and disables result formats', async () => {
    const job = completeJob({
      has_results: false,
      results_discarded_at: '2026-08-11T00:00:00Z',
    });
    render(job);
    openMenu();

    // ActionMenu marks unavailable items with aria-disabled, never the native
    // attribute, so the reason stays focusable and announced.
    expect(menuItem('Frequency data').getAttribute('aria-disabled')).toBe('true');
    expect(menuItem('Frequency data').textContent).toContain('removed by retention');
    expect(menuItem('STEP solid').getAttribute('aria-disabled')).toBeNull();
    await act(async () => { menuItem('STEP solid').click(); await settle(); });

    expect(mocks.fetchJobResults).not.toHaveBeenCalled();
    expect(mocks.runWorkspaceExportBundle).toHaveBeenCalledWith(
      expect.objectContaining({ design: hydrateJobDesign(job), designRevision: 42 }),
      ['step'],
    );
  });

  it('passes a multi-channel result to the bundle writer from a rail action', async () => {
    const wrapped = {
      frequencies: [], channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-hf': directivityResult(), 'drive-mf': directivityResult() },
    };
    mocks.fetchJobResults.mockResolvedValue(wrapped);
    mocks.runWorkspaceExportBundle.mockResolvedValue({
      directory: 'C:/chosen/1_stored_horn_v07',
      files: [
        'C:/chosen/1_stored_horn_v07/1_stored_horn_v07-drive-hf.csv',
        'C:/chosen/1_stored_horn_v07/1_stored_horn_v07-drive-mf.csv',
      ],
      failures: [],
    });
    render();
    openMenu();
    await act(async () => { menuItem('Frequency data').click(); await settle(); });

    expect(mocks.runWorkspaceExportBundle).toHaveBeenCalledWith(
      expect.objectContaining({ result: wrapped }),
      ['csv'],
    );
    expect(patchMetadata).toHaveBeenCalledWith('job-export-1', {
      exported_files: [
        'earlier.csv',
        'C:/chosen/1_stored_horn_v07/1_stored_horn_v07-drive-hf.csv',
        'C:/chosen/1_stored_horn_v07/1_stored_horn_v07-drive-mf.csv',
      ],
    });
  });

  it('writes exactly one on-axis FRD file to the Workspace', async () => {
    mocks.fetchJobResults.mockResolvedValue(directivityResult());
    render();
    openMenu();
    await act(async () => { menuItem('On-axis response').click(); await settle(); });

    expect(mocks.fetchJobResults).toHaveBeenCalledOnce();
    expect(mocks.writeWorkspaceFiles).toHaveBeenCalledOnce();
    const [subdirectory, members] = mocks.writeWorkspaceFiles.mock.calls[0];
    expect(subdirectory).toBe('1_stored_horn_v07');
    expect(members.map(({ filename }: { filename: string }) => filename)).toEqual(['1_stored_horn_v07.frd']);
    expect(await members[0].blob.text()).toContain('1000');
    expect(patchMetadata).toHaveBeenCalledWith('job-export-1', {
      exported_files: ['earlier.csv', 'C:/chosen/1_stored_horn_v07/1_stored_horn_v07.frd'],
    });
  });

  it('writes one on-axis FRD per drive channel', async () => {
    mocks.fetchJobResults.mockResolvedValue({
      frequencies: [], channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-hf': directivityResult(), 'drive-mf': directivityResult() },
    });
    mocks.writeWorkspaceFiles.mockResolvedValueOnce({
      directory: 'C:/chosen/1_stored_horn_v07',
      files: [
        'C:/chosen/1_stored_horn_v07/1_stored_horn_v07-drive-hf.frd',
        'C:/chosen/1_stored_horn_v07/1_stored_horn_v07-drive-mf.frd',
      ],
    });
    render();
    openMenu();
    await act(async () => { menuItem('On-axis response').click(); await settle(); });

    expect(mocks.writeWorkspaceFiles.mock.calls[0][1].map(({ filename }: { filename: string }) => filename)).toEqual([
      '1_stored_horn_v07-drive-hf.frd', '1_stored_horn_v07-drive-mf.frd',
    ]);
  });

  it('posts the complete polar set to the workspace and reports its count', async () => {
    mocks.fetchJobResults.mockResolvedValue(directivityResult());
    mocks.writeWorkspaceFiles.mockResolvedValueOnce({
      directory: 'C:/chosen/1_stored_horn_v07',
      files: Array.from({ length: 6 }, (_, index) => `C:/chosen/1_stored_horn_v07/${index}.frd`),
    });
    render(completeJob({ polar_grid: { angle_step: 5 } }));
    openMenu();
    await act(async () => { menuItem('Polar set (VituixCAD)').click(); await settle(); });

    expect(mocks.writeWorkspaceFiles).toHaveBeenCalledOnce();
    const [subdirectory, members] = mocks.writeWorkspaceFiles.mock.calls[0];
    expect(subdirectory).toBe('1_stored_horn_v07');
    expect(members).toHaveLength(6);
    expect(members.map((member: { filename: string }) => member.filename)).toEqual([
      'hor/1_stored_horn_v07 -30.frd', 'hor/1_stored_horn_v07 0.frd', 'hor/1_stored_horn_v07 30.frd',
      'ver/1_stored_horn_v07 -20.frd', 'ver/1_stored_horn_v07 0.frd', 'ver/1_stored_horn_v07 20.frd',
    ]);
    expect(document.querySelector('[role="status"]')?.textContent).toContain('6 polar FRD files written');
    expect(document.querySelector('[role="status"]')?.textContent).toContain('C:/chosen/1_stored_horn_v07');
  });

  it('uses the configured default Workspace without opening a picker', async () => {
    mocks.fetchJobResults.mockResolvedValue(directivityResult());
    mocks.writeWorkspaceFiles.mockResolvedValueOnce({
      directory: 'C:/default/output/1_stored_horn_v07',
      files: Array.from({ length: 6 }, (_, index) => `C:/default/output/1_stored_horn_v07/${index}.frd`),
    });
    render(completeJob({ polar_grid: { angle_step: 5 } }));
    openMenu();
    await act(async () => { menuItem('Polar set (VituixCAD)').click(); await settle(); });

    expect(mocks.writeWorkspaceFiles).toHaveBeenCalledOnce();
    expect(globalThis.fetch).not.toHaveBeenCalledWith('/api/workspace/select', expect.anything());
    expect(document.querySelector('[role="status"]')?.textContent).toContain('C:/default/output/1_stored_horn_v07');
  });

  it('disables the polar set with a reason when the run has no directivity data', () => {
    render(completeJob({ polar_grid: {} }));
    openMenu();

    const item = menuItem('Polar set (VituixCAD)');
    expect(item.getAttribute('aria-disabled')).toBe('true');
    expect(item.textContent).toContain('no directivity data');
  });

  it('calls both canonical render endpoints for Charts', async () => {
    mocks.fetchJobResults.mockResolvedValue(directivityResult());
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      if (path === '/api/render-directivity') {
        return new Response(JSON.stringify({ image: 'data:image/png;base64,Ag==' }), { status: 200 });
      }
      if (path === '/api/render-charts') {
        return new Response(JSON.stringify({ charts: { frequency_response: 'data:image/png;base64,AQ==' } }), { status: 200 });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    mocks.runExportFormat.mockImplementationOnce(async (_format, context) => {
      await fetch('/api/render-charts', { method: 'POST' });
      context.saveBlob?.(new Blob(['chart']), '1_stored_horn_v07_frequency_response.png');
      return ['1_stored_horn_v07_frequency_response.png'];
    });
    mocks.writeWorkspaceFiles.mockResolvedValueOnce({
      directory: 'C:/chosen/1_stored_horn_v07',
      files: [
        'C:/chosen/1_stored_horn_v07/1_stored_horn_v07_frequency_response.png',
        'C:/chosen/1_stored_horn_v07/1_stored_horn_v07_directivity_map.png',
      ],
    });
    render();
    openMenu();
    await act(async () => { menuItem('Charts').click(); await settle(); });

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/render-directivity', '/api/render-charts',
    ]);
    expect(mocks.writeWorkspaceFiles.mock.calls[0][1].map(({ filename }: { filename: string }) => filename)).toEqual([
      '1_stored_horn_v07_frequency_response.png',
      '1_stored_horn_v07_directivity_map.png',
    ]);
    expect(document.querySelector('[role="status"]')?.textContent).toContain('2 files');
    expect(document.querySelector('[role="status"]')?.textContent).toContain('C:/chosen/1_stored_horn_v07');
  });

  it('surfaces polar write failures without claiming success', async () => {
    mocks.fetchJobResults.mockResolvedValue(directivityResult());
    mocks.writeWorkspaceFiles.mockRejectedValueOnce(new Error('disk is full'));
    render(completeJob({ polar_grid: { angle_step: 5 } }));
    openMenu();
    await act(async () => { menuItem('Polar set (VituixCAD)').click(); await settle(); });

    expect(document.querySelector('[role="alert"]')?.textContent).toContain('disk is full');
    expect(document.body.textContent).not.toContain('polar FRD files written');
    expect(patchMetadata).not.toHaveBeenCalled();
  });

  it('opens the menu instead of exporting when preferred formats are empty', () => {
    preferencesStore.update({ exportFormats: [] });
    render();
    act(() => { host.querySelector<HTMLButtonElement>('.action-menu-primary')!.click(); });

    expect(document.querySelector('[role="menu"]')).not.toBeNull();
    expect(mocks.runWorkspaceExportBundle).not.toHaveBeenCalled();
    expect(mocks.fetchJobResults).not.toHaveBeenCalled();
  });

  it('runs the configured preferred-format bundle from the primary action', async () => {
    preferencesStore.update({ exportFormats: ['csv', 'step'] });
    render();
    await act(async () => { host.querySelector<HTMLButtonElement>('.action-menu-primary')!.click(); await Promise.resolve(); });

    expect(mocks.fetchJobResults).toHaveBeenCalledOnce();
    expect(mocks.runWorkspaceExportBundle).toHaveBeenCalledOnce();
    expect(mocks.runWorkspaceExportBundle.mock.calls[0][1]).toEqual(['csv', 'step']);
    expect(mocks.runWorkspaceExportBundle.mock.calls[0][0].design).toEqual(hydrateJobDesign(completeJob()));
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
    mocks.runWorkspaceExportBundle.mockResolvedValue({
      directory: 'C:/chosen/1_stored_horn_v07',
      files: ['C:/chosen/1_stored_horn_v07/1_stored_horn_v07.csv'],
      failures: [{ format: 'step', reason: 'geometry service unavailable' }],
    });
    render();
    await act(async () => { host.querySelector<HTMLButtonElement>('.action-menu-primary')!.click(); await Promise.resolve(); });

    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('1 file written');
    expect(alert?.textContent).toContain('STEP solid (.step)');
    expect(alert?.textContent).toContain('geometry service unavailable');
  });

  it('keeps an in-flight export in the store after the control unmounts', async () => {
    const pending = deferred<{ files: string[]; failures: [] }>();
    mocks.runWorkspaceExportBundle.mockReturnValue(pending.promise);
    const job = completeJob();
    render(job);
    openMenu();
    await act(async () => { menuItem('Frequency data').click(); await Promise.resolve(); });
    expect(useRunExportStore.getState().jobs[job.id]).toMatchObject({ busy: true, busyFormats: ['csv'] });

    act(() => root.unmount());
    expect(useRunExportStore.getState().jobs[job.id]).toMatchObject({ busy: true, busyFormats: ['csv'] });

    await act(async () => { pending.resolve({ files: ['after-unmount.csv'], failures: [] }); await pending.promise; await Promise.resolve(); });
    expect(useRunExportStore.getState().jobs[job.id]).toMatchObject({ busy: false, busyFormats: [] });
    expect(patchMetadata).toHaveBeenCalledWith(job.id, { exported_files: ['earlier.csv', 'after-unmount.csv'] });
    root = createRoot(host);
  });

  it.each(['running', 'error'] as const)('does not render for a %s job', (status) => {
    render(completeJob({ status }));
    expect(host.querySelector('.run-export-control')).toBeNull();
  });
});
