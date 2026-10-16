import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket } from '../api/jobsSocket';
import { resetCadCoordinationForTests } from '../api/cadCoordination';
import { Shell } from './Shell';
import { useCadSetupPublisherStore, useCadFeedbackError } from './cadSetupPublisher';
import { useCadConnectionStore } from '../stores/cadConnection';
import { cadReceiptsStore } from '../stores/cadReceipts';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { resetCadPreparationStore, useCadPreparationStore } from '../stores/cadPreparation';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { buildCadProjectSetup, startCadSetupPublisher } from './cadSetupPublisher';

const app = vi.hoisted(() => ({ coordinatorMounted: false }));
vi.mock('./CadWorkspaceOwners', async (original) => {
  const actual = await original<typeof import('./CadWorkspaceOwners')>();
  return { ...actual, CadWorkspaceOwners: () => app.coordinatorMounted ? createElement(actual.CadWorkspaceOwners) : null };
});
vi.mock('./JobsCoordinator', async (original) => ({
  ...await original<typeof import('./JobsCoordinator')>(), JobsCoordinator: () => null,
}));
vi.mock('./TopBar', () => ({ TopBar: () => null }));
vi.mock('./Workspace', () => ({ Workspace: () => null }));
vi.mock('./StatusBar', () => ({ StatusBar: () => null }));

const bundle: CadReturnBundle = {
  name: 'speaker.wgreturn',
  bundlePath: 'wgreturn/speaker.wgreturn',
  modifiedAt: '2026-09-14T10:00:00Z',
  readable: true,
  documentName: 'Speaker',
  requestId: null,
  sourceCount: 1,
  instanceCount: 1,
  designIds: [],
  sources: [{
    id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-hf',
  }],
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

function recorder() {
  const calls: Array<{ url: string; body: unknown }> = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null });
    return json({});
  }) as unknown as typeof fetch;
  const to = (suffix: string) => calls.filter(({ url }) => url.endsWith(suffix)).map(({ body }) => body);
  return { calls, fetcher, to };
}

function ingested(lineageId: string): CadReturnIngestRecord {
  return {
    ingest_id: `wgi_${lineageId}`, manifest_sha256: 'sha256:m', artifact_sha256: 'sha256:a', report_sha256: 'sha256:r',
    findings: [], polar_grid_derivation: {}, skipped_source_ids: [],
    mesh_sizes: { rigid_size_mm: 4, transition_mm: 4, source_size_mm: { 'source-hf': 4 } },
    project: { lineage_id: lineageId },
  } as unknown as CadReturnIngestRecord;
}

/** What an ingestion does to the store: it files the settings under the
 * project it states, and saves them as that project's profile. */
function ingest(lineageId: string): void {
  const store = useCadReturnStore.getState();
  expect(store.applyIngest(ingested(lineageId), store.beginIngestIntent())).toBe(true);
}

