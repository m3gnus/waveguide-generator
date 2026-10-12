import { beforeEach, expect, it } from 'vitest';
import { exteriorTransducerError, parseExteriorTransducer } from '../stores/exteriorTransducer';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { buildImportedSubmission, exteriorModeBlocker, undrivenChannels } from './importedSubmission';
import { buildCadProjectSetup } from '../shell/cadSetupPublisher';

const form = { version: 1, motion_axis: [1, 2, 3] as [number, number, number],
  re_ohm: 6, le_h: .0005, bl_n_per_a: 7, mmd_kg: .02, cms_m_per_n: .001, rms_n_s_per_m: 1.5 };
beforeEach(() => { resetCadReturnStore(); resetSolveOptionsStore(); });

function enable() {
  useSolveOptionsStore.getState().setEngine('beat-metal');
  useCadReturnStore.setState({ driveChannels: [{ id: 'd', source_ids: ['s'], motion: 'normal' }], combineEnabled: false,
    ingestRecord: { ingest_id: 'fixture', manifest_sha256: 'm', artifact_sha256: 'a', polar_grid_derivation: {}, findings: [] } as never,
    sourceSizesMm: { s: 3 }, needsIngest: false, driveVoltageV: 5.66 });
  useCadReturnStore.getState().setExteriorTransducer('d', form);
}

it('uses explicit SI values and voltage while omitting legacy LEM', () => {
  enable();
  expect(exteriorModeBlocker(useCadReturnStore.getState(), useSolveOptionsStore.getState())).toBeNull();
  const wire = buildImportedSubmission();
  expect(wire.geometry.drive_channels[0]).toEqual({ id: 'd', source_ids: ['s'], motion: 'axial', exterior_transducer: form });
  expect(wire.geometry.drive_voltage_v).toBe(5.66);
  expect(undrivenChannels(useCadReturnStore.getState())).toEqual([]);
  useCadReturnStore.getState().setChannelMotion('d', 'normal');
  expect(useCadReturnStore.getState().driveChannels[0].motion).toBe('axial');
  useCadReturnStore.getState().setExteriorTransducer('d', undefined);
  expect(buildImportedSubmission().geometry.drive_channels[0]).not.toHaveProperty('exterior_transducer');
});

it('never silently drops incomplete or unknown saved physics', () => {
  enable();
  for (const value of [{ version: 1, motion_axis: [0, 0, 1] }, { ...form, mmd_kg: 0 }, { ...form, mms_kg: .02 }]) {
    const parsed = parseExteriorTransducer(value);
    expect(exteriorTransducerError(parsed)).not.toBeNull();
    useCadReturnStore.getState().setExteriorTransducer('d', parsed);
    expect(() => buildImportedSubmission()).toThrow();
  }
  expect(parseExteriorTransducer(form)).toEqual(form);
});

it('refuses hybrid jobs, automatic engine fallback and adaptive jobs', () => {
  enable();
  useSolveOptionsStore.getState().setEngine('auto');
  expect(() => buildImportedSubmission()).toThrow('Choose BEAT');
  useSolveOptionsStore.getState().setEngine('beat-cpu');
  useCadReturnStore.setState({ driveChannels: [...useCadReturnStore.getState().driveChannels, { id: 'ideal', source_ids: ['other'], motion: 'normal' }] });
  expect(() => buildImportedSubmission()).toThrow('every channel');
  useCadReturnStore.setState({ driveChannels: useCadReturnStore.getState().driveChannels.slice(0, 1) });
  useSolveOptionsStore.getState().setAdaptiveFrequencySampling(true);
  expect(() => buildImportedSubmission()).toThrow('adaptive');
});

it('publishes and restores the exact opt-in setup and voltage', () => {
  enable();
  useCadReturnStore.setState({ selectedBundle: { sources: [{ id: 's', role: 'LF', required: true }] } as never });
  const setup = buildCadProjectSetup(useCadReturnStore.getState(), useSolveOptionsStore.getState(), undefined, 'lineage')!;
  expect(setup.setup.geometry.drive_channels).toEqual(buildImportedSubmission().geometry.drive_channels);
  expect(setup.setup.geometry.drive_voltage_v).toBe(5.66);
});

