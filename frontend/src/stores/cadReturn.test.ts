import { beforeEach, describe, expect, it } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import {
  acknowledgedFindingWire,
  DRIVER_REQUIRED_KEYS,
  resetCadReturnStore,
  unacknowledgedBlocking,
  useCadReturnStore,
} from './cadReturn';
import { buildImportedSubmission } from '../jobs/importedSubmission';
import { resetDocumentStore, useDocumentStore } from './document';

const solveProfileStorageKey = 'waveguide-v2-g3-cad-solve-profiles';

const bundle: CadReturnBundle = {
  name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', modifiedAt: '2026-08-11T00:00:00Z', readable: true,
  documentName: 'Speaker', requestId: null, sourceCount: 2, instanceCount: 1,
  sources: [
    { id: 'source-mf', role: 'MF', required: true, suggestedResolutionMm: 8, defaultDriveChannelId: 'drive-mf' },
    { id: 'source-hf', role: 'HF', required: false, suggestedResolutionMm: 3, defaultDriveChannelId: 'drive-hf' },
  ],
};

function record(id = 'wgi_one'): CadReturnIngestRecord {
  return {
    ingest_id: id, created_at: '', return_id: '', manifest_sha256: 'sha256:m', artifact_sha256: 'sha256:a', report_sha256: `sha256:${id}`,
    acoustic_domain: 'free-space', scope: { status: 'degraded', degraded_skip_count: 1 }, sources: [],
    mesh_sizes: { rigid_size_mm: 8, transition_mm: 8, source_size_mm: { 'source-mf': 8, 'source-hf': 3 } }, skipped_source_ids: [],
    freshness: { verdict: 'per-instance', instances: [] },
    findings: [{ id: 'finding-a', kind: 'freshness', blocking: true }],
    symmetry: {}, healing: {}, sizing_estimate: {}, polar_grid_derivation: {}, tag_map: {},
  };
}

