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
  runExportBundle: vi.fn(),
  downloadText: vi.fn(),
  downloadBlob: vi.fn(),
}));

vi.mock('../api/designIo', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/designIo')>(),
  downloadText: mocks.downloadText,
  downloadBlob: mocks.downloadBlob,
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

  it('shows export immediately on every exportable collapsed run and keeps it separate from selection', () => {
    preferencesStore.update({ exportFormats: [] });
    const exportableOne = completeJob({ id: 'exportable-one', run_number: 1, label: 'exportable_one' });
    const exportableTwo = completeJob({ id: 'exportable-two', run_number: 2, label: 'exportable_two' });
    const running = completeJob({ id: 'running-one', run_number: 3, label: 'running_one', status: 'running' });
    const failed = completeJob({ id: 'failed-one', run_number: 4, label: 'failed_one', status: 'error' });
    publishJobs([exportableOne, exportableTwo, running, failed]);
    const selectSpy = vi.spyOn(compareSelection, 'setPrimary');

    act(() => { root.render(<JobsPanel/>); });

    const collapsed = [...host.querySelectorAll<HTMLElement>('.job-card.collapsed')];
    expect(collapsed).toHaveLength(2);
    expect(collapsed.every((card) => card.querySelector('.run-export-control.compact'))).toBe(true);
    expect(host.querySelectorAll('.run-export-control')).toHaveLength(2);

    const secondCard = collapsed.find((card) => card.querySelector('[aria-label="Select #2 · exportable_two"]'))!;
    act(() => { secondCard.querySelector<HTMLButtonElement>('.action-menu-primary')!.click(); });

    expect(selectSpy).not.toHaveBeenCalled();
    expect(compareSelection.getSnapshot().primary).toBeNull();
    expect(document.querySelector('[role="menu"]')).not.toBeNull();
  });

  it('fetches results once for a result item and exports with the job design snapshot', async () => {
    const job = completeJob();
    render(job);
    openMenu();
    await act(async () => { menuItem('Frequency data').click(); await Promise.resolve(); });

    expect(mocks.fetchJobResults).toHaveBeenCalledOnce();
    expect(mocks.fetchJobResults).toHaveBeenCalledWith(job.id);
    expect(mocks.runExportBundle).toHaveBeenCalledOnce();
    const [context, formats] = mocks.runExportBundle.mock.calls[0];
    expect(formats).toEqual(['csv']);
    expect(context.result).toEqual(await mocks.fetchJobResults.mock.results[0].value);
    expect(context.design).toEqual(hydrateJobDesign(job));
    expect(context.designRevision).toBe(42);
    expect(context.preferences.outputName).toBe('stored_horn_v07');
    expect(patchMetadata).toHaveBeenCalledWith(job.id, { exported_files: ['earlier.csv', 'stored_horn_v07_1.csv'] });
  });

  it('keeps design-only exports after result retention and disables result formats', async () => {
    const job = completeJob({
      has_results: false,
      results_discarded_at: '2026-08-11T00:00:00Z',
    });
    render(job);
    openMenu();

    expect(menuItem('Frequency data').disabled).toBe(true);
    expect(menuItem('STEP solid').disabled).toBe(false);
    await act(async () => { menuItem('STEP solid').click(); await settle(); });

    expect(mocks.fetchJobResults).not.toHaveBeenCalled();
    expect(mocks.runExportBundle).toHaveBeenCalledWith(
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
    mocks.runExportBundle.mockResolvedValue({
      files: ['stored_horn_v07_1-drive-hf.csv', 'stored_horn_v07_1-drive-mf.csv'], failures: [],
    });
    render();
    openMenu();
    await act(async () => { menuItem('Frequency data').click(); await settle(); });

    expect(mocks.runExportBundle).toHaveBeenCalledWith(
      expect.objectContaining({ result: wrapped }),
      ['csv'],
    );
    expect(patchMetadata).toHaveBeenCalledWith('job-export-1', {
      exported_files: ['earlier.csv', 'stored_horn_v07_1-drive-hf.csv', 'stored_horn_v07_1-drive-mf.csv'],
    });
  });

  it('downloads exactly one on-axis FRD file', async () => {
    mocks.fetchJobResults.mockResolvedValue(directivityResult());
    render();
    openMenu();
    await act(async () => { menuItem('On-axis response').click(); await settle(); });

    expect(mocks.fetchJobResults).toHaveBeenCalledOnce();
    expect(mocks.downloadText).toHaveBeenCalledOnce();
    expect(mocks.downloadText.mock.calls[0][1]).toBe('stored_horn_v07_1.frd');
    expect(patchMetadata).toHaveBeenCalledWith('job-export-1', {
      exported_files: ['earlier.csv', 'stored_horn_v07_1.frd'],
    });
  });

  it('downloads one on-axis FRD per drive channel', async () => {
    mocks.fetchJobResults.mockResolvedValue({
      frequencies: [], channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-hf': directivityResult(), 'drive-mf': directivityResult() },
    });
    render();
    openMenu();
    await act(async () => { menuItem('On-axis response').click(); await settle(); });

    expect(mocks.downloadText.mock.calls.map(([, filename]) => filename)).toEqual([
      'stored_horn_v07_1-drive-hf.frd', 'stored_horn_v07_1-drive-mf.frd',
    ]);
  });

  it('posts the complete polar set to the workspace and reports its count', async () => {
    mocks.fetchJobResults.mockResolvedValue(directivityResult());
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      if (path === '/api/workspace/path') {
        return new Response(JSON.stringify({ selected: true, path: '/chosen' }), { status: 200 });
      }
      if (path === '/api/workspace/write-export') {
        return new Response(JSON.stringify({
          directory: '/chosen/stored_horn_v07_1',
          files: Array.from({ length: 6 }, (_, index) => `/chosen/stored_horn_v07_1/${index}.frd`),
        }), { status: 200 });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(completeJob({ polar_grid: { angle_step: 5 } }));
    openMenu();
    await act(async () => { menuItem('Polar set (VituixCAD)').click(); await settle(); });

    const writeCall = fetchMock.mock.calls.find(([input]) => String(input) === '/api/workspace/write-export');
    expect(writeCall).toBeDefined();
    const body = JSON.parse(String(writeCall![1]?.body));
    expect(body.subdirectory).toBe('stored_horn_v07_1');
    expect(body.members).toHaveLength(6);
    expect(body.members.map((member: { relative_path: string }) => member.relative_path)).toEqual([
      'hor/stored_horn_v07_1 -30.frd', 'hor/stored_horn_v07_1 0.frd', 'hor/stored_horn_v07_1 30.frd',
      'ver/stored_horn_v07_1 -20.frd', 'ver/stored_horn_v07_1 0.frd', 'ver/stored_horn_v07_1 20.frd',
    ]);
    expect(document.querySelector('[role="status"]')?.textContent).toContain('6 polar FRD files written');
    expect(document.querySelector('[role="status"]')?.textContent).toContain('/chosen/stored_horn_v07_1');
  });

  it('asks for a workspace selection and writes nothing when it is cancelled', async () => {
    mocks.fetchJobResults.mockResolvedValue(directivityResult());
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      if (path === '/api/workspace/path') {
        return new Response(JSON.stringify({ selected: false, path: '/default' }), { status: 200 });
      }
      if (path === '/api/workspace/select') {
        return new Response(JSON.stringify({ selected: false, path: '/default' }), { status: 200 });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(completeJob({ polar_grid: { angle_step: 5 } }));
    openMenu();
    await act(async () => { menuItem('Polar set (VituixCAD)').click(); await settle(); });

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/workspace/path', '/api/workspace/select',
    ]);
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('cancelled');
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('No files were written');
    expect(patchMetadata).not.toHaveBeenCalled();
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
    mocks.runExportFormat.mockImplementationOnce(async () => {
      await fetch('/api/render-charts', { method: 'POST' });
      return ['stored_horn_v07_1_frequency_response.png'];
    });
    render();
    openMenu();
    await act(async () => { menuItem('Charts').click(); await settle(); });

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/render-directivity', '/api/render-charts',
    ]);
    expect(mocks.downloadBlob).toHaveBeenCalledOnce();
    expect(mocks.downloadBlob.mock.calls[0][1]).toBe('stored_horn_v07_1_directivity_map.png');
    expect(document.querySelector('[role="status"]')?.textContent).toContain('2 files');
  });

  it('surfaces polar write failures without claiming success', async () => {
    mocks.fetchJobResults.mockResolvedValue(directivityResult());
    vi.mocked(globalThis.fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === '/api/workspace/path') {
        return new Response(JSON.stringify({ selected: true, path: '/chosen' }), { status: 200 });
      }
      return new Response(JSON.stringify({ detail: 'disk is full' }), { status: 507 });
    });
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
    const pending = deferred<{ files: string[]; failures: [] }>();
    mocks.runExportBundle.mockReturnValue(pending.promise);
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