it('requires clearing the mechanical coordinate before source reassignment', () => {
  enable();
  useCadReturnStore.setState({ selectedBundle: { sources: [{ id: 's', defaultDriveChannelId: 'd' }] } as never });
  useCadReturnStore.getState().setSourceChannel('s', 'different');
  expect(useCadReturnStore.getState().driveChannels[0]).toEqual({ id: 'd', source_ids: ['s'], motion: 'axial', exterior_transducer: form });
});

const semi = { enabled: true, re_prime_ohm: 5.5, leb_h: .0001, le_h: .002, ke_semi_h: .03, rss_ohm: 20 };

it('passes and persists v2 physical models without a user-supplied Sd', () => {
  enable();
  const extended = { ...form, version: 2, power_w: 100, xmax_mm: 2, semi_inductance: semi,
    lumped_sealed_rear_chamber: { enabled: true, volume_m3: .01 } };
  useCadReturnStore.getState().setExteriorTransducer('d', extended);
  expect(parseExteriorTransducer(JSON.parse(JSON.stringify(extended)))).toEqual(extended);
  expect(exteriorTransducerError(extended)).toBeNull();
  expect(buildImportedSubmission().geometry.drive_channels[0].exterior_transducer).toEqual(extended);
  useCadReturnStore.setState({ selectedBundle: { sources: [{ id: 's', role: 'LF', required: true }] } as never });
  const setup = buildCadProjectSetup(useCadReturnStore.getState(), useSolveOptionsStore.getState(), undefined, 'lineage')!;
  const savedChannels = setup.setup.geometry.drive_channels as Array<{ exterior_transducer: unknown }>;
  const saved = JSON.parse(JSON.stringify(savedChannels[0].exterior_transducer));
  expect(parseExteriorTransducer(saved)).toEqual(extended);
  // Switching models off retains their editable values in a setup.
  const disabled = { ...extended, semi_inductance: { ...semi, enabled: false },
    lumped_sealed_rear_chamber: { enabled: false, volume_m3: .01 } };
  expect(parseExteriorTransducer(disabled)).toEqual(disabled);
  expect(exteriorTransducerError(disabled)).toBeNull();
  expect(parseExteriorTransducer(form)).toEqual(form);
});

it('blocks malformed, incomplete and unsupported v2 options rather than dropping them', () => {
  enable();
  for (const options of [
    { power_w: 0 },
    { power_w: true },
    { xmax_mm: -1 },
    { xmax_mm: Infinity },
    { semi_inductance: { enabled: true } },
    { semi_inductance: { ...semi, enabled: 1 } },
    { semi_inductance: { ...semi, le_h: 0 } },
    { semi_inductance: { ...semi, ke_semi_h: -1 } },
    { semi_inductance: { ...semi, leb_h: Infinity } },
    { semi_inductance: { ...semi, rss_ohm: true } },
    { semi_inductance: { ...semi, extra: 1 } },
    { lumped_sealed_rear_chamber: { enabled: true } },
    { lumped_sealed_rear_chamber: { enabled: true, volume_m3: 0 } },
    { lumped_sealed_rear_chamber: { enabled: true, volume_m3: .01, projected_area_m2: .02 } },
    { lumped_sealed_rear_chamber: null },
  ]) {
    const parsed = parseExteriorTransducer({ ...form, version: 2, ...options });
    expect(exteriorTransducerError(parsed)).not.toBeNull();
    useCadReturnStore.getState().setExteriorTransducer('d', parsed);
    expect(() => buildImportedSubmission()).toThrow();
  }
});

it('restores v2 driver options through the persisted CAD profile parser', () => {
  localStorage.clear();
  const bundle = { name: 'driver.wgreturn', bundlePath: 'wgreturn/driver.wgreturn', modifiedAt: '2026-08-11T00:00:00Z', readable: true,
    documentName: 'Driver', requestId: null, sourceCount: 1, instanceCount: 1,
    sources: [{ id: 's', role: 'LF', required: true, suggestedResolutionMm: 3, defaultDriveChannelId: 'd' }] };
  useCadReturnStore.getState().selectBundle(bundle, 'driver-project');
  const extended = { ...form, version: 2, power_w: 100, xmax_mm: 2, semi_inductance: semi,
    lumped_sealed_rear_chamber: { enabled: true, volume_m3: .01 } };
  useCadReturnStore.getState().setExteriorTransducer('d', extended);
  resetCadReturnStore();
  useCadReturnStore.getState().selectBundle(bundle, 'driver-project');
  expect(useCadReturnStore.getState().driveChannels[0].exterior_transducer).toEqual(extended);
  localStorage.clear();
});