describe('CAD return store', () => {
  beforeEach(() => {
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
  });

  it('initializes complete sizes and one default channel per source', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    const state = useCadReturnStore.getState();
    expect(state.sourceSizesMm).toEqual({ 'source-mf': 8, 'source-hf': 3 });
    expect(state.rigidSizeMm).toBe(8);
    expect(state.driveChannels.map(({ id, source_ids }) => ({ id, source_ids }))).toEqual([
      { id: 'drive-mf', source_ids: ['source-mf'] },
      { id: 'drive-hf', source_ids: ['source-hf'] },
    ]);
  });

  it('uses a sub-millimetre coarsest source suggestion without a 1 mm floor', () => {
    useCadReturnStore.getState().selectBundle({
      ...bundle,
      sources: bundle.sources.map((source, index) => ({ ...source, suggestedResolutionMm: index ? 0.15 : 0.4 })),
    });
    expect(useCadReturnStore.getState().rigidSizeMm).toBe(0.4);
    expect(useCadReturnStore.getState().transitionMm).toBe(0.4);
  });

  it('gates on every blocking finding and resets acknowledgements on re-ingest', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().applyIngest(record(), useCadReturnStore.getState().beginIngestIntent());
    expect(unacknowledgedBlocking(useCadReturnStore.getState())).toEqual(['finding-a']);
    useCadReturnStore.getState().acknowledge('finding-a', true);
    expect(unacknowledgedBlocking(useCadReturnStore.getState())).toEqual([]);
    expect(acknowledgedFindingWire(record(), ['finding-a'])).toEqual(['sha256:wgi_one:finding-a']);
    useCadReturnStore.getState().applyIngest(record('wgi_two'), useCadReturnStore.getState().beginIngestIntent());
    expect(useCadReturnStore.getState().acknowledgedFindingIds).toEqual([]);
    expect(unacknowledgedBlocking(useCadReturnStore.getState())).toEqual(['finding-a']);
  });

  it('supports explicit grouping and removes optional skipped sources from channels', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setSourceChannel('source-hf', 'drive-mf');
    expect(useCadReturnStore.getState().driveChannels).toEqual([
      { id: 'drive-mf', source_ids: ['source-mf', 'source-hf'], motion: 'normal' },
    ]);
    useCadReturnStore.getState().setSkipped('source-hf', true);
    expect(useCadReturnStore.getState().driveChannels[0].source_ids).toEqual(['source-mf']);
    expect(useCadReturnStore.getState().needsIngest).toBe(true);
  });

  it('carries the solve setup across a same-inventory arrival but never acknowledgements', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.applyIngest(record(), store.beginIngestIntent());
    store.acknowledge('finding-a', true);
    store.setSourceSize('source-hf', 2.25);
    store.setSourceChannel('source-hf', 'drive-mf');
    store.setExteriorOnly(true);
    store.setCombineEnabled(true);
    store.setSweep({ frequencyStartHz: 300, frequencyEndHz: 12_000, frequencyCount: 31 });

    const arrived = {
      ...bundle,
      modifiedAt: '2026-08-11T02:00:00Z',
      documentName: 'Speaker v2',
      // New suggestions must not clobber the user's chosen sizes.
      sources: bundle.sources.map((source) => ({ ...source, suggestedResolutionMm: 5 })),
    };
    expect(useCadReturnStore.getState().selectArrivedBundle(arrived)).toBe('carried');

    const state = useCadReturnStore.getState();
    expect(state.selectedBundle?.documentName).toBe('Speaker v2');
    expect(state.sourceSizesMm['source-hf']).toBe(2.25);
    expect(state.driveChannels).toEqual([
      { id: 'drive-mf', source_ids: ['source-mf', 'source-hf'], motion: 'normal' },
    ]);
    expect(state.exteriorOnly).toBe(true);
    expect(state.combineEnabled).toBe(true);
    expect(state.frequencyStartHz).toBe(300);
    // The new geometry re-earns its evidence.
    expect(state.ingestRecord).toBeNull();
    expect(state.acknowledgedFindingIds).toEqual([]);
    expect(state.needsIngest).toBe(true);
  });

  it('carries an eligible channel driver across a same-inventory arrival', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setChannelDriverEnabled('drive-hf', true);
    store.setChannelDriverField('drive-hf', 'sd_cm2', 8);

    expect(store.selectArrivedBundle({ ...bundle, modifiedAt: '2026-08-11T02:00:00Z' })).toBe('carried');

    const driver = useCadReturnStore.getState().channelDrivers['drive-hf'];
    expect(driver?.enabled).toBe(true);
    expect(driver?.fields.sd_cm2).toBe(8);
  });

  it('drops a channel driver when the channel turns axial', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setChannelDriverEnabled('drive-mf', true);

    // A driver models a piston, so the server refuses one on axial motion.
    store.setChannelMotion('drive-mf', 'axial');

    expect(useCadReturnStore.getState().channelDrivers['drive-mf']).toBeUndefined();
  });

  it('drops a channel driver when a second source joins the channel', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setChannelDriverEnabled('drive-hf', true);

    // Two sources leave the radiating area ambiguous, which the server refuses.
    store.setSourceChannel('source-mf', 'drive-hf');

    expect(useCadReturnStore.getState().driveChannels).toEqual([
      { id: 'drive-hf', source_ids: ['source-mf', 'source-hf'], motion: 'normal' },
    ]);
    expect(useCadReturnStore.getState().channelDrivers['drive-hf']).toBeUndefined();
  });

  it('never submits a driver a channel cannot carry', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setChannelDriverEnabled('drive-hf', true);
    DRIVER_REQUIRED_KEYS.forEach((key) => store.setChannelDriverField('drive-hf', key, 1));
    store.applyIngest(record(), store.beginIngestIntent());
    const complete = useCadReturnStore.getState();
    expect(buildImportedSubmission(complete).geometry.drive_channels
      .find((channel) => channel.id === 'drive-hf')?.driver).toBeDefined();

    // A form that outlived its channel's eligibility -- reachable only past the
    // store's own pruning -- must still never reach the server.
    const stale = {
      ...complete,
      driveChannels: complete.driveChannels.map((channel) => (
        channel.id === 'drive-hf' ? { ...channel, motion: 'axial' as const } : channel
      )),
    };
    const submitted = buildImportedSubmission(stale);
    expect(submitted.geometry.drive_channels.find((channel) => channel.id === 'drive-hf')?.driver)
      .toBeUndefined();
    expect(submitted.geometry.drive_voltage_v).toBeUndefined();
  });

  it('resets fully when an arrival changes the source inventory', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setSourceSize('source-hf', 2.25);
    store.setCombineEnabled(true);

    const arrived = {
      ...bundle,
      modifiedAt: '2026-08-11T02:00:00Z',
      sourceCount: 1,
      sources: [bundle.sources[0]],
    };
    expect(useCadReturnStore.getState().selectArrivedBundle(arrived)).toBe('reset');
    const state = useCadReturnStore.getState();
    expect(state.sourceSizesMm).toEqual({ 'source-mf': 8 });
    expect(state.combineEnabled).toBe(false);
  });

  it('refuses ingestion evidence smuggled into a stored profile', () => {
    // The persisted payload is machine-local and user-writable, so the restore
    // path — not just the save path — has to guarantee that acknowledgements
    // and ingest state never come back. A restored acknowledgement would let a
    // blocking finding pass its gate without ever being seen.
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setSourceSize('source-hf', 2.25);

    const raw = JSON.parse(localStorage.getItem(solveProfileStorageKey)!);
    Object.assign(raw.profiles[0].settings, {
      acknowledgedFindingIds: ['finding-a'],
      ingestRecord: { ingest_id: 'wgi_forged' },
      needsIngest: false,
      ingestedBundleIdentity: 'forged',
      areaDriftOverrides: ['source-hf'],
    });
    localStorage.setItem(solveProfileStorageKey, JSON.stringify(raw));

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);

    const state = useCadReturnStore.getState();
    expect(state.sourceSizesMm['source-hf']).toBe(2.25);
    expect(state.acknowledgedFindingIds).toEqual([]);
    expect(state.ingestRecord).toBeNull();
    expect(state.needsIngest).toBe(true);
    expect(state.ingestedBundleIdentity).toBeNull();
    expect(state.areaDriftOverrides).toEqual([]);
  });

  it('restores a compatible persisted solve profile across sessions', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 3,
    }, 'current');
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.applyIngest(record(), store.beginIngestIntent());
    store.setSourceSize('source-mf', 6.5);
    store.setSourceSize('source-hf', 2.25);
    store.setRigidSize(9.5);
    store.setTransition(4.5);
    store.setSkipped('source-hf', true);
    store.setChannelMotion('drive-mf', 'axial');
    store.setExteriorOnly(true);
    store.setCombineEnabled(true);
    store.setCombineCrossover('drive-mf→drive-hf', 1_350);
    store.setCombineLevelMatch(false);
    store.setCombineAlign(false);
    store.setChannelDriverEnabled('drive-mf', true);
    store.setChannelDriverField('drive-mf', 'sd_cm2', 135);
    store.setDriveVoltage(4);
    store.setSweep({ frequencyStartHz: 300, frequencyEndHz: 12_000, frequencyCount: 31 });
    store.acknowledge('finding-a', true);
    store.setAreaDriftOverride('source-mf', true);
    store.flagAreaDrift('source-mf');
    store.markIngestStale('Exact-ingest evidence must stay session-local.');

    resetCadReturnStore();
    const compatible = {
      ...bundle,
      modifiedAt: '2026-08-12T00:00:00Z',
      sources: [...bundle.sources].reverse().map((source) => ({ ...source, suggestedResolutionMm: 20 })),
    };
    expect(useCadReturnStore.getState().selectArrivedBundle(compatible)).toBe('carried');

    const restored = useCadReturnStore.getState();
    expect(restored).toMatchObject({
      sourceSizesMm: { 'source-mf': 6.5, 'source-hf': 2.25 },
      rigidSizeMm: 9.5,
      transitionMm: 4.5,
      skippedSourceIds: ['source-hf'],
      driveChannels: [{ id: 'drive-mf', source_ids: ['source-mf'], motion: 'axial' }],
      exteriorOnly: true,
      combineEnabled: true,
      combineCrossoversHz: { 'drive-mf→drive-hf': 1_350 },
      combineLevelMatch: false,
      combineAlign: false,
      channelDrivers: { 'drive-mf': { enabled: true, fields: { sd_cm2: 135 } } },
      driveVoltageV: 4,
      frequencyStartHz: 300,
      frequencyEndHz: 12_000,
      frequencyCount: 31,
      ingestRecord: null,
      acknowledgedFindingIds: [],
      areaDriftOverrides: [],
      areaDriftSourceIds: [],
      needsIngest: true,
      ingestedBundleIdentity: null,
      ingestStaleReason: null,
    });
  });

  it('rejects a persisted profile when the source inventory is incompatible', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 3,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setRigidSize(12);
    useCadReturnStore.getState().setCombineEnabled(true);

    resetCadReturnStore();
    const incompatible = {
      ...bundle,
      sources: bundle.sources.map((source) => source.id === 'source-hf'
        ? { ...source, role: 'LF', suggestedResolutionMm: 5 }
        : source),
    };
    expect(useCadReturnStore.getState().selectArrivedBundle(incompatible)).toBe('reset');
    expect(useCadReturnStore.getState()).toMatchObject({
      sourceSizesMm: { 'source-mf': 8, 'source-hf': 5 },
      rigidSizeMm: 8,
      transitionMm: 8,
      combineEnabled: false,
    });
  });

  it('never restores finding acknowledgements with a solve profile', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 3,
    }, 'current');
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.applyIngest(record(), store.beginIngestIntent());
    store.acknowledge('finding-a', true);
    store.setExteriorOnly(true);
    expect(useCadReturnStore.getState().acknowledgedFindingIds).toEqual(['finding-a']);

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().exteriorOnly).toBe(true);
    expect(useCadReturnStore.getState().acknowledgedFindingIds).toEqual([]);
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
  });

  it.each([
    ['corrupt JSON', '{not-json'],
    ['partial profile', JSON.stringify({ version: 1, profiles: [{}] })],
    ['wrong version', JSON.stringify({ version: 99, profiles: [] })],
  ])('drops a %s payload without applying it', (_label, raw) => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 3,
    }, 'current');
    localStorage.setItem(solveProfileStorageKey, raw);

    expect(() => useCadReturnStore.getState().selectBundle(bundle)).not.toThrow();
    expect(useCadReturnStore.getState()).toMatchObject({
      sourceSizesMm: { 'source-mf': 8, 'source-hf': 3 },
      rigidSizeMm: 8,
      exteriorOnly: false,
    });
    expect(localStorage.getItem(solveProfileStorageKey)).toBeNull();
  });

  it('keeps the 20 most recently used profiles', () => {
    for (let index = 0; index < 20; index += 1) {
      useDocumentStore.getState().setCadLink({
        designId: `wgd_${index}`, lineageId: `wgl_${index}`, baseEditVersion: 1,
      }, 'current');
      useCadReturnStore.getState().selectBundle(bundle);
      useCadReturnStore.getState().setDriveVoltage(3 + index);
    }
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_0', lineageId: 'wgl_0', baseEditVersion: 2,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_20', lineageId: 'wgl_20', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setDriveVoltage(23);

    const stored = JSON.parse(localStorage.getItem(solveProfileStorageKey) ?? '{}') as {
      profiles: Array<{ designId: string }>;
    };
    expect(stored.profiles).toHaveLength(20);
    expect(stored.profiles.map(({ designId }) => designId)).toContain('wgd_0');
    expect(stored.profiles.map(({ designId }) => designId)).not.toContain('wgd_1');
  });

  it('marks a refreshed changed listing stale while preserving sizing edits', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().applyIngest(record(), useCadReturnStore.getState().beginIngestIntent());
    useCadReturnStore.getState().setSourceSize('source-hf', 2.25);
    useCadReturnStore.getState().refreshSelectedBundle({
      ...bundle,
      modifiedAt: '2026-08-11T01:00:00Z',
      sources: bundle.sources.map((source) => source.id === 'source-hf'
        ? { ...source, suggestedResolutionMm: 2.5 }
        : source),
    });
    const state = useCadReturnStore.getState();
    expect(state.needsIngest).toBe(true);
    expect(state.ingestStaleReason).toContain('source inventory or source sizing suggestions changed');
    expect(state.sourceSizesMm['source-hf']).toBe(2.25);
    expect(state.acknowledgedFindingIds).toEqual([]);
  });
});
