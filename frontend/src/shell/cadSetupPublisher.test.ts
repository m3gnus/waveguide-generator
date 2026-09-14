import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { resetCadPreparationStore, useCadPreparationStore } from '../stores/cadPreparation';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
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
