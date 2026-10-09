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