describe('CAD setup publisher', () => {
  beforeEach(() => {
    localStorage.clear();
    resetCadReturnStore();
    resetCadPreparationStore();
    resetDocumentStore();
    resetSolveOptionsStore();
    workspaceModeStore.setMode('cad');
  });
  afterEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    workspaceModeStore.setMode('parametric');
  });

  it('records which FEM regions an exterior-only choice excludes', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle, 'wgl_speaker');
    const record = { ...ingested('wgl_speaker'), evidence: { fem_regions: [{ region_id: 'air' }] } };
    store.applyIngest(record, store.beginIngestIntent());
    store.setExteriorOnly(true);
    const setup = buildCadProjectSetup();
    expect(setup?.setup.geometry.exterior_only_region_ids).toEqual(['air']);
    store.applyIngest({ ...record, evidence: { fem_regions: [{ region_id: 'second' }] } }, store.beginIngestIntent());
    expect(useCadReturnStore.getState().exteriorOnly).toBe(false);
  });

  it('builds the setup a solve of this project needs, without the snapshot and without widening its polar grid', () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_speaker');
    // A derivation that would widen the grid if the submission builder ran:
    // widening is the backend's, from the snapshot it prepares.
    useCadReturnStore.setState({
      ingestRecord: {
        ingest_id: 'wgi_1', manifest_sha256: 'sha256:m', artifact_sha256: 'sha256:a', report_sha256: 'sha256:r',
        findings: [], polar_grid_derivation: { axes: { horizontal: { minimum_deg: -180, maximum_deg: 180 } } },
      } as unknown as CadReturnIngestRecord,
    });
    useSolveOptionsStore.getState().setEngine('bempp');
    useCadPreparationStore.getState().setSymmetryMode('full');

    const built = buildCadProjectSetup();
    expect(built).not.toBeNull();
    expect(built!.lineageId).toBe('wgl_speaker');
    expect(built!.inventory).toEqual([{ id: 'source-hf', role: 'HF', required: true }]);
    const { setup } = built!;
    expect(setup.schema_version).toBe(1);
    for (const snapshotField of ['type', 'ingest_id', 'manifest_sha256', 'artifact_sha256', 'acknowledged_findings']) {
      expect(setup.geometry).not.toHaveProperty(snapshotField);
    }
    expect(setup.geometry).toMatchObject({
      mesh: { rigid_size_mm: 4, source_size_mm: { 'source-hf': 4 } },
      skipped_source_ids: [],
      exterior_only: false,
    });
    expect((setup.geometry.drive_channels as Array<{ source_ids: string[] }>)[0].source_ids).toEqual(['source-hf']);
    // The engine is the selector's, whatever the backend later finds capable.
    expect(setup.options.engine).toBe('bempp');
    expect(setup.options.frequency_range).toEqual([50, 20_000]);
    expect(setup.options.num_frequencies).toBe(36);
    expect(setup.options.polar_config).toEqual(useSolveOptionsStore.getState().options().polar_config);
    expect(setup.preparation).toEqual({ area_drift_overrides: [], symmetry_mode: 'full' });
  });

  it('records an edit of the project settings once, after the edits settle, and not a first-time selection', async () => {
    vi.useFakeTimers();
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    // A model this project has no settings for starts from defaults, which
    // nobody chose: the backend keeps asking for a setup instead.
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_speaker');
    await vi.advanceTimersByTimeAsync(2_000);
    expect(to('/project-setups')).toEqual([]);

    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(200);
    useCadReturnStore.getState().setExteriorOnly(false);
    await vi.advanceTimersByTimeAsync(200);
    useCadReturnStore.getState().setExteriorOnly(true);
    expect(to('/project-setups')).toEqual([]);
    await vi.advanceTimersByTimeAsync(600);
    const recorded = to('/project-setups') as Array<{ lineageId: string; inventory: unknown; setup: { geometry: { exterior_only: boolean } } }>;
    expect(recorded).toHaveLength(1);
    expect(recorded[0].lineageId).toBe('wgl_speaker');
    expect(recorded[0].inventory).toEqual([{ id: 'source-hf', role: 'HF', required: true }]);
    expect(recorded[0].setup.geometry.exterior_only).toBe(true);
    stop();
  });

  it('sends each source with exactly the role its return states', async () => {
    vi.useFakeTimers();
    const twoWay: CadReturnBundle = {
      ...bundle,
      sources: [
        { id: 'source-lf', role: 'LF', required: true, suggestedResolutionMm: 8, defaultDriveChannelId: 'drive-lf' },
        { id: 'source-hf', role: 'HF', required: false, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-hf' },
      ],
    };
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useCadReturnStore.getState().selectBundle(twoWay, 'wgl_speaker');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(600);
    const recorded = to('/project-setups') as Array<{ inventory: Array<{ id: string; role: string; required: boolean }> }>;
    expect(recorded).toHaveLength(1);
    expect(recorded[0].inventory).toEqual(twoWay.sources.map(({ id, role, required }) => ({ id, role, required })));
    expect(recorded[0].inventory.map(({ role }) => role)).toEqual(twoWay.sources.map(({ role }) => role));
    stop();
  });

  it('never records a first-time model’s defaults, though its ingestion saved them as a profile', async () => {
    vi.useFakeTimers();
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_speaker');
    ingest('wgl_speaker');
    await vi.advanceTimersByTimeAsync(2_000);
    // Selected again: a profile exists now, but nobody chose it.
    useCadReturnStore.getState().selectBundle(null, null);
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_speaker');
    ingest('wgl_speaker');
    await vi.advanceTimersByTimeAsync(2_000);
    expect(to('/project-setups')).toEqual([]);
    stop();
  });

  it('never records settings an ingestion carried over from another project as that project’s', async () => {
    vi.useFakeTimers();
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(600);
    expect((to('/project-setups') as Array<{ lineageId: string }>).map(({ lineageId }) => lineageId)).toEqual(['wgl_a']);

    // The ingestion states another project, which has no settings of its own:
    // project A's stay on screen and are saved under B.
    ingest('wgl_b');
    await vi.advanceTimersByTimeAsync(2_000);
    useCadReturnStore.getState().selectBundle(null, null);
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_b');
    await vi.advanceTimersByTimeAsync(2_000);
    expect((to('/project-setups') as Array<{ lineageId: string }>).map(({ lineageId }) => lineageId)).toEqual(['wgl_a']);
    stop();
  });

  it('does not re-record a retained CAD project for a solve option edited in the parametric workspace', async () => {
    vi.useFakeTimers();
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_speaker');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(600);
    expect(to('/project-setups')).toHaveLength(1);

    workspaceModeStore.setMode('parametric');
    useSolveOptionsStore.getState().setFrequencySpacing('linear');
    useSolveOptionsStore.getState().updatePolar({ distance: 3 });
    await vi.advanceTimersByTimeAsync(2_000);
    expect(to('/project-setups')).toHaveLength(1);

    // The same kind of edit made in the CAD workspace is the project's.
    workspaceModeStore.setMode('cad');
    useSolveOptionsStore.getState().setFrequencySpacing('log');
    await vi.advanceTimersByTimeAsync(600);
    const recorded = to('/project-setups') as Array<{ setup: { options: { frequency_spacing: string } } }>;
    expect(recorded).toHaveLength(2);
    expect(recorded[1].setup.options.frequency_spacing).toBe('log');
    stop();
  });

  it('records both accuracy and engine at start and whenever either changes', async () => {
    vi.useFakeTimers();
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    await vi.advanceTimersByTimeAsync(0);
    useSolveOptionsStore.getState().setEngine('bempp');
    await vi.advanceTimersByTimeAsync(0);
    useSolveOptionsStore.getState().setEngine('bempp');
    useSolveOptionsStore.getState().setEngine('metal');
    await vi.advanceTimersByTimeAsync(0);
    useSolveOptionsStore.getState().setAccuracy('accurate');
    await vi.advanceTimersByTimeAsync(0);
    expect(to('/solver-selection')).toEqual([
      { engine: 'auto', accuracy: 'fast' },
      { engine: 'bempp', accuracy: 'fast' },
      { engine: 'metal', accuracy: 'fast' },
      { engine: 'auto', accuracy: 'accurate' },
    ]);
    stop();
  });
});

