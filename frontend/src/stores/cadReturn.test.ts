import { beforeEach, describe, expect, it } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import {
  blockingFindingWire,
  channelDriverWire,
  combineChain,
  combineChannelRole,
  combineDefaultHz,
  combineEnabledEffective,
  combineMembers,
  combineWire,
  driverEditedKeys,
  driverMissingGroups,
  driverValues,
  driversForChannels,
  projectChannelDrivers,
  DRIVER_REQUIRED_KEYS,
  resetCadReturnStore,
  useCadReturnStore,
  type DriverPreset,
} from './cadReturn';
import {
  expandLegacy,
  relinkPairs,
  withChannel,
  withDelayMode,
  withGainMode,
  withPair,
} from '../results/crossoverSpec';
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

  it('distinguishes a first arrival from resetting an existing solve setup', () => {
    expect(useCadReturnStore.getState().selectArrivedBundle(bundle)).toBe('initial');

    const changedInventory = {
      ...bundle,
      modifiedAt: '2026-08-11T02:00:00Z',
      sourceCount: 1,
      sources: [bundle.sources[0]],
    };
    expect(useCadReturnStore.getState().selectArrivedBundle(changedInventory)).toBe('reset');
  });

  it('puts every blocking finding on the wire without a gate', () => {
    const twoFindings = {
      ...record(),
      findings: [
        { id: 'finding-a', kind: 'freshness', blocking: true },
        { id: 'finding-b', kind: 'healing-performed', blocking: true },
        { id: 'finding-c', kind: 'informational', blocking: false },
      ],
    };
    expect(blockingFindingWire(twoFindings)).toEqual([
      'sha256:wgi_one:finding-a',
      'sha256:wgi_one:finding-b',
    ]);
    expect(blockingFindingWire(record('wgi_two'))).toEqual(['sha256:wgi_two:finding-a']);
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

  it('carries the solve setup across a same-inventory arrival', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.applyIngest(record(), store.beginIngestIntent());
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
    // One mass and one compliance source complete the spec; which ones is the
    // user's choice, exactly as `DriverSpec.validate_completeness` has it.
    store.setChannelDriverField('drive-hf', 'mmd_g', 12);
    store.setChannelDriverField('drive-hf', 'cms_m_per_n', 0.0003);
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
    // The combine choice resets to "none made yet", not to off.
    expect(state.combineEnabled).toBeNull();
  });

  it('refuses ingestion evidence smuggled into a stored profile', () => {
    // The persisted payload is machine-local and user-writable, so the restore
    // path — not just the save path — has to guarantee that ingest state and
    // evidence never come back from storage.
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setSourceSize('source-hf', 2.25);

    const raw = JSON.parse(localStorage.getItem(solveProfileStorageKey)!);
    Object.assign(raw.profiles[0].settings, {
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
    expect(state.ingestRecord).toBeNull();
    expect(state.needsIngest).toBe(true);
    expect(state.ingestedBundleIdentity).toBeNull();
    expect(state.areaDriftOverrides).toEqual([]);
  });

  it('carries the cardioid form across sessions and tolerates a profile written before it existed', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setPassiveCardioid({
      enabled: true, rearVolumeL: 6, portLengthMm: 25, modelPortAreaM2: 0.05,
      bemPortAreaM2: 0.0094, foamResistancePaSM3: 10_000, coupled: true,
    });

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().passiveCardioid).toMatchObject({
      enabled: true, rearVolumeL: 6, coupled: true, invertPort: true, portAreaSource: 'user',
    });

    // A profile from before this section shipped carries no key at all. Losing
    // every stored mesh size over a feature nobody used would be the worse
    // answer, so the missing form starts at its defaults instead.
    const raw = JSON.parse(localStorage.getItem(solveProfileStorageKey)!);
    delete raw.profiles[0].settings.passiveCardioid;
    localStorage.setItem(solveProfileStorageKey, JSON.stringify(raw));
    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().passiveCardioid).toMatchObject({ enabled: false, rearVolumeL: null });
    expect(useCadReturnStore.getState().sourceSizesMm).toEqual(
      Object.fromEntries(bundle.sources.map((source) => [source.id, source.suggestedResolutionMm])),
    );
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
    // The crossover is set while both bands still have a channel: a spec names
    // its members, so it cannot be edited once one of them is skipped away.
    store.setCombineEnabled(true);
    store.setCombineCrossover('drive-mf→drive-hf', 1_350);
    store.updateCombineSpec((spec) => withDelayMode(withGainMode(spec, 'manual'), 'manual'));
    store.setSkipped('source-hf', true);
    store.setChannelMotion('drive-mf', 'axial');
    store.setExteriorOnly(true);
    store.setChannelDriverEnabled('drive-mf', true);
    store.setChannelDriverField('drive-mf', 'sd_cm2', 135);
    store.setDriveVoltage(4);
    store.setSweep({ frequencyStartHz: 300, frequencyEndHz: 12_000, frequencyCount: 31 });
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
      combineSpec: expandLegacy(['drive-mf', 'drive-hf'], [1_350], false, false),
      channelDrivers: { 'drive-mf': { enabled: true, fields: { sd_cm2: 135 } } },
      driveVoltageV: 4,
      frequencyStartHz: 300,
      frequencyEndHz: 12_000,
      frequencyCount: 31,
      ingestRecord: null,
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
      combineEnabled: null,
    });
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
      profiles: Array<{ owner: string }>;
    };
    expect(stored.profiles).toHaveLength(20);
    expect(stored.profiles.map(({ owner }) => owner)).toContain('design:wgd_0:wgl_0');
    expect(stored.profiles.map(({ owner }) => owner)).not.toContain('design:wgd_1:wgl_1');
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
  });
});

