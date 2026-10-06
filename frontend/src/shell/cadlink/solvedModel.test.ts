import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../../api/cadlink';
import type { JobItem } from '../../api/jobsSocket';
import { runContext, runContextMarker, runDisplayVerdict } from '../../results/runCoherence';
import { resetCadReturnStore, useCadReturnStore } from '../../stores/cadReturn';
import { useCadSolverFrameStore } from '../../stores/cadSolverFrame';
import { resetDocumentStore } from '../../stores/document';
import { resetSolveOptionsStore } from '../../stores/solveOptions';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { adoptSolvedCadModel, resetSolvedCadModelsForTests, solvedCadModels, type SolvedModelDisplay } from './solvedModel';

const MANIFEST = 'sha256:manifest-m1a';

const bundle: CadReturnBundle = {
  name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', modifiedAt: '2026-10-06T00:00:00Z', readable: true,
  documentName: 'Speaker', requestId: null, sourceCount: 1, instanceCount: 1,
  sources: [{ id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 3, defaultDriveChannelId: 'drive-hf' }],
};

/** One ingestion of the same return, meshed in the frame its solve model names. */
function ingestion(id: string, solveModel: string, manifest = MANIFEST): CadReturnIngestRecord {
  return {
    ingest_id: id, created_at: '', return_id: '', manifest_sha256: manifest, artifact_sha256: 'sha256:a', report_sha256: `sha256:${id}`,
    solve_model_sha256: solveModel,
    acoustic_domain: 'free-space', scope: { status: 'complete', degraded_skip_count: 0 }, sources: [],
    mesh_sizes: { rigid_size_mm: 8, transition_mm: 8, source_size_mm: { 'source-hf': 3 } }, skipped_source_ids: [],
    freshness: { verdict: 'per-instance', instances: [] }, findings: [],
    symmetry: {}, healing: {}, sizing_estimate: {}, polar_grid_derivation: {}, tag_map: {},
  } as unknown as CadReturnIngestRecord;
}

function run(id: string, prepared: CadReturnIngestRecord, axis = '-y'): JobItem {
  return {
    id,
    status: 'queued',
    config_summary: { geometry_type: 'imported' },
    cad_provenance: { frame: { axis, provenance: 'confirmed', confirmed: true, requirement: null } },
    design_revision: 0,
    script_snapshot: null,
    cad_source: {
      ingest_id: prepared.ingest_id,
      manifest_sha256: prepared.manifest_sha256,
      solve_model_sha256: prepared.solve_model_sha256,
      document_name: 'Speaker',
    },
  } as unknown as JobItem;
}

function serving(...records: CadReturnIngestRecord[]) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const id = decodeURIComponent(String(input).split('/').at(-1)!);
    const record = records.find((item) => item.ingest_id === id);
    return record
      ? new Response(JSON.stringify(record), { status: 200, headers: { 'Content-Type': 'application/json' } })
      : new Response(JSON.stringify({ detail: 'unknown ingestion' }), { status: 404, headers: { 'Content-Type': 'application/json' } });
  }) as unknown as typeof fetch;
}

const display: SolvedModelDisplay = {
  showIngestedMesh: async (record, name, _notice, _fetcher, generation) => {
    importedMeshStore.setCad({ source: 'cad', ingestId: record.ingest_id, name } as never, generation, true);
  },
};

/** The return as the UI ingested it on arrival: meshed as modelled, along +z. */
function onScreen(record: CadReturnIngestRecord): void {
  const store = useCadReturnStore.getState();
  store.selectBundle(bundle);
  expect(store.applyIngest(record, store.beginIngestIntent())).toBe(true);
  importedMeshStore.setCad({ source: 'cad', ingestId: record.ingest_id } as never);
}

