import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { compareSelection } from '../api/results';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { JobsCoordinator, jobsCoordinatorBridge } from './JobsCoordinator';

const mocks = vi.hoisted(() => ({ showJobModel: vi.fn(async () => true) }));

vi.mock('../jobs/showJobModel', async (importOriginal) => ({
  ...await importOriginal<typeof import('../jobs/showJobModel')>(),
  showJobModel: mocks.showJobModel,
}));
vi.mock('../jobs/useCapabilities', () => ({
  useCapabilities: () => ({ capabilityGeneration: 1, refreshCapabilities: async () => undefined, engines: [], engineSelection: { default: 'auto', resolvedDefault: null, full3dOrder: [] }, error: null, isLoading: false }),
  useCapabilityRefreshOnReconnect: () => undefined,
  useLegacyBeatEngineMigration: () => undefined,
}));
vi.mock('../jobs/useSolvePlan', () => ({ useSolvePlan: () => ({ plan: null, error: null, isPending: false }) }));
vi.mock('../jobs/useImportedSolvePlan', () => ({ useImportedSolvePlan: () => ({ plan: null, error: null, isPending: false }) }));

function publish(jobs: JobItem[], cursor: number | null = 1): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

function job(id: string, status: JobItem['status'], hasResults: boolean): JobItem {
  return {
    id, label: id, status, has_results: hasResults, progress: 0.5,
    created_at: '2026-10-11T00:00:00Z', queued_at: '2026-10-11T00:00:00Z',
    config_summary: { geometry_type: 'imported' }, solve_options: {}, log_tail: [],
    exported_files: [], auto_export_formats: {},
  } as unknown as JobItem;
}

describe('?job= deep link', () => {
  let host: HTMLDivElement;
  let root: Root;
  const mount = async (search: string) => {
    window.history.replaceState(null, '', `/${search}#frag`);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    await act(async () => { root.render(<JobsCoordinator><span>ready</span></JobsCoordinator>); });
  };

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    compareSelection.clear();
    mocks.showJobModel.mockClear();
    vi.spyOn(jobsSocket, 'start').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'stop').mockImplementation(() => undefined);
    publish([], null);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publish([], null);
    compareSelection.clear();
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
  });

  it('opens a completed run as a click on its card does, once the list loads', async () => {
    await mount('?job=done-1&keep=1');
    expect(compareSelection.getSnapshot().primary).toBeNull();
    expect(window.location.search).toBe('?job=done-1&keep=1');
    await act(async () => { publish([job('other', 'complete', true), job('done-1', 'complete', true)]); });
    expect(compareSelection.getSnapshot().primary).toBe('done-1');
    expect(mocks.showJobModel).toHaveBeenCalledWith(expect.objectContaining({ id: 'done-1' }), expect.any(Function));
    expect(window.location.search).toBe('?keep=1');
    expect(window.location.hash).toBe('#frag');
  });

  it('selects a run that is still running and claims its results', async () => {
    publish([job('live-1', 'running', false)]);
    await mount('?job=live-1');
    expect(mocks.showJobModel).toHaveBeenCalledWith(expect.objectContaining({ id: 'live-1' }), expect.any(Function));
    expect(compareSelection.getSnapshot().awaiting).toBe('live-1');
    expect(window.location.search).toBe('');
  });

  it('says so, and selects nothing, for an unknown id', async () => {
    publish([job('failed-1', 'error', false)]);
    await mount('?job=nope');
    expect(compareSelection.getSnapshot().primary).toBeNull();
    expect(mocks.showJobModel).not.toHaveBeenCalled();
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe('No run with id nope in this WG data folder.');
    expect(window.location.search).toBe('');
  });

  it('drops an empty job parameter at once and opens nothing', async () => {
    await mount('?job=&keep=1');
    expect(window.location.search).toBe('?keep=1');
    await act(async () => { publish([job('done-1', 'complete', true)]); });
    expect(compareSelection.getSnapshot().primary).toBeNull();
  });

  it('does nothing without the parameter and does not re-trigger after being handled', async () => {
    await mount('');
    await act(async () => { publish([job('done-1', 'complete', true)]); });
    expect(compareSelection.getSnapshot().primary).toBeNull();
    window.history.replaceState(null, '', '/?job=done-1');
    await act(async () => { publish([job('done-1', 'complete', true), job('b', 'complete', true)]); });
    expect(compareSelection.getSnapshot().primary).toBeNull();
  });
});