describe('combined output', () => {
  const rebanded = (roles: Record<string, string>): CadReturnBundle => ({
    ...bundle,
    sources: bundle.sources.map((source) => ({ ...source, role: roles[source.id] ?? source.role })),
  });

  beforeEach(() => {
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
  });

  it('is on by default for two or more drive channels and off for one', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    const state = useCadReturnStore.getState();
    expect(state.combineEnabled).toBeNull();
    expect(combineEnabledEffective(state)).toBe(true);
    expect(combineWire(state)?.members).toEqual(['drive-mf', 'drive-hf']);

    useCadReturnStore.getState().setSourceChannel('source-hf', 'drive-mf');
    const single = useCadReturnStore.getState();
    expect(combineEnabledEffective(single)).toBe(false);
    expect(combineWire(single)).toBeUndefined();
  });

  it('keeps an explicit off, and forgets it when another return is selected', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setCombineEnabled(false);
    expect(combineEnabledEffective(useCadReturnStore.getState())).toBe(false);
    expect(combineWire(useCadReturnStore.getState())).toBeUndefined();

    // An unrelated edit must not talk the user back into the default.
    useCadReturnStore.getState().setSourceSize('source-hf', 2.25);
    expect(combineEnabledEffective(useCadReturnStore.getState())).toBe(false);

    useCadReturnStore.getState().selectBundle({ ...bundle, name: 'other.wgreturn', bundlePath: 'wgreturn/other.wgreturn' });
    expect(useCadReturnStore.getState().combineEnabled).toBeNull();
    expect(combineEnabledEffective(useCadReturnStore.getState())).toBe(true);
  });

  it('defaults each crossover from the two bands it joins', () => {
    expect(combineDefaultHz('LF', 'MF')).toBe(100);
    expect(combineDefaultHz('MF', 'HF')).toBe(1_000);
    expect(combineDefaultHz('LF', 'HF')).toBe(1_000);
    expect(combineDefaultHz('MF', 'MF')).toBeUndefined();
    expect(combineDefaultHz(undefined, 'HF')).toBeUndefined();

    useCadReturnStore.getState().selectBundle(bundle);
    expect(combineChain(useCadReturnStore.getState())).toEqual([{
      key: 'drive-mf→drive-hf',
      lower: 'drive-mf',
      upper: 'drive-hf',
      hz: 1_000,
      lowerRole: 'MF',
      upperRole: 'HF',
      defaultHz: 1_000,
      outsideSweep: false,
      linked: true,
      family: 'lr',
      order: 4,
    }]);
  });

  it('falls back inside the sweep when the role default lies outside it', () => {
    useCadReturnStore.getState().selectBundle(rebanded({ 'source-mf': 'LF', 'source-hf': 'MF' }));
    // 100 Hz would be refused by the server's own band check on a 200 Hz sweep,
    // so the log-spaced sqrt(200 * 20000) = 2000 Hz is used instead.
    const [pair] = combineChain(useCadReturnStore.getState());
    expect(pair).toMatchObject({ defaultHz: 100, outsideSweep: true, hz: 2_000 });
    expect(combineWire(useCadReturnStore.getState())?.channels['drive-mf'].lp)
      .toEqual({ family: 'lr', order: 4, fc_hz: 2_000 });

    useCadReturnStore.getState().setSweep({ frequencyStartHz: 50, frequencyEndHz: 20_000, frequencyCount: 24 });
    expect(combineChain(useCadReturnStore.getState())[0]).toMatchObject({ outsideSweep: false, hz: 100 });
  });

  it('keeps the log-spaced fallback and listing order for an unroled return', () => {
    useCadReturnStore.getState().selectBundle(rebanded({ 'source-mf': 'AUX', 'source-hf': 'AUX' }));
    expect(combineChain(useCadReturnStore.getState())).toEqual([{
      key: 'drive-mf→drive-hf',
      lower: 'drive-mf',
      upper: 'drive-hf',
      hz: 2_000,
      lowerRole: undefined,
      upperRole: undefined,
      defaultHz: undefined,
      outsideSweep: false,
      linked: true,
      family: 'lr',
      order: 4,
    }]);
  });

  it('adopts the spec a recombine was computed with, ignoring foreign members', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    const applied = expandLegacy(['drive-mf', 'drive-hf'], [1_450]);
    useCadReturnStore.getState().setCombineSpecFromResult(applied);
    expect(useCadReturnStore.getState().combineSpec).toEqual(applied);
    expect(combineChain(useCadReturnStore.getState())[0].hz).toBe(1_450);

    // A run from another return names channels this one has not got.
    useCadReturnStore.getState().setCombineSpecFromResult(
      expandLegacy(['drive-lf', 'drive-mf', 'drive-hf'], [90, 900]),
    );
    expect(useCadReturnStore.getState().combineSpec).toEqual(applied);
  });

  it('keeps a family and slope change symmetric and submits it as the v2 wire', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().updateCombineSpec(
      (spec) => withPair(spec, 'drive-mf→drive-hf', { hz: 1_400, family: 'butterworth', order: 3 }),
    );
    const [pair] = combineChain(useCadReturnStore.getState());
    expect(pair).toMatchObject({ hz: 1_400, family: 'butterworth', order: 3, linked: true });
    const wire = combineWire(useCadReturnStore.getState())!;
    expect(wire.channels['drive-mf'].lp).toEqual({ family: 'butterworth', order: 3, fc_hz: 1_400 });
    expect(wire.channels['drive-hf'].hp).toEqual({ family: 'butterworth', order: 3, fc_hz: 1_400 });
    expect(wire.reference).toBe('drive-hf');
    expect(wire).not.toHaveProperty('crossovers_hz');
  });

  it('marks a pair unlinked when one channel is edited on its own', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().updateCombineSpec((spec) => withChannel(spec, 'drive-mf', {
      lp: { family: 'butterworth', order: 3, fcHz: 900 },
    }));
    expect(combineChain(useCadReturnStore.getState())[0]).toMatchObject({ linked: false, hz: 900 });
    useCadReturnStore.getState().updateCombineSpec(relinkPairs);
    expect(combineChain(useCadReturnStore.getState())[0]).toMatchObject({ linked: true, hz: 900 });
  });

  it('falls back to the base chain when the drive channels no longer match the override', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setCombineCrossover('drive-mf→drive-hf', 1_450);
    useCadReturnStore.getState().setSourceChannel('source-hf', 'drive-mf');
    expect(combineWire(useCadReturnStore.getState())).toBeUndefined();
    useCadReturnStore.getState().setSourceChannel('source-hf', 'drive-hf');
    expect(combineChain(useCadReturnStore.getState())[0].hz).toBe(1_450);
  });

  it('migrates a version 2 profile crossover map into one spec override', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setCombineEnabled(true);

    const raw = JSON.parse(localStorage.getItem(solveProfileStorageKey)!);
    // Versions 1 to 3 were design-owned and stated the identity the owner is
    // rebuilt from, so a fixture claiming to be one has to state it too.
    raw.version = 2;
    raw.profiles[0].designId = 'wgd_speaker';
    raw.profiles[0].lineageId = 'wgl_speaker';
    delete raw.profiles[0].settings.combineSpec;
    raw.profiles[0].settings.combineCrossoversHz = { 'drive-mf→drive-hf': 1_350 };
    raw.profiles[0].settings.combineLevelMatch = false;
    raw.profiles[0].settings.combineAlign = null;
    localStorage.setItem(solveProfileStorageKey, JSON.stringify(raw));

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().combineSpec)
      .toEqual(expandLegacy(['drive-mf', 'drive-hf'], [1_350], false, true));
  });

  it('migrates an untouched version 2 profile to no override at all', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setCombineEnabled(true);

    const raw = JSON.parse(localStorage.getItem(solveProfileStorageKey)!);
    // Versions 1 to 3 were design-owned and stated the identity the owner is
    // rebuilt from, so a fixture claiming to be one has to state it too.
    raw.version = 2;
    raw.profiles[0].designId = 'wgd_speaker';
    raw.profiles[0].lineageId = 'wgl_speaker';
    delete raw.profiles[0].settings.combineSpec;
    raw.profiles[0].settings.combineCrossoversHz = {};
    raw.profiles[0].settings.combineLevelMatch = null;
    raw.profiles[0].settings.combineAlign = null;
    localStorage.setItem(solveProfileStorageKey, JSON.stringify(raw));

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().combineSpec).toBeNull();
    expect(combineChain(useCadReturnStore.getState())[0].hz).toBe(1_000);
  });

  it('restores a profile written before the combined output defaulted on as no choice', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setCombineEnabled(false);

    const raw = JSON.parse(localStorage.getItem(solveProfileStorageKey)!);
    raw.version = 1;
    raw.profiles[0].settings.combineEnabled = false;
    localStorage.setItem(solveProfileStorageKey, JSON.stringify(raw));

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().combineEnabled).toBeNull();
    expect(combineEnabledEffective(useCadReturnStore.getState())).toBe(true);
  });

  it('restores an explicit off from a current profile', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setCombineEnabled(false);

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().combineEnabled).toBe(false);
    expect(combineEnabledEffective(useCadReturnStore.getState())).toBe(false);
  });
});

