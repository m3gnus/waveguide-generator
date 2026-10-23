vi.mock('./cadProjectSettings', async original => ({ ...await original<typeof import('./cadProjectSettings')>(), loadCadProjectSettings: () => Promise.resolve(true) }));
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import {
  combineSpecEffective,
  blockingFindingWire,
  channelDriverPresent,
  channelDriverWire,
  combineChain,
  combineChannelRole,
  combineDefaultHz,
  combineEnabledEffective,
  combineMembers,
  combineWire,
  driverEditedKeys,
  driverMissingGroups,
  driverShortfallText,
  driverValues,
  DRIVER_REQUIRED_KEYS,
  resetCadReturnStore,
  useCadReturnStore,
  type DriverPreset,
} from './cadReturn';
import {
  relinkPairs,
  withChannel,
  withPair,
} from '../results/crossoverSpec';
import { buildImportedSubmission } from '../jobs/importedSubmission';
import { resetDocumentStore, useDocumentStore } from './document';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { workspaceModeStore } from './workspaceMode';

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

function project(lineage_id: string) {
  return {
    lineage_id, design_id: null, document_native_id: null,
    document_name: null, archive_stem: null,
  };
}

describe('CAD return store', () => {
  beforeEach(() => {
    localStorage.clear();
    resetSolveOptionsStore();
    resetDocumentStore();
    resetCadReturnStore();
  });

  it('drops the previous project\'s CAD options when another project is selected with a file on screen', () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_a');
    useCadReturnStore.getState().setExteriorOnly(true);
    useImportedSolveOptionsStore.getState().updatePolar({ distance: 9 });
    useImportedSolveOptionsStore.getState().setFrequencyMode('list');
    useImportedSolveOptionsStore.getState().setMeshValidationMode('off');
    useImportedSolveOptionsStore.getState().setAccuracy('accurate');
    workspaceModeStore.setImportedInput('file');
    // An automatic selection of project B arrives in the background.
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_b', true);
    workspaceModeStore.setImportedInput('cad');
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({
      frequencyMode: 'range', meshValidationMode: useSolveOptionsStore.getState().meshValidationMode,
      accuracy: 'fast', polar: { distance: useSolveOptionsStore.getState().polar.distance } });
  });

  it('leaves the options alone when the owner is adopted and no profile exists', () => {
    useDocumentStore.getState().setCadLink({ designId: 'wgd_new', lineageId: 'wgl_new', baseEditVersion: 1 }, 'current');
    useCadReturnStore.getState().selectBundle(bundle);
    useImportedSolveOptionsStore.getState().updatePolar({ distance: 9 });
    useImportedSolveOptionsStore.getState().setFrequencyMode('list');
    const store = useCadReturnStore.getState();
    store.applyIngest({ ...record(), project: project('wgl_other') }, store.beginIngestIntent());
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ frequencyMode: 'list', polar: { distance: 9 } });
  });

  it('initializes complete sizes and one default channel per source', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    const state = useCadReturnStore.getState();
    expect(state.sourceSizesMm).toEqual({ 'source-mf': 8, 'source-hf': 3 });
    expect(state.rigidSizeMm).toBe(25);
    expect(state.transitionMm).toBe(100);
    expect(state.driveChannels.map(({ id, source_ids }) => ({ id, source_ids }))).toEqual([
      { id: 'drive-mf', source_ids: ['source-mf'] },
      { id: 'drive-hf', source_ids: ['source-hf'] },
    ]);
  });

  it('keeps custom sub-millimetre source suggestions independent of wall sizing', () => {
    useCadReturnStore.getState().selectBundle({
      ...bundle,
      sources: bundle.sources.map((source, index) => ({ ...source, suggestedResolutionMm: index ? 0.15 : 0.4 })),
    });
    expect(useCadReturnStore.getState().sourceSizesMm).toEqual({ 'source-mf': 0.4, 'source-hf': 0.15 });
    expect(useCadReturnStore.getState().rigidSizeMm).toBe(25);
    expect(useCadReturnStore.getState().transitionMm).toBe(100);
  });

  it('uses the server default mesh without recomputing source role defaults', () => {
    useCadReturnStore.getState().selectBundle({
      ...bundle,
      defaultMesh: { rigid_size_mm: 25, transition_mm: 100, source_size_mm: { 'source-mf': 15, 'source-hf': 6 } },
    });
    expect(useCadReturnStore.getState()).toMatchObject({
      rigidSizeMm: 25, transitionMm: 100, sourceSizesMm: { 'source-mf': 15, 'source-hf': 6 },
    });
  });

  it('uses server defaults for newly listed sources while retaining existing choices', () => {
    useCadReturnStore.getState().selectBundle({ ...bundle, sources: [bundle.sources[0]], sourceCount: 1 });
    useCadReturnStore.getState().setSourceSize('source-mf', 9);
    const refreshed = {
      ...bundle,
      defaultMesh: { rigid_size_mm: 25, transition_mm: 100, source_size_mm: { 'source-mf': 15, 'source-hf': 6 } },
    };
    useCadReturnStore.getState().refreshSelectedBundle(refreshed);
    expect(useCadReturnStore.getState().sourceSizesMm).toEqual({ 'source-mf': 9, 'source-hf': 6 });
    // Re-enabling an unsized optional source follows the same server policy.
    useCadReturnStore.setState({ sourceSizesMm: { 'source-mf': 9 }, skippedSourceIds: ['source-hf'] });
    useCadReturnStore.getState().setSkipped('source-hf', false);
    expect(useCadReturnStore.getState().sourceSizesMm).toEqual({ 'source-mf': 9, 'source-hf': 6 });
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
    store.setChannelDriverField('drive-hf', 'sd_cm2', 8);

    expect(store.selectArrivedBundle({ ...bundle, modifiedAt: '2026-08-11T02:00:00Z' })).toBe('carried');

    const driver = useCadReturnStore.getState().channelDrivers['drive-hf'];
    expect(channelDriverPresent(driver)).toBe(true);
    expect(driver?.fields.sd_cm2).toBe(8);
  });

  it('drops a channel driver when the channel turns axial', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setChannelDriverField('drive-mf', 'sd_cm2', 135);

    // A driver models a piston, so the server refuses one on axial motion.
    store.setChannelMotion('drive-mf', 'axial');

    expect(useCadReturnStore.getState().channelDrivers['drive-mf']).toBeUndefined();
  });

  it('drops a channel driver when a second source joins the channel', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setChannelDriverField('drive-hf', 'sd_cm2', 26);

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
    useCadReturnStore.getState().setSweep({ frequencyStartHz: 200, frequencyEndHz: 20_000, frequencyCount: 24 });
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
    // sqrt(200 * 20000): the log-spaced midpoint of this sweep.
    useCadReturnStore.getState().setSweep({ frequencyStartHz: 200, frequencyEndHz: 20_000, frequencyCount: 24 });
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

  it('keeps a family and slope change symmetric and submits it as the v2 wire', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setCombineSpec(withPair(combineSpecEffective(useCadReturnStore.getState())!, 'drive-mf→drive-hf', { hz: 1_400, family: 'butterworth', order: 3 }));
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
    useCadReturnStore.getState().setCombineSpec(withChannel(combineSpecEffective(useCadReturnStore.getState())!, 'drive-mf', {
      lp: { family: 'butterworth', order: 3, fcHz: 900 },
    }));
    expect(combineChain(useCadReturnStore.getState())[0]).toMatchObject({ linked: false, hz: 900 });
    useCadReturnStore.getState().setCombineSpec(relinkPairs(combineSpecEffective(useCadReturnStore.getState())!));
    expect(combineChain(useCadReturnStore.getState())[0]).toMatchObject({ linked: true, hz: 900 });
  });

  it('falls back to the base chain when the drive channels no longer match the override', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setCombineSpec(withPair(combineSpecEffective(useCadReturnStore.getState())!, 'drive-mf→drive-hf', { hz: 1_450 }));
    useCadReturnStore.getState().setSourceChannel('source-hf', 'drive-mf');
    expect(combineWire(useCadReturnStore.getState())).toBeUndefined();
    useCadReturnStore.getState().setSourceChannel('source-hf', 'drive-hf');
    expect(combineChain(useCadReturnStore.getState())[0].hz).toBe(1_450);
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

  it('makes the semi-inductance pair required only once one half is typed', () => {
    // LR-2 is optional, so an untouched driver must stay complete; but half a
    // pair is a 422 from DriverSpec, and the point of mirroring the server's
    // rule here is that the user hears about it before the BEM run, not after.
    const store = useCadReturnStore.getState();
    store.setChannelDriverPreset('drive-hf', PRESET);
    expect(driverMissingGroups(useCadReturnStore.getState().channelDrivers['drive-hf'])).toEqual([]);

    store.setChannelDriverField('drive-hf', 'le2_mh', 1.4);
    let form = useCadReturnStore.getState().channelDrivers['drive-hf'];
    expect(driverMissingGroups(form)).toEqual([['re2_ohm']]);
    expect(driverShortfallText(form)).toBe('Re2');
    expect(channelDriverWire(form)).toBeUndefined();

    store.setChannelDriverField('drive-hf', 're2_ohm', 8);
    form = useCadReturnStore.getState().channelDrivers['drive-hf'];
    expect(driverMissingGroups(form)).toEqual([]);
    const wire = channelDriverWire(form)!;
    expect(wire.le2_mh).toBe(1.4);
    expect(wire.re2_ohm).toBe(8);
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

  it('counts a driver present only by its own numbers, never the installation fields', () => {
    // Count and rear volume describe the channel and survive a Clear, so they
    // must not make a cleared channel look driven.
    expect(channelDriverPresent(undefined)).toBe(false);
    expect(channelDriverPresent({ fields: {}, preset: null })).toBe(false);
    expect(channelDriverPresent({ fields: { count: 2, rear_volume_l: 1.5 }, preset: null })).toBe(false);
    expect(channelDriverPresent({ fields: { sd_cm2: 26 }, preset: null })).toBe(true);
    expect(channelDriverPresent({ fields: {}, preset: PRESET })).toBe(true);
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

  it('does not hand one project\'s drivers to another', () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_party');
    useCadReturnStore.getState().setChannelDriverPreset('drive-mf', PRESET_12RS430);

    resetCadReturnStore();
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_someone_else');

    expect(useCadReturnStore.getState().channelDrivers).toEqual({});
  });

  /** Reload, or any first selection: nothing knows which CAD project the open
   * design belongs to until the ingestion states it, so the selection files the
   * settings under the design. Saving them under the project the ingestion then
   * names would put whatever that selection produced -- usually the defaults a
   * restore under the wrong owner left behind -- over the project's own saved
   * setup. */

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

  it('leaves an Onshape return solvable across WGLink listing polls', () => {
    // An Onshape return is published under WG's own data directory, so the
    // WGLink return folder never held it and its absence from that listing is
    // not evidence about it. Reconciling it would mark the rebuild that just
    // succeeded stale again on the next poll.
    localStorage.clear();
    resetDocumentStore();
    resetCadReturnStore();
    const onshape: CadReturnBundle = {
      ...bundle,
      name: 'wgr_demo.wgreturn',
      bundlePath: 'wgr_demo.wgreturn',
      bundleOrigin: 'onshape',
    };
    const store = useCadReturnStore.getState();
    store.selectBundle(onshape);
    store.applyIngest(record(), store.beginIngestIntent());

    useCadReturnStore.getState().refreshSelectedBundle(null);

    expect(useCadReturnStore.getState().needsIngest).toBe(false);
    expect(useCadReturnStore.getState().ingestStaleReason).toBeNull();
    expect(useCadReturnStore.getState().selectedBundle?.bundleOrigin).toBe('onshape');
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

describe('FEM exterior-only choice binding', () => {
  beforeEach(() => {
    localStorage.clear();
    resetSolveOptionsStore();
    resetDocumentStore();
    resetCadReturnStore();
  });

  it('resets an exterior-only choice when a later snapshot declares new FEM air', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.applyIngest(record(), store.beginIngestIntent());
    store.setExteriorOnly(true);
    expect(useCadReturnStore.getState().exteriorOnlyRegionIds).toEqual([]);
    store.applyIngest({ ...record('new'), evidence: { fem_regions: [{ region_id: 'new-air' }] } }, store.beginIngestIntent());
    expect(useCadReturnStore.getState().exteriorOnly).toBe(false);
  });

  it('preserves a choice only for the same excluded FEM set', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    const air = { ...record(), evidence: { fem_regions: [{ region_id: 'air' }] } };
    store.applyIngest(air, store.beginIngestIntent());
    store.setExteriorOnly(true);
    store.applyIngest({ ...air, manifest_sha256: 'sha256:another' }, store.beginIngestIntent());
    expect(useCadReturnStore.getState().exteriorOnly).toBe(true);
    store.applyIngest({ ...air, evidence: { fem_regions: [{ region_id: 'air' }, { region_id: 'second' }] } }, store.beginIngestIntent());
    expect(useCadReturnStore.getState().exteriorOnly).toBe(false);
  });

  it('retains existing exterior-only behavior on snapshots without regions', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setExteriorOnly(true);
    store.applyIngest(record(), store.beginIngestIntent());
    expect(useCadReturnStore.getState().exteriorOnly).toBe(true);
  });
});

it('recalls an explicit exterior-only run on its own FEM snapshot', () => {
  resetCadReturnStore();
  const store = useCadReturnStore.getState();
  store.selectBundle(bundle);
  const air = { ...record(), evidence: { fem_regions: [{ region_id: 'air' }] } };
  store.restoreIngest(air, store.beginIngestIntent(), { exteriorOnly: true });
  expect(useCadReturnStore.getState().exteriorOnly).toBe(true);
  expect(useCadReturnStore.getState().exteriorOnlyRegionIds).toEqual(['air']);
  store.applyIngest({ ...air, evidence: { fem_regions: [{ region_id: 'new' }] } }, store.beginIngestIntent());
  expect(useCadReturnStore.getState().exteriorOnly).toBe(false);
});

it('prefills explicit BEAT Sd from the selected library driver before the legacy form is retired', () => {
  localStorage.clear();
  resetCadReturnStore();
  const store = useCadReturnStore.getState();
  store.selectBundle(bundle);
  store.setChannelDriverPreset('drive-hf', PRESET);
  store.setExteriorTransducer('drive-hf', { version: 1, motion_axis: [0, 0, 1] });
  const transducer = useCadReturnStore.getState().driveChannels.find(c => c.id === 'drive-hf')!.exterior_transducer!;
  expect(transducer).toMatchObject({ version: 2, sd_m2: .0026, re_ohm: 6.2, bl_n_per_a: 12.4 });
  expect(transducer.le_h).toBeCloseTo(.00012);
  expect(transducer.mmd_kg).toBeUndefined(); // The library's Mms is not bare Mmd.
  store.setExteriorTransducer('drive-hf', { ...transducer, sd_m2: .003 });
  expect(useCadReturnStore.getState().driveChannels.find(c => c.id === 'drive-hf')!.exterior_transducer!.sd_m2).toBe(.003);
});
