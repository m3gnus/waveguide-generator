import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { resetCadPreparationStore, useCadPreparationStore } from '../stores/cadPreparation';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { buildCadProjectSetup, startCadSetupPublisher } from './cadSetupPublisher';

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

describe('CAD setup publisher', () => {
  beforeEach(() => {
    localStorage.clear();
    resetCadReturnStore();
    resetCadPreparationStore();
    resetDocumentStore();
    resetSolveOptionsStore();
  });
  afterEach(() => {
    vi.useRealTimers();
    localStorage.clear();
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
    expect(setup.options.frequency_range).toEqual([200, 20_000]);
    expect(setup.options.num_frequencies).toBe(24);
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

  it('records a project the user already chose settings for when it is selected', async () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_speaker');
    // Saved as the user edits, exactly as before this publisher existed.
    useCadReturnStore.getState().setExteriorOnly(true);
    useCadReturnStore.getState().selectBundle(null, null);

    vi.useFakeTimers();
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_speaker');
    await vi.advanceTimersByTimeAsync(600);
    const recorded = to('/project-setups') as Array<{ setup: { geometry: { exterior_only: boolean } } }>;
    expect(recorded).toHaveLength(1);
    expect(recorded[0].setup.geometry.exterior_only).toBe(true);
    stop();
  });

  it('records the solver selection at start and whenever the selector changes', async () => {
    const { fetcher, to } = recorder();
    const stop = startCadSetupPublisher({ fetcher, debounceMs: 500 });
    useSolveOptionsStore.getState().setEngine('bempp');
    useSolveOptionsStore.getState().setEngine('bempp');
    useSolveOptionsStore.getState().setEngine('metal');
    await Promise.resolve();
    expect(to('/solver-selection')).toEqual([{ engine: 'auto' }, { engine: 'bempp' }, { engine: 'metal' }]);
    stop();
  });
});