const PRESET: DriverPreset = {
  id: 'Acme::HD-1::8',
  label: 'Acme HD-1',
  source: 'database',
  kind: 'cd',
  z_ohm: 8,
  xo_min_hz: 1_600,
  base: { sd_cm2: 26, bl_t_m: 12.4, re_ohm: 6.2, le_mh: 0.12, mms_g: 2.4, fs_hz: 620, vas_l: 0.35, qms: 3.1 },
};

describe('a channel driver picked from the library', () => {
  beforeEach(() => {
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setChannelDriverEnabled('drive-hf', true);
  });

  it('submits the preset values under the user’s edits', () => {
    const store = useCadReturnStore.getState();
    store.setChannelDriverPreset('drive-hf', PRESET);
    expect(driverValues(useCadReturnStore.getState().channelDrivers['drive-hf'])).toEqual(PRESET.base);

    store.setChannelDriverField('drive-hf', 'bl_t_m', 11.9);
    const form = useCadReturnStore.getState().channelDrivers['drive-hf'];
    // The override lives beside the base rather than replacing it, which is
    // what makes the edit reversible and countable.
    expect(form.fields).toEqual({ bl_t_m: 11.9 });
    expect(form.preset?.base.bl_t_m).toBe(12.4);
    expect(driverValues(form)).toMatchObject({ bl_t_m: 11.9, sd_cm2: 26 });
    expect(driverEditedKeys(form)).toEqual(['bl_t_m']);

    // Typing the database value back is not an edit.
    store.setChannelDriverField('drive-hf', 'bl_t_m', 12.4);
    expect(driverEditedKeys(useCadReturnStore.getState().channelDrivers['drive-hf'])).toEqual([]);
  });

  it('counts a datasheet Mms and Fs as a complete spec', () => {
    // The pre-picker rule wanted Mmd and Cms, neither of which a datasheet
    // prints. A driver stating Mms and Fs is complete to the server, so it has
    // to be complete to the rail as well.
    useCadReturnStore.getState().setChannelDriverPreset('drive-hf', PRESET);
    const form = useCadReturnStore.getState().channelDrivers['drive-hf'];
    expect(driverMissingGroups(form)).toEqual([]);
    expect(channelDriverWire(form)).toBeDefined();

    const withoutMass = { ...form, preset: { ...PRESET, base: { ...PRESET.base, mms_g: undefined } } };
    expect(driverMissingGroups(withoutMass)).toEqual([['mms_g', 'mmd_g']]);
    expect(channelDriverWire(withoutMass)).toBeUndefined();
  });

  it('emits the label and never both masses', () => {
    const store = useCadReturnStore.getState();
    store.setChannelDriverPreset('drive-hf', PRESET);
    // A hand-typed Mmd on top of a preset that already states Mms: sending
    // both refuses the whole solve (DriverSpec.validate_completeness).
    store.setChannelDriverField('drive-hf', 'mmd_g', 2.1);
    const wire = channelDriverWire(useCadReturnStore.getState().channelDrivers['drive-hf'])!;

    expect(wire.label).toBe('Acme HD-1');
    expect(wire.mms_g).toBe(2.4);
    expect(wire).not.toHaveProperty('mmd_g');

    // A hand-entered driver with no preset still submits Mmd and no label.
    store.setChannelDriverPreset('drive-hf', null);
    (['sd_cm2', 'bl_t_m', 're_ohm'] as const).forEach((key) => store.setChannelDriverField('drive-hf', key, 1));
    store.setChannelDriverField('drive-hf', 'mmd_g', 12);
    store.setChannelDriverField('drive-hf', 'cms_m_per_n', 0.0003);
    const manual = channelDriverWire(useCadReturnStore.getState().channelDrivers['drive-hf'])!;
    expect(manual.mmd_g).toBe(12);
    expect(manual).not.toHaveProperty('mms_g');
    expect(manual).not.toHaveProperty('label');
  });

  it('treats a hand-entered driver as its own numbers, not as edits of a base', () => {
    const store = useCadReturnStore.getState();
    store.setChannelDriverPreset('drive-hf', {
      id: 'manual:radian-745neo',
      label: 'Radian 745Neo',
      source: 'manual',
      kind: 'cd',
      z_ohm: null,
      xo_min_hz: null,
      base: {},
    });
    (['sd_cm2', 'bl_t_m', 're_ohm'] as const).forEach((key) => store.setChannelDriverField('drive-hf', key, 1));
    store.setChannelDriverField('drive-hf', 'mms_g', 2.4);

    const form = useCadReturnStore.getState().channelDrivers['drive-hf'];
    // Every value is an override on an empty base, so counting them would put
    // an "n edited" chip and a live reset on a driver with nothing to reset to.
    expect(form.fields).toMatchObject({ sd_cm2: 1, mms_g: 2.4 });
    expect(driverEditedKeys(form)).toEqual([]);
    // Still incomplete: no compliance source yet.
    expect(driverMissingGroups(form)).toEqual([['cms_m_per_n', 'vas_l', 'fs_hz']]);

    store.setChannelDriverField('drive-hf', 'fs_hz', 620);
    // The name reaches the wire exactly as a picked driver's does.
    expect(channelDriverWire(useCadReturnStore.getState().channelDrivers['drive-hf'])).toEqual({
      sd_cm2: 1, bl_t_m: 1, re_ohm: 1, mms_g: 2.4, fs_hz: 620, label: 'Radian 745Neo',
    });
  });

  it('keeps the installation inputs when the driver changes or the edits are reset', () => {
    const store = useCadReturnStore.getState();
    store.setChannelDriverPreset('drive-hf', PRESET);
    store.setChannelDriverField('drive-hf', 'count', 2);
    store.setChannelDriverField('drive-hf', 'rear_volume_l', 1.5);
    store.setChannelDriverField('drive-hf', 'sd_cm2', 30);
    expect(driverEditedKeys(useCadReturnStore.getState().channelDrivers['drive-hf'])).toEqual(['sd_cm2']);

    store.clearChannelDriverOverrides('drive-hf');
    const reset = useCadReturnStore.getState().channelDrivers['drive-hf'];
    expect(reset.fields).toEqual({ count: 2, rear_volume_l: 1.5 });
    expect(driverValues(reset).sd_cm2).toBe(26);

    // A different driver drops the edits with the driver they belonged to.
    store.setChannelDriverField('drive-hf', 'sd_cm2', 30);
    store.setChannelDriverPreset('drive-hf', { ...PRESET, id: 'Acme::HD-1::16', z_ohm: 16 });
    expect(useCadReturnStore.getState().channelDrivers['drive-hf'].fields).toEqual({ count: 2, rear_volume_l: 1.5 });
  });

  it('carries the preset across sessions and reads a pre-picker profile as hand entry', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    // The link has to be in place before the profile is written.
    useCadReturnStore.getState().setChannelDriverPreset('drive-hf', PRESET);
    useCadReturnStore.getState().setChannelDriverField('drive-hf', 'xmax_mm', 1.2);

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().channelDrivers['drive-hf']).toEqual({
      enabled: true, fields: { xmax_mm: 1.2 }, preset: PRESET,
    });

    // A profile written before drivers could be picked carries no preset key
    // at all. Its hand-typed fields are still the whole driver.
    const raw = JSON.parse(localStorage.getItem(solveProfileStorageKey)!);
    delete raw.profiles[0].settings.channelDrivers['drive-hf'].preset;
    localStorage.setItem(solveProfileStorageKey, JSON.stringify(raw));
    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().channelDrivers['drive-hf']).toEqual({
      enabled: true, fields: { xmax_mm: 1.2 }, preset: null,
    });
  });

  it('tolerates a stored preset saved before xo_min_hz existed, and rejects a malformed one', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().setChannelDriverPreset('drive-hf', PRESET);

    // A preset stored before this field existed carries no `xo_min_hz` key at
    // all; that migrates to null rather than dropping the preset.
    const raw = JSON.parse(localStorage.getItem(solveProfileStorageKey)!);
    delete raw.profiles[0].settings.channelDrivers['drive-hf'].preset.xo_min_hz;
    localStorage.setItem(solveProfileStorageKey, JSON.stringify(raw));
    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().channelDrivers['drive-hf'].preset).toEqual({ ...PRESET, xo_min_hz: null });

    // A present but malformed value still fails the whole profile, like every
    // other field parsed here.
    raw.profiles[0].settings.channelDrivers['drive-hf'].preset.xo_min_hz = 'soon';
    localStorage.setItem(solveProfileStorageKey, JSON.stringify(raw));
    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().channelDrivers).toEqual({});
  });

  it('refuses a malformed stored preset rather than restoring half of one', () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_speaker', lineageId: 'wgl_speaker', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().setChannelDriverPreset('drive-hf', PRESET);
    useCadReturnStore.getState().setRigidSize(9.5);

    const raw = JSON.parse(localStorage.getItem(solveProfileStorageKey)!);
    raw.profiles[0].settings.channelDrivers['drive-hf'].preset.source = 'somewhere-else';
    localStorage.setItem(solveProfileStorageKey, JSON.stringify(raw));

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle);
    expect(useCadReturnStore.getState().channelDrivers).toEqual({});
    expect(useCadReturnStore.getState().rigidSizeMm).toBe(8);
  });
});

