import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { seedDesign, serializeDesign } from '../stores/design';
import { provideExportDestinationPrompt } from './exportDestinationPrompt';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { ResultsPanel } from './ResultsPanel';

const rerun = vi.hoisted(() => ({ rerunWithPatch: vi.fn() }));
vi.mock('../jobs/rerun', () => ({ rerunWithPatch: rerun.rerunWithPatch }));

function job(id: string, imported = false): JobItem {
  return {
    id, run_number: 1, parent_job_id: null,
    label: id, status: 'complete', progress: 1, stage: null,
    stage_message: null, created_at: '2026-08-08T00:00:00Z',
    queued_at: '2026-08-08T00:00:00Z', started_at: null,
    completed_at: '2026-08-08T00:00:01Z', config_summary: {}, solve_options: {} as JobItem['solve_options'],
    has_results: true, has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null,
    script_snapshot: { version: 1, design: serializeDesign(seedDesign) },
    design_revision: 1, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [],
    cad_source: imported ? {
      ingest_id: 'i', design_id: 'd', lineage_id: 'l', archive_stem: id,
      manifest_sha256: 'sha256:x', document_name: 'doc', return_state_hash: null,
    } : null,
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

function response(frequencies: number[], ok = true): Response {
  const digest = 'a'.repeat(64);
  const result = {
    result_kind: 'parametric',
    result_contract_version: 1,
    client_request_id: null,
    client_metadata: {},
    provenance: {
      schema_version: 1,
      wg_version: 'test',
      dependency_shas: {},
      request_sha256: digest,
      geometry_sha256: digest,
      solve_options_sha256: digest,
      request_identity: 'execution',
      execution_request_sha256: digest,
      execution_geometry_sha256: digest,
      execution_solve_options_sha256: digest,
      effective_request_sha256: digest,
      effective_geometry_sha256: digest,
      effective_solve_options_sha256: digest,
      resolved_engine: 'test',
    },
    frequencies,
    metadata: {},
  };
  return new Response(JSON.stringify(ok ? result : { detail: 'result vanished' }), {
    status: ok ? 200 : 500,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('Enable & rerun wiring', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resultsCache.clear();
    provisionalResults.clear();
    compareSelection.clear();
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['beam_shape'] });
    provideExportDestinationPrompt(async () => ({ token: 't', directory: '/exports' }));
    rerun.rerunWithPatch.mockReset().mockResolvedValue('job-new');
    (jobsCoordinatorBridge.getSnapshot() as unknown as { runExclusive: unknown }).runExclusive =
      (submit: () => Promise<unknown>) => submit();
    vi.stubGlobal('fetch', vi.fn(async () => response([100])));
    publishJobs([job('parametric-run'), job('cad-run', true)]);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    provideExportDestinationPrompt(null);
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    compareSelection.clear();
    resultsCache.clear();
    provisionalResults.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const button = () => [...host.querySelectorAll('button')].find((item) => item.textContent === 'Enable & rerun');

  it('reruns the selected run with spherical sampling switched on', async () => {
    compareSelection.setPrimary('parametric-run');
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { await Promise.resolve(); });
    expect(button()).toBeTruthy();
    await act(async () => { button()!.click(); });
    expect(rerun.rerunWithPatch).toHaveBeenCalledWith(
      'parametric-run', { options: { polar_config: { spherical_sampling: true } } },
    );
  });

  it('does not offer the rerun on an imported run', async () => {
    compareSelection.setPrimary('cad-run');
    await act(async () => { root.render(<ResultsPanel/>); });
    await act(async () => { await Promise.resolve(); });
    expect(button()).toBeUndefined();
  });
});