describe('CAD solver selection publication', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    resetCadReturnStore();
    resetCadPreparationStore();
    resetDocumentStore();
    resetSolveOptionsStore();
    workspaceModeStore.setMode('cad');
  });
  afterEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    workspaceModeStore.setMode('parametric');
  });

  function controlled() {
    const calls: Array<{
      selection: { engine: string; accuracy: string };
      resolve: (response: Response) => void;
      reject: (error: Error) => void;
    }> = [];
    const fetcher = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).endsWith('/solver-selection')) return Promise.resolve(json({}));
      return new Promise<Response>((resolve, reject) => {
        calls.push({ selection: JSON.parse(String(init?.body)), resolve, reject });
      });
    }) as unknown as typeof fetch;
    return { calls, fetcher };
  }

  it('finishes the old write before publishing the latest choice and coalesces intermediate choices', async () => {
    const { calls, fetcher } = controlled();
    const stop = startCadSetupPublisher({ fetcher });
    expect(calls[0].selection).toEqual({ engine: 'auto', accuracy: 'fast' });
    useSolveOptionsStore.getState().setEngine('bempp');
    useSolveOptionsStore.getState().setAccuracy('accurate');
    expect(calls).toHaveLength(1);
    calls[0].resolve(json({}));
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(2);
    expect(calls[1].selection).toEqual({ engine: 'auto', accuracy: 'accurate' });
    calls[1].resolve(json({}));
    await vi.advanceTimersByTimeAsync(0);
    useSolveOptionsStore.getState().setAccuracy('accurate');
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(2);
    stop();
  });

  it.each(['network', 503, 408, 429] as const)('recovers a transient %s failure without another edit', async (failure) => {
    const { calls, fetcher } = controlled();
    const states: string[] = [];
    const stop = startCadSetupPublisher({ fetcher, retryDelaysMs: [100], onSaveState: (state) => states.push(state) });
    if (failure === 'network') calls[0].reject(new TypeError('offline'));
    else calls[0].resolve(new Response('{"detail":"temporary"}', { status: failure }));
    await vi.advanceTimersByTimeAsync(99);
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toHaveLength(2);
    expect(calls[1].selection).toEqual(calls[0].selection);
    calls[1].resolve(json({}));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toHaveLength(2);
    expect(states).toEqual([]);
    stop();
  });

  it('bounds retries, reports exhaustion and clears the error after a later successful selection', async () => {
    const { calls, fetcher } = controlled();
    const states: string[] = [];
    const stop = startCadSetupPublisher({ fetcher, retryDelaysMs: [100], onSaveState: (state) => states.push(state) });
    calls[0].reject(new TypeError('offline'));
    await vi.advanceTimersByTimeAsync(100);
    calls[1].reject(new TypeError('offline'));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toHaveLength(2);
    expect(states).toEqual(['failed']);
    useSolveOptionsStore.getState().setAccuracy('accurate');
    expect(calls).toHaveLength(3);
    calls[2].resolve(json({}));
    await vi.advanceTimersByTimeAsync(0);
    expect(states).toEqual(['failed', 'saved']);
    stop();
  });

  it('reports a permanent refusal without retrying it', async () => {
    const { calls, fetcher } = controlled();
    const states: string[] = [];
    const stop = startCadSetupPublisher({ fetcher, retryDelaysMs: [100], onSaveState: (state) => states.push(state) });
    calls[0].resolve(new Response('{"detail":"unsupported selection"}', { status: 422 }));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toHaveLength(1);
    expect(states).toEqual(['failed']);
    stop();
  });

  it('replaces an old retry with the new choice', async () => {
    const { calls, fetcher } = controlled();
    const stop = startCadSetupPublisher({ fetcher, retryDelaysMs: [100] });
    calls[0].reject(new TypeError('offline'));
    await vi.advanceTimersByTimeAsync(0);
    useSolveOptionsStore.getState().setAccuracy('accurate');
    expect(calls).toHaveLength(2);
    expect(calls[1].selection.accuracy).toBe('accurate');
    calls[1].resolve(json({}));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toHaveLength(2);
    stop();
  });

  it('sends the current choice when an older in-flight write fails', async () => {
    const { calls, fetcher } = controlled();
    const stop = startCadSetupPublisher({ fetcher, retryDelaysMs: [100] });
    useSolveOptionsStore.getState().setAccuracy('accurate');
    calls[0].reject(new TypeError('offline'));
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(2);
    expect(calls[1].selection.accuracy).toBe('accurate');
    calls[1].resolve(json({}));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toHaveLength(2);
    stop();
  });

  it('cancels retry timers on stop', async () => {
    const { calls, fetcher } = controlled();
    const states: string[] = [];
    const stop = startCadSetupPublisher({ fetcher, retryDelaysMs: [100], onSaveState: (state) => states.push(state) });
    calls[0].reject(new TypeError('offline'));
    await vi.advanceTimersByTimeAsync(0);
    stop();
    await vi.advanceTimersByTimeAsync(10_000);
    useSolveOptionsStore.getState().setAccuracy('accurate');
    expect(calls).toHaveLength(1);
    expect(states).toEqual([]);
  });

  it.each(['success', 'failure'])('does not publish a queued choice or feedback after an in-flight %s settles following stop', async (outcome) => {
    const { calls, fetcher } = controlled();
    const states: string[] = [];
    const stop = startCadSetupPublisher({ fetcher, retryDelaysMs: [100], onSaveState: (state) => states.push(state) });
    useSolveOptionsStore.getState().setAccuracy('accurate');
    stop();
    if (outcome === 'success') calls[0].resolve(json({}));
    else calls[0].reject(new TypeError('offline'));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls).toHaveLength(1);
    expect(states).toEqual([]);
  });

  it('keeps a solver save error visible when the project setup saves successfully', async () => {
    const { calls, fetcher } = controlled();
    const states: string[] = [];
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 100, retryDelaysMs: [], onSaveState: (state) => states.push(state) });
    calls[0].reject(new TypeError('offline'));
    await vi.advanceTimersByTimeAsync(0);
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(100);
    expect(states).toEqual(['failed']);
    stop();
  });

  it('keeps a project setup error visible when the solver selection saves successfully', async () => {
    const states: string[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/project-setups')) throw new TypeError('offline');
      return json({});
    }) as unknown as typeof fetch;
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 100, retryDelaysMs: [], onSaveState: (state) => states.push(state) });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(100);
    expect(states).toEqual(['failed']);
    useSolveOptionsStore.getState().setAccuracy('accurate');
    await vi.advanceTimersByTimeAsync(0);
    expect(states).toEqual(['failed']);
    stop();
  });
});