describe('the CAD project owns its solve settings', () => {
  const PRESET_12RS430 = {
    id: 'Faital Pro::12RS430::8', label: 'Faital Pro 12RS430', source: 'database' as const,
    kind: 'lf' as const, z_ohm: 8, xo_min_hz: null,
    base: { sd_cm2: 552, bl_t_m: 18, re_ohm: 6.8, mms_g: 91.3, vas_l: 131.2, fs_hz: 30 },
  };

  beforeEach(() => {
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
  });

  it('keeps a project\'s drivers when the open design is a different one', () => {
    // A project that exists only in CAD has no design identity of its own, so
    // its settings used to be filed under whichever parametric design happened
    // to be open -- and were gone as soon as another one was.
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_unrelated', lineageId: 'wgl_unrelated', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');
    useCadReturnStore.getState().setChannelDriverPreset('drive-mf', PRESET_12RS430);

    // Another design is opened, and the same project's return comes back.
    resetCadReturnStore();
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_other', lineageId: 'wgl_other', baseEditVersion: 1,
    }, 'current');
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');

    expect(useCadReturnStore.getState().channelDrivers['drive-mf'].preset)
      .toMatchObject({ id: 'Faital Pro::12RS430::8' });
  });

  it('does not hand one project\'s drivers to another', () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');
    useCadReturnStore.getState().setChannelDriverPreset('drive-mf', PRESET_12RS430);

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_someone_else');

    expect(useCadReturnStore.getState().channelDrivers).toEqual({});
  });

  it('saves a driver picked while an archived run is on screen', () => {
    // A recalled run's bundle is not readable, which used to mean nothing done
    // on it was kept -- including picking the driver you recalled it to change.
    useCadReturnStore.setState({
      selectedBundle: { ...bundle, readable: false, bundlePath: '' },
      ingestRecord: record(),
      projectLineageId: 'wgl_party',
      driveChannels: [{ id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' }],
      needsIngest: false,
    });
    useCadReturnStore.getState().setChannelDriverPreset('drive-mf', PRESET_12RS430);

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');

    expect(useCadReturnStore.getState().channelDrivers['drive-mf'].preset)
      .toMatchObject({ label: 'Faital Pro 12RS430' });
  });

  it('offers the project\'s drivers for a recalled run, fitted to its channels', () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');
    useCadReturnStore.getState().setChannelDriverPreset('drive-mf', PRESET_12RS430);

    const drivers = projectChannelDrivers(bundle, 'wgl_party')!;
    expect(drivers['drive-mf'].preset).toMatchObject({ id: 'Faital Pro::12RS430::8' });
    // A channel the recalled run does not have keeps nothing.
    expect(driversForChannels(drivers, [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }]))
      .toEqual({});
    expect(projectChannelDrivers(bundle, 'wgl_other')).toBeNull();
  });

  it('survives a document rename, which is not a new project', () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');
    useCadReturnStore.getState().setChannelDriverPreset('drive-mf', PRESET_12RS430);

    const renamed = { ...bundle, documentName: 'Speaker v2', modifiedAt: '2026-08-12T00:00:00Z' };
    expect(useCadReturnStore.getState().selectArrivedBundle(renamed)).toBe('carried');
    expect(useCadReturnStore.getState().channelDrivers['drive-mf'].preset)
      .toMatchObject({ id: 'Faital Pro::12RS430::8' });
  });
});

