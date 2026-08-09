import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { exportBaseName, preferencesStore } from '../prefs/preferences';
import type { ExportContext } from '../results/exporters';
import { JobsCoordinator } from './JobsCoordinator';

const mocks = vi.hoisted(() => ({
  fetchJobResults: vi.fn(),
  runExportBundle: vi.fn(),
  downloadMeshArtifact: vi.fn(),
}));

vi.mock('../api/results', () => ({ fetchJobResults: mocks.fetchJobResults }));
vi.mock('../results/exporters', () => ({
  downloadMeshArtifact: mocks.downloadMeshArtifact,
  runExportBundle: mocks.runExportBundle,
}));
vi.mock('../jobs/useCapabilities', () => ({
  useCapabilities: () => ({ engines: [], error: null, isLoading: false }),
  useCapabilityRefreshOnReconnect: () => undefined,
}));

function job(id: string, label: string): JobItem {
  return {
    id, run_number: 1, parent_job_id: null,
    label, status: 'complete', progress: 1, stage: null,
    stage_message: null, created_at: '2026-08-08T00:00:00Z',
    queued_at: '2026-08-08T00:00:00Z', started_at: null,
    completed_at: '2026-08-08T00:00:01Z', config_summary: {},
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

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    preferencesStore.resetForTests();
    preferencesStore.update({
      autoExportOnComplete: true,
      exportFormats: ['csv'],
      outputName: 'horn',
      counter: 7,
    });
    mocks.fetchJobResults.mockResolvedValue({ frequencies: [100] });
    mocks.runExportBundle.mockImplementation(async (context: ExportContext) => ({
      files: [`${exportBaseName(context.preferences)}.csv`],
      failures: [],
    }));
    vi.spyOn(jobsSocket, 'start').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'stop').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'patchMetadata').mockResolvedValue(undefined);
    publishJobs([
      job('job-one', '260808_horn_v01'),
      job('job-two', '260808_horn_v02'),
    ]);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it('uses the completed job identity for each bundle and its persisted filenames', async () => {
    await act(async () => { root.render(<JobsCoordinator><span>ready</span></JobsCoordinator>); });
    await act(async () => {
      await vi.waitFor(() => expect(mocks.runExportBundle).toHaveBeenCalledTimes(2));
      await vi.waitFor(() => expect(jobsSocket.patchMetadata).toHaveBeenCalledTimes(2));
    });

    const baseNames = mocks.runExportBundle.mock.calls.map(([context]) =>
      exportBaseName((context as ExportContext).preferences));
    expect(baseNames).toEqual(['260808_horn_v01_7', '260808_horn_v02_7']);

    expect(jobsSocket.patchMetadata).toHaveBeenCalledWith('job-one', expect.objectContaining({
      exported_files: ['260808_horn_v01_7.csv'],
    }));
    expect(jobsSocket.patchMetadata).toHaveBeenCalledWith('job-two', expect.objectContaining({
      exported_files: ['260808_horn_v02_7.csv'],
    }));
    expect(preferencesStore.getSnapshot().counter).toBe(9);
  });
});