describe('CAD setup publisher: what an edit not yet sent does', () => {
  beforeEach(() => {
    localStorage.clear();
    resetCadReturnStore();
    resetCadPreparationStore();
    resetDocumentStore();
    resetSolveOptionsStore();
    workspaceModeStore.setMode('cad');
  });
  afterEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    workspaceModeStore.setMode('parametric');
  });

  it('sends an edit still inside the debounce when the project is switched, under the project it was made in', async () => {
    vi.useFakeTimers();
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(100);
    expect(to('/project-setups')).toEqual([]);

    useCadReturnStore.getState().selectBundle(null, null);
    await vi.advanceTimersByTimeAsync(0);

    const recorded = to('/project-setups') as Array<{ lineageId: string; setup: { geometry: { exterior_only: boolean } } }>;
    expect(recorded.map(({ lineageId }) => lineageId)).toEqual(['wgl_a']);
    expect(recorded[0].setup.geometry.exterior_only).toBe(true);
    stop();
  });

  it('sends an edit still inside the debounce when the publisher stops', async () => {
    vi.useFakeTimers();
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(100);
    expect(to('/project-setups')).toEqual([]);

    stop();
    await vi.advanceTimersByTimeAsync(0);

    expect(to('/project-setups')).toHaveLength(1);
    // The timer went with it: nothing more is sent later.
    await vi.advanceTimersByTimeAsync(2_000);
    expect(to('/project-setups')).toHaveLength(1);
  });

  function flaky(failures: number) {
    const calls: Array<{ body: unknown; keepalive: boolean | undefined }> = [];
    let remaining = failures;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/project-setups')) {
        calls.push({ body: JSON.parse(String(init?.body)), keepalive: init?.keepalive });
        if (remaining > 0) { remaining -= 1; throw new Error('offline'); }
      }
      return json({});
    }) as unknown as typeof fetch;
    return { calls, fetcher };
  }

  it('retries a failed recording itself, with bounded backoff, and stays quiet when a retry lands', async () => {
    vi.useFakeTimers();
    const { calls, fetcher } = flaky(2);
    const states: string[] = [];
    const stop = startCadSetupPublisher({
      fetcher, debounceMs: 500, retryDelaysMs: [1_000, 4_000], onSaveState: (state) => states.push(state),
    });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(600);
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(calls).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(4_000);
    expect(calls).toHaveLength(3);
    expect(calls[2].body).toEqual(calls[0].body);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(3);
    expect(states).toEqual([]);
    stop();
  });

  it('gives up after the last retry, says so once, and says saved when a later edit lands', async () => {
    vi.useFakeTimers();
    const { calls, fetcher } = flaky(3);
    const states: string[] = [];
    const stop = startCadSetupPublisher({
      fetcher, debounceMs: 500, retryDelaysMs: [1_000, 4_000], onSaveState: (state) => states.push(state),
    });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(600 + 1_000 + 4_000);
    expect(calls).toHaveLength(3);
    expect(states).toEqual(['failed']);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(3);

    useCadReturnStore.getState().setExteriorOnly(false);
    await vi.advanceTimersByTimeAsync(600);
    expect(calls).toHaveLength(4);
    expect(states).toEqual(['failed', 'saved']);
    stop();
  });

  it('does not retry an old setup once a newer edit has been sent', async () => {
    vi.useFakeTimers();
    const { calls, fetcher } = flaky(1);
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500, retryDelaysMs: [5_000] });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(600);
    useCadReturnStore.getState().setExteriorOnly(false);
    await vi.advanceTimersByTimeAsync(600);
    expect(calls).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(calls).toHaveLength(2);
    stop();
  });

  it.each([
    ['visibilitychange to hidden', () => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    }],
    ['pagehide', () => { window.dispatchEvent(new Event('pagehide')); }],
  ])('sends an edit still inside the debounce on %s, as a request that survives unload', async (_name, leave) => {
    vi.useFakeTimers();
    const { calls, fetcher } = flaky(0);
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(100);
    expect(calls).toHaveLength(0);
    try { leave(); } finally {
      Reflect.deleteProperty(document, 'visibilityState');
    }
    expect(calls).toHaveLength(1);
    expect(calls[0].keepalive).toBe(true);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(calls).toHaveLength(1);
    stop();
  });

  it('does nothing when the page becomes visible again, and an ordinary send is not keepalive', async () => {
    vi.useFakeTimers();
    const { calls, fetcher } = flaky(0);
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    document.dispatchEvent(new Event('visibilitychange'));
    expect(calls).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(600);
    expect(calls).toHaveLength(1);
    expect(calls[0].keepalive).toBeUndefined();
    stop();
  });

  it('resends a failed recording at once, as keepalive, when the page is hidden before its retry', async () => {
    vi.useFakeTimers();
    const { calls, fetcher } = flaky(1);
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500, retryDelaysMs: [10_000] });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    await vi.advanceTimersByTimeAsync(600);
    expect(calls).toHaveLength(1);
    window.dispatchEvent(new Event('pagehide'));
    expect(calls).toHaveLength(2);
    expect(calls[1].keepalive).toBe(true);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(calls).toHaveLength(2);
    stop();
  });
});