describe('a picked driver re-reads its T/S from the library', () => {
  const CATALOGUE_ROW = {
    id: 'B&C::DE250::8', label: 'B&C DE250', source: 'database' as const,
    kind: 'cd' as const, z_ohm: 8, xo_min_hz: 1_200,
    // What a compression-driver sheet publishes before anyone fills in T/S:
    // enough to name the driver, not enough to solve one.
    base: { re_ohm: 5.4, bl_t_m: 17.5, le_mh: 0.62 },
  };

  beforeEach(() => {
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
  });

  it('takes the numbers the row has now, and keeps the user\'s edits', () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');
    useCadReturnStore.getState().setChannelDriverPreset('drive-hf', CATALOGUE_ROW);
    useCadReturnStore.getState().setChannelDriverField('drive-hf', 'count', 2);
    expect(channelDriverWire(useCadReturnStore.getState().channelDrivers['drive-hf'])).toBeUndefined();

    // The library row gains its T/S.
    const changed = useCadReturnStore.getState().refreshChannelDriverBases({
      'drive-hf': {
        presetId: 'B&C::DE250::8',
        base: { re_ohm: 5.4, bl_t_m: 17.5, le_mh: 0.62, sd_cm2: 13.2, mms_g: 0.65, fs_hz: 550 },
        xo_min_hz: 1_200,
      },
    });

    expect(changed).toEqual(['drive-hf']);
    const form = useCadReturnStore.getState().channelDrivers['drive-hf'];
    expect(driverValues(form)).toMatchObject({ sd_cm2: 13.2, mms_g: 0.65, fs_hz: 550, count: 2 });
    // Complete now, so it reaches the wire instead of being dropped.
    expect(channelDriverWire(form)).toMatchObject({ sd_cm2: 13.2, label: 'B&C DE250' });
  });

  it('leaves a channel alone when its driver has changed underneath', () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');
    useCadReturnStore.getState().setChannelDriverPreset('drive-hf', CATALOGUE_ROW);

    const changed = useCadReturnStore.getState().refreshChannelDriverBases({
      'drive-hf': { presetId: 'Someone::Else::8', base: { sd_cm2: 999 } },
    });

    expect(changed).toEqual([]);
    expect(driverValues(useCadReturnStore.getState().channelDrivers['drive-hf']).sd_cm2).toBeUndefined();
  });

  it('reports nothing when the row still says what it said', () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');
    useCadReturnStore.getState().setChannelDriverPreset('drive-hf', CATALOGUE_ROW);

    expect(useCadReturnStore.getState().refreshChannelDriverBases({
      'drive-hf': { presetId: 'B&C::DE250::8', base: { ...CATALOGUE_ROW.base }, xo_min_hz: 1_200 },
    })).toEqual([]);
  });
});