describe('the model a CAD solve prepared', () => {
  // The M1a fixture: received along +z, solved along the automatic -y.
  const received = ingestion('wgi_received_YBY', 'sha256:39d46c-plus-z');
  const prepared = ingestion('wgi_prepared_5ES', 'sha256:f66928-minus-y');

  beforeEach(() => {
    localStorage.clear();
    resetSolveOptionsStore();
    resetDocumentStore();
    resetCadReturnStore();
    resetSolvedCadModelsForTests();
    importedMeshStore.clear();
    useCadSolverFrameStore.setState({ frames: {} });
    workspaceModeStore.setMode('cad');
  });

  it('puts the solved model on screen, so its run is the model in the viewport', async () => {
    onScreen(received);
    const solved = run('job-solved', prepared);
    // The defect: a solve of the model on screen, called another model.
    expect(runContextMarker(solved, runContext())).toBe('Different CAD return');

    await expect(adoptSolvedCadModel(solved, { manifestSha256: MANIFEST, sourceIngestId: received.ingest_id }, display, serving(prepared)))
      .resolves.toBe('adopted');

    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(prepared.ingest_id);
    expect(useCadReturnStore.getState().needsIngest).toBe(false);
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe(prepared.ingest_id);
    expect(runDisplayVerdict(solved, runContext())).toBe('current');
    expect(runContextMarker(solved, runContext())).toBeNull();
  });

  it('adopts once the claimed run names its preparation, and only once', async () => {
    onScreen(received);
    const fetcher = serving(prepared);
    solvedCadModels.claim('job-solved', { manifestSha256: MANIFEST, sourceIngestId: null });
    // Not listed yet: nothing to adopt, and the claim waits.
    solvedCadModels.settle([], display, fetcher);
    expect(fetcher).not.toHaveBeenCalled();

    solvedCadModels.settle([run('job-solved', prepared)], display, fetcher);
    solvedCadModels.settle([run('job-solved', prepared)], display, fetcher);
    await vi.waitFor(() => expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(prepared.ingest_id));
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('leaves a newer return on screen alone', async () => {
    onScreen(ingestion('wgi_newer', 'sha256:other', 'sha256:manifest-newer'));
    await expect(adoptSolvedCadModel(run('job-solved', prepared), { manifestSha256: MANIFEST, sourceIngestId: null }, display, serving(prepared)))
      .resolves.toBe('declined');
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_newer');
  });

  it('leaves the screen alone when WG solved another ingestion than the one shown', async () => {
    onScreen(received);
    await expect(adoptSolvedCadModel(run('job-solved', prepared), { manifestSha256: MANIFEST, sourceIngestId: 'wgi_earlier' }, display, serving(prepared)))
      .resolves.toBe('declined');
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(received.ingest_id);
  });

  it('keeps settings edited since the solve, and the run keeps its marker', async () => {
    onScreen(received);
    useCadReturnStore.getState().setSourceSize('source-hf', 2);
    const solved = run('job-solved', prepared);
    await expect(adoptSolvedCadModel(solved, { manifestSha256: MANIFEST, sourceIngestId: received.ingest_id }, display, serving(prepared)))
      .resolves.toBe('declined');
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(received.ingest_id);
    expect(useCadReturnStore.getState().sourceSizesMm['source-hf']).toBe(2);
    expect(runContextMarker(solved, runContext())).toBe('Different CAD return');
  });

  it('yields to an ingest the user started while the prepared record was read', async () => {
    onScreen(received);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const slow = (async (input: RequestInfo | URL, init?: RequestInit) => {
      await gate;
      return serving(prepared)(input, init);
    }) as typeof fetch;
    const adopting = adoptSolvedCadModel(run('job-solved', prepared), { manifestSha256: MANIFEST, sourceIngestId: null }, display, slow);
    // Rebuild mesh pressed meanwhile: its result must not be discarded.
    const rebuild = useCadReturnStore.getState().beginIngestIntent();
    release();

    await expect(adopting).resolves.toBe('declined');
    expect(useCadReturnStore.getState().isCurrentIngestIntent(rebuild)).toBe(true);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(received.ingest_id);
  });

  it('leaves a rebuild already in flight to finish', async () => {
    onScreen(received);
    // Rebuild mesh pressed after Solve, its response not back yet.
    const rebuild = useCadReturnStore.getState().beginIngestIntent();
    const fetcher = serving(prepared);
    await expect(adoptSolvedCadModel(run('job-solved', prepared), { manifestSha256: MANIFEST, sourceIngestId: null }, display, fetcher))
      .resolves.toBe('declined');
    expect(fetcher).not.toHaveBeenCalled();
    expect(useCadReturnStore.getState().isCurrentIngestIntent(rebuild)).toBe(true);
  });

  it('keeps an axis picked on the frame card after Solve', async () => {
    onScreen(received);
    useCadSolverFrameStore.setState({ frames: { [received.ingest_id]: {
      ingestId: received.ingest_id, status: 'ready', frame: null, linked: false,
      axis: '+x', picked: true, changedFrom: null, error: null,
    } } });
    await expect(adoptSolvedCadModel(run('job-solved', prepared, '-y'), { manifestSha256: MANIFEST, sourceIngestId: null }, display, serving(prepared)))
      .resolves.toBe('declined');
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(received.ingest_id);
    // The axis the run was solved along is no pick to protect.
    await expect(adoptSolvedCadModel(run('job-solved', prepared, '+x'), { manifestSha256: MANIFEST, sourceIngestId: null }, display, serving(prepared)))
      .resolves.toBe('adopted');
  });

  it('tries a dropped read again on a later jobs message, and a missing record never', async () => {
    onScreen(received);
    let failures = 1;
    const flaky = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (failures > 0) { failures -= 1; throw new TypeError('Failed to fetch'); }
      return serving(prepared)(input, init);
    }) as unknown as typeof fetch;
    solvedCadModels.claim('job-solved', { manifestSha256: MANIFEST, sourceIngestId: null });
    solvedCadModels.settle([run('job-solved', prepared)], display, flaky);
    // Each later jobs message settles again; the second read succeeds.
    await vi.waitFor(() => {
      solvedCadModels.settle([run('job-solved', prepared)], display, flaky);
      expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(prepared.ingest_id);
    });
    expect(flaky).toHaveBeenCalledTimes(2);

    const gone = serving();
    solvedCadModels.claim('job-gone', { manifestSha256: MANIFEST, sourceIngestId: null });
    const missing = run('job-gone', ingestion('wgi_gone', 'sha256:x'));
    solvedCadModels.settle([missing], display, gone);
    await vi.waitFor(() => expect(gone).toHaveBeenCalledOnce());
    for (let index = 0; index < 5; index += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      solvedCadModels.settle([missing], display, gone);
    }
    expect(gone).toHaveBeenCalledOnce();
  });

  it('does nothing when the solve prepared exactly the model shown', async () => {
    onScreen(prepared);
    const fetcher = serving(prepared);
    await expect(adoptSolvedCadModel(run('job-solved', prepared), { manifestSha256: MANIFEST, sourceIngestId: prepared.ingest_id }, display, fetcher))
      .resolves.toBe('already-shown');
    expect(fetcher).not.toHaveBeenCalled();
  });
});