describe('shell-owned CAD setup publisher', () => {
  let host: HTMLDivElement;
  let root: Root;
  let calls: Array<{ lineageId: string; setup: { geometry: { exterior_only: boolean } } }>;
  let failures: number;
  let healthReads: number;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    localStorage.clear();
    resetCadReturnStore();
    resetCadPreparationStore();
    resetDocumentStore();
    resetSolveOptionsStore();

    resetCadCoordinationForTests('off');
    useCadSetupPublisherStore.setState({ error: null });
    useCadConnectionStore.setRecoveryError(null);
    cadReceiptsStore.setState({ recoveryError: null });
    workspaceModeStore.setMode('cad');
    app.coordinatorMounted = false;
    calls = [];
    failures = 0;
    healthReads = 0;
    vi.spyOn(jobsSocket, 'subscribeCadOperations').mockReturnValue(() => undefined);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/delivery')) healthReads += 1;
      if (path.endsWith('/project-setups')) {
        calls.push(JSON.parse(String(init?.body)));
        if (failures-- > 0) throw new Error('offline');
      }
      if (path.includes('/operations')) return json({ operations: [] });
      if (path.endsWith('/returns')) return json({ items: [], cadFolderConfigured: false, coordination: 'off' });
      if (path.endsWith('/fusion-status')) return json({ running: false, state: 'addin_offline' });
      return json({ consumer: 'running', recentRefusals: [] });
    }));
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    resetCadCoordinationForTests();
    workspaceModeStore.setMode('parametric');
  });
  const advance = async (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  it('shows reconnect and receipt-feed recovery failures with action feedback taking precedence (rows 6, 7)', () => {
    const Feedback = ({ error = null }: { error?: string | null }) => createElement('p', null, useCadFeedbackError(error));
    act(() => { root.render(createElement(Feedback)); useCadConnectionStore.setRecoveryError('Reconnect failed'); });
    expect(host.textContent).toBe('Reconnect failed');
    act(() => { root.render(createElement(Feedback, { error: 'Action failed' })); });
    expect(host.textContent).toBe('Action failed');
    act(() => { root.render(createElement(Feedback)); useCadConnectionStore.setRecoveryError(null); cadReceiptsStore.setState({ recoveryError: 'offline' }); });
    expect(host.textContent).toContain('could not check Fusion Sends (offline)');
    act(() => { cadReceiptsStore.setState({ recoveryError: null }); });
    expect(host.textContent).toBe('');
  });

  const mount = async () => act(async () => { root.render(createElement(Shell)); await vi.advanceTimersByTimeAsync(10); });

  it('mounts without the coordinator and publishes only deliberate edits, retrying and flushing the correct project', async () => {
    await mount();
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    ingest('wgl_a');
    await advance(2_000);
    expect(calls).toEqual([]);
    failures = 1;
    useCadReturnStore.getState().setExteriorOnly(true);
    await advance(800);
    expect(calls).toHaveLength(1);
    await advance(1_000);
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual(calls[0]);
    useCadReturnStore.getState().setExteriorOnly(false);
    await advance(100);
    useCadReturnStore.getState().selectBundle(null, null);
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_b');
    await advance(2_000);
    expect(calls.map(({ lineageId }) => lineageId)).toEqual(['wgl_a', 'wgl_a', 'wgl_a']);
    expect(calls[2].setup.geometry.exterior_only).toBe(false);
  });

  it('with the coordinator mounted publishes exactly once per deliberate edit', async () => {
    app.coordinatorMounted = true;
    await mount();
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    await advance(2_000);
    expect(calls).toEqual([]);
    useCadReturnStore.getState().setExteriorOnly(true);
    await advance(2_000);
    expect(calls).toHaveLength(1);
    useCadReturnStore.getState().setExteriorOnly(false);
    await advance(2_000);
    expect(calls).toHaveLength(2);
    expect(calls.map(({ setup }) => setup.geometry.exterior_only)).toEqual([true, false]);
    expect(healthReads).toBe(1);
  });

  it('keeps save-failure feedback without the coordinator and clears it after a later edit saves', async () => {
    await mount();
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    failures = 3;
    useCadReturnStore.getState().setExteriorOnly(true);
    await advance(6_000);
    expect(calls).toHaveLength(3);
    expect(useCadSetupPublisherStore.getState().error).toBe('Settings not saved. Edit a setting to retry.');
    useCadReturnStore.getState().setExteriorOnly(false);
    await advance(800);
    expect(useCadSetupPublisherStore.getState().error).toBeNull();
  });
});