describe('listing-gone staleness self-heals', () => {
  it('clears when the identical bundle reappears, and only then', () => {
    // A poll against a restarting server reads an empty listing; that must
    // not permanently block solving once the same bundle is listed again.
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.applyIngest(record(), store.beginIngestIntent());
    useCadReturnStore.getState().refreshSelectedBundle(null);
    expect(useCadReturnStore.getState().needsIngest).toBe(true);
    expect(useCadReturnStore.getState().ingestStaleReason).toContain('no longer appears');

    useCadReturnStore.getState().refreshSelectedBundle(bundle);
    expect(useCadReturnStore.getState().needsIngest).toBe(false);
    expect(useCadReturnStore.getState().ingestStaleReason).toBeNull();

    // A genuinely changed bundle keeps its flag.
    useCadReturnStore.getState().refreshSelectedBundle({ ...bundle, modifiedAt: '2099-01-01T00:00:00Z' });
    expect(useCadReturnStore.getState().needsIngest).toBe(true);
  });

  it('leaves a run recalled from the archive solvable across listing polls', () => {
    // A recalled run's bundle is rebuilt from its ingest record and has no
    // workspace path, so the listing can never contain it. Reconciling it
    // against the listing blocked "that older one was better -- run it again"
    // 2.5 s after every recall.
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
    useCadReturnStore.setState({
      selectedBundle: { ...bundle, bundlePath: '', readable: false },
      ingestRecord: record(),
      needsIngest: false,
      ingestedBundleIdentity: null,
      ingestStaleReason: null,
    });

    useCadReturnStore.getState().refreshSelectedBundle(null);
    useCadReturnStore.getState().refreshSelectedBundle(bundle);

    expect(useCadReturnStore.getState().needsIngest).toBe(false);
    expect(useCadReturnStore.getState().ingestStaleReason).toBeNull();
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe('');
  });
});

