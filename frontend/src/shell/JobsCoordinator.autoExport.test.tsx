import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import type { ExportContext } from '../results/exporters';
import { JobsCoordinator } from './JobsCoordinator';

const mocks = vi.hoisted(() => ({
  fetchJobResults: vi.fn(),
  runWorkspaceExportBundle: vi.fn(),
  saveMeshArtifactToWorkspace: vi.fn(),
}));

vi.mock('../api/results', () => ({ fetchJobResults: mocks.fetchJobResults }));
vi.mock('../results/exporters', () => ({
  saveMeshArtifactToWorkspace: mocks.saveMeshArtifactToWorkspace,
  runWorkspaceExportBundle: mocks.runWorkspaceExportBundle,
}));
vi.mock('../jobs/useCapabilities', () => ({
  useCapabilities: () => ({
    engines: [],
    engineSelection: { default: 'auto', resolvedDefault: null, full3dOrder: [] },
    error: null,
    isLoading: false,
  }),
  useCapabilityRefreshOnReconnect: () => undefined,
  useLegacyBeatEngineMigration: () => undefined,
}));
vi.mock('../jobs/useImportedSolvePlan', () => ({
  useImportedSolvePlan: () => ({ plan: null, error: null, isPending: false }),
}));
vi.mock('../jobs/useSolvePlan', () => ({
  useSolvePlan: () => ({ plan: null, error: null, isPending: true }),
}));

function job(id: string, label: string, runNumber: number): JobItem {
  return {
    id, run_number: runNumber, parent_job_id: null,
    label, status: 'complete', progress: 1, stage: null,
    stage_message: null, created_at: '2026-08-08T00:00:00Z',
    queued_at: '2026-08-08T00:00:00Z', started_at: null,
    completed_at: '2026-08-08T00:00:01Z', config_summary: {}, solve_options: {} as JobItem['solve_options'],
    has_results: true, has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null, script_snapshot: null,
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

describe('completed-job auto-export naming', () => {
  let host: HTMLDivElement;
  let root: Root;
  let fetchMock: ReturnType<typeof vi.fn>;
  const finishes = () => fetchMock.mock.calls.filter(([path]) => (path as string).endsWith('/finish')).map(([path, init]) => ({ jobId: (path as string).split('/')[4], body: JSON.parse((init as RequestInit).body as string) }));

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    preferencesStore.resetForTests();
    preferencesStore.update({
      autoExportOnComplete: true,
      archiveRunsOnComplete: false,
      exportFormats: ['png'],
      autoExportFormats: ['csv'],
      runSequenceName: 'horn',
      runSequenceNext: 7,
    });
    fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      const body = path.endsWith('/claim') ? {
        claimed: true, token: path.split('/')[3], formats: JSON.parse(init!.body as string).formats,
        job: jobsSocket.getSnapshot().jobs.find((item) => item.id === path.split('/')[3]),
      } : { status: 'ok' };
      return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    mocks.fetchJobResults.mockResolvedValue({ frequencies: [100] });
    mocks.runWorkspaceExportBundle.mockImplementation(async (context: ExportContext) => ({
      files: [`${context.jobStem}.csv`],
      failures: [],
    }));
    vi.spyOn(jobsSocket, 'start').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'stop').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'patchMetadata').mockResolvedValue(undefined);
    publishJobs([
      job('job-one', '260808_horn_v01', 101),
      job('job-two', '260808_horn_v02', 102),
    ]);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it('uses the completed job identity for each bundle and its persisted filenames', async () => {
    await act(async () => { root.render(<JobsCoordinator><span>ready</span></JobsCoordinator>); });
    await act(async () => {
      await vi.waitFor(() => expect(mocks.runWorkspaceExportBundle).toHaveBeenCalledTimes(2));
      await vi.waitFor(() => expect(finishes()).toHaveLength(2));
    });

    const baseNames = mocks.runWorkspaceExportBundle.mock.calls.map(([context]) =>
      (context as ExportContext).jobStem);
    expect(baseNames).toEqual(['101_260808_horn_v01', '102_260808_horn_v02']);

    expect(finishes().map(({ body }) => body.files)).toEqual([['101_260808_horn_v01.csv'], ['102_260808_horn_v02.csv']]);
    expect(jobsSocket.patchMetadata).not.toHaveBeenCalled();
    // Exporting finished runs is not a submission, so the run counter stands.
    expect(preferencesStore.getSnapshot().runSequenceNext).toBe(7);
  });

  it('hands the multi-channel wrapper to auto-export and persists every channel file', async () => {
    const wrapped = {
      frequencies: [], channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-hf': { frequencies: [100] }, 'drive-mf': { frequencies: [100] } },
    };
    mocks.fetchJobResults.mockResolvedValue(wrapped);
    mocks.runWorkspaceExportBundle.mockResolvedValue({
      files: ['103_260808_horn_v03-drive-hf.csv', '103_260808_horn_v03-drive-mf.csv'],
      failures: [],
    });
    publishJobs([job('job-three', '260808_horn_v03', 103)]);

    await act(async () => { root.render(<JobsCoordinator><span>ready</span></JobsCoordinator>); });
    await act(async () => {
      await vi.waitFor(() => expect(finishes()).toHaveLength(1));
    });

    expect(mocks.runWorkspaceExportBundle).toHaveBeenCalledWith(
      expect.objectContaining({ result: wrapped }),
      ['csv'],
    );
    expect(finishes()[0].body.files).toEqual(['103_260808_horn_v03-drive-hf.csv', '103_260808_horn_v03-drive-mf.csv']);
    expect(jobsSocket.patchMetadata).not.toHaveBeenCalled();
  });
});