describe('combined output: band roles', () => {
  beforeEach(() => {
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
  });

  it('assigns a mixed-role channel its lowest band regardless of source order', () => {
    const mixed = {
      ...bundle,
      sources: [
        { ...bundle.sources[1], id: 'source-hf', role: 'hf' },
        { ...bundle.sources[0], id: 'source-lf', role: ' LF ' },
      ],
    };
    useCadReturnStore.setState({
      selectedBundle: mixed,
      driveChannels: [{ id: 'mixed', source_ids: ['source-hf', 'source-lf'], motion: 'normal' }],
    });

    expect(combineChannelRole(useCadReturnStore.getState(), 'mixed')).toBe('LF');
  });

  it('canonicalizes returned band roles before ordering and defaulting the chain', () => {
    const mixedCase = {
      ...bundle,
      sources: [
        { ...bundle.sources[1], id: 'source-hf', role: 'hf' },
        { ...bundle.sources[0], id: 'source-lf', role: ' LF ' },
      ],
    };
    useCadReturnStore.setState({
      selectedBundle: mixedCase,
      driveChannels: [
        { id: 'high', source_ids: ['source-hf'], motion: 'normal' },
        { id: 'low', source_ids: ['source-lf'], motion: 'normal' },
      ],
    });

    const state = useCadReturnStore.getState();
    expect(combineMembers(state)).toEqual(['low', 'high']);
    expect(combineChain(state)[0]).toMatchObject({
      lower: 'low',
      upper: 'high',
      lowerRole: 'LF',
      upperRole: 'HF',
      defaultHz: 1_000,
      hz: 1_000,
    });
  });
});
