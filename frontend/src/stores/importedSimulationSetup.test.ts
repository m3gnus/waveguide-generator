import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { RegisteredMesh } from '../api/meshImport';
import type { ImportedSetup } from '../jobs/importedSetup';
import { bindImportedSetup } from '../jobs/importedSetup';
import { simulationDriverGeometry } from '../jobs/importedSubmission';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { startCadSetupPublisher } from '../shell/cadSetupPublisher';
import { observeFileDriverBaseRefresh, refreshChannelDriverBases } from '../shell/cadlink/restores';
import { hasPassiveCardioidSurface, resetCadReturnStore, useCadReturnStore, type DriverPreset } from './cadReturn';
import { editFileDraft, flushImportedSetupWrites, openImported, resetImportedModelStore, useImportedModelStore } from './importedModel';
import { createImportedSimulationSetup, importedSimulationSetup, seedFileSimulation } from './importedSimulationSetup';
import { fileMeshSetup, fileMeshSubmission, fileSetupForSave } from './meshImport';
import { resetSolveOptionsStore } from './solveOptions';
import { workspaceModeStore } from './workspaceMode';

const msh = new TextDecoder().decode(readFileSync('../server/tests/fixtures/mesh_import/tetra.msh'));
const mesh = {
  ingest_id: 'wgi_file', model_id: 'mesh-file:sha256:artifact', producer: 'mesh-file',
  manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact', msh_text: msh,
  summary: { node_count: 4, triangle_count: 4, dimensions: [100, 100, 100], groups: [] },
  source_file: { filename: 'speaker.msh', transform: { units: 'mm', forward: '+z', up: '+y' }, source_tags: [1, 2, 3] },
  source_tags: { left: 1, right: 2, high: 3 },
  sources: [{ id: 'left', role: 'MF' }, { id: 'right', role: 'MF' }, { id: 'high', role: 'HF' }],
} satisfies RegisteredMesh;
const preset: DriverPreset = { id: 'catalog:woofer', label: 'Woofer', source: 'database', kind: 'lf', z_ohm: 8,
  xo_min_hz: null, base: { sd_cm2: 100, bl_t_m: 10, re_ohm: 6, mms_g: 20, fs_hz: 50 } };
let stopPublisher: (() => void) | undefined;
function show(setup: ImportedSetup | null = null) {
  void openImported({ kind: 'file', prepared: mesh, setup });
  importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(msh), 'file', mesh.ingest_id));
}
beforeEach(() => {
  resetCadReturnStore(); resetSolveOptionsStore(); resetImportedModelStore(); importedMeshStore.clear();
  workspaceModeStore.setImportedInput('file'); workspaceModeStore.setMode('cad');
  show();
});
afterEach(() => { stopPublisher?.(); stopPublisher = undefined; });

it('binds an independent input to its own state and record sources', () => {
  const record = { ...mesh, source_tags: { unrelated: 7 }, sources: [{ id: 'cone', role: 'LF' }] };
  let state = seedFileSimulation(record, null);
  const adapter = createImportedSimulationSetup({
    input: 'assembly', read: () => state,
    write: update => { state = { ...state, ...(typeof update === 'function' ? update(state) : update) }; },
    sources: () => record.sources, skippedSourceIds: () => [],
    setSweep: update => { state = { ...state, ...update }; },
  });
  expect(adapter.get().sources).toEqual([{ id: 'cone', role: 'LF' }]);
  expect(adapter.get().driveChannels[0].source_ids).toEqual(['cone']);
  adapter.get().setChannelMotion(state.driveChannels[0].id, 'axial');
  adapter.get().setSweep({ frequencyStartHz: 75 });
  expect(adapter.get().driveChannels[0].motion).toBe('axial');
  expect(adapter.get().frequencyStartHz).toBe(75);
  expect(importedSimulationSetup().sources).toEqual(mesh.sources);
});

it('lets another adapter keep an explicit group spanning roles', () => {
  const record = { ...mesh, sources: [{ id: 'left', role: 'LF' }, { id: 'high', role: 'HF' }] };
  let state = seedFileSimulation(record, null);
  state = { ...state, driveChannels: state.driveChannels.filter(channel => channel.source_ids[0] !== 'right'),
    driverGroups: [{ id: 'array', label: 'Array', channelIds: ['source:left', 'source:high'] }] };
  const adapter = createImportedSimulationSetup({
    input: 'assembly', read: () => state,
    write: update => { state = { ...state, ...(typeof update === 'function' ? update(state) : update) }; },
    sources: () => record.sources, skippedSourceIds: () => [], setSweep: () => {},
  });
  expect(adapter.get().driverGroups).toEqual([{ id: 'array', label: 'Array', channelIds: ['source:left', 'source:high'] }]);
  adapter.get().setChannelMotion('source:left', 'axial');
  expect(adapter.get().driveChannels.map(channel => channel.motion)).toEqual(['axial', 'axial']);
});

it('matches the server aperture roles and exact legacy source names', () => {
  expect(hasPassiveCardioidSurface([{ id: 'group_7', role: 'PASSIVE_CARDIOID' }])).toBe(true);
  expect(hasPassiveCardioidSurface([{ id: 'opening', role: 'port_exit' }])).toBe(true);
  expect(hasPassiveCardioidSurface([{ id: 'mid_port_exit_right', role: 'MF' }])).toBe(true);
  expect(hasPassiveCardioidSurface([{ id: 'not_port_exit', role: 'MF' }])).toBe(false);
  expect(hasPassiveCardioidSurface([{ id: 'group_7', role: 'PASSIVE_CARDIOID_L' }])).toBe(false);
});

it('saves, restores and submits file cardioid physics selected by source role', () => {
  const record = { ...mesh, source_tags: { left: 1, high: 3, opening: 4 },
    sources: [mesh.sources[0], mesh.sources[2], { id: 'opening', role: 'PASSIVE_CARDIOID' }] };
  void openImported({ kind: 'file', prepared: record, setup: null });
  importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(msh), 'file', record.ingest_id));
  importedSimulationSetup().setPassiveCardioid({ enabled: true });
  expect(() => fileMeshSetup()).toThrow('Passive cardioid needs');
  importedSimulationSetup().setChannelDriverPreset('source:left', preset);
  importedSimulationSetup().setPassiveCardioid({ rearVolumeL: 6, portLengthMm: 25,
    modelPortAreaM2: 0.05, bemPortAreaM2: 0.0094, foamResistancePaSM3: 10_000, coupled: true });
  const saved = fileMeshSetup();
  expect(saved.geometry).toMatchObject({ passive_cardioid_rear_volume_l: 6,
    passive_cardioid_port_length_mm: 25, passive_cardioid_coupled: true });
  void openImported({ kind: 'file', prepared: record, setup: saved });
  expect(fileMeshSubmission()).toEqual(bindImportedSetup(record, saved));
  expect(importedSimulationSetup().passiveCardioid).toMatchObject({ enabled: true, rearVolumeL: 6, coupled: true });
  importedSimulationSetup().setCombineEnabled(true);
  expect(() => fileMeshSetup()).toThrow('Turn off Crossover while using coupled passive cardioid.');
});

it('refreshes a file preset on reopen without changing its saved solve', async () => {
  importedSimulationSetup().setChannelDriverPreset('source:high', preset);
  const saved = fileMeshSetup();
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ spec: { re_ohm: 7 }, xo_min_hz: 1200 })));
  const stop = observeFileDriverBaseRefresh(fetcher);
  try {
    const reopened = { ...mesh, ingest_id: 'wgi_reopened' };
    void openImported({ kind: 'file', prepared: reopened, setup: saved });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(msh), 'file', reopened.ingest_id));
    await vi.waitFor(() => expect(importedSimulationSetup().channelDrivers['source:high'].preset?.base.re_ohm).toBe(7));
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fileMeshSubmission()).toEqual(bindImportedSetup(reopened, saved));
    expect(useImportedModelStore.getState().setup).toEqual(saved);
  } finally { stop(); }
});

it('does not apply a file driver refresh to a newer model', async () => {
  importedSimulationSetup().setChannelDriverPreset('source:high', preset);
  let resolve!: (response: Response) => void;
  const pending = new Promise<Response>(done => { resolve = done; });
  const refresh = refreshChannelDriverBases(() => pending, 'file');
  const next = { ...mesh, ingest_id: 'wgi_other', model_id: 'mesh-file:other' };
  void openImported({ kind: 'file', prepared: next, setup: null });
  resolve(new Response(JSON.stringify({ spec: { re_ohm: 7 } })));
  expect(await refresh).toEqual([]);
  expect(importedSimulationSetup().channelDrivers['source:high']).toBeUndefined();
});

it('defaults to one card per role and keeps each surface on its own channel', () => {
  const prepared = useImportedModelStore.getState().prepared;
  expect(importedSimulationSetup().driveChannels).toEqual([
    { id: 'source:left', source_ids: ['left'], motion: 'normal' },
    { id: 'source:right', source_ids: ['right'], motion: 'normal' },
    { id: 'source:high', source_ids: ['high'], motion: 'normal' },
  ]);
  expect(importedSimulationSetup().driverGroups).toEqual([
    { id: 'group:source%3Aleft', channelIds: ['source:left', 'source:right'] },
    { id: 'group:source%3Ahigh', channelIds: ['source:high'] },
  ]);
  importedSimulationSetup().setRoleSharing!('MF', false);
  expect(importedSimulationSetup().driverGroups).toHaveLength(3);
  importedSimulationSetup().setRoleSharing!('MF', true);
  expect(importedSimulationSetup().driverGroups).toHaveLength(2);
  expect(importedSimulationSetup().driveChannels.map(channel => channel.source_ids)).toEqual([['left'], ['right'], ['high']]);
  expect(useImportedModelStore.getState().prepared).toBe(prepared);
});

it('sends the same driver and voltages as CAD and saves library references with the setup', async () => {
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ revision_id: 'wgs_driver' })));
  stopPublisher = startCadSetupPublisher({ fetcher });
  const state = importedSimulationSetup();
  state.setChannelDriverPreset('source:high', preset);
  state.setDriveVoltage(4.2); state.setMaxDriveVoltage(18);
  const setup = fileMeshSetup();
  expect(setup.geometry.drive_channels.find(channel => channel.id === 'source:high')!.driver).toMatchObject({ label: 'Woofer', ...preset.base });
  expect(setup.geometry).toMatchObject({ drive_voltage_v: 4.2, max_drive_voltage_v: 18 });
  expect(setup.driver_references).toEqual({ 'source:high': { driver_id: preset.id, source: 'database' } });
  useCadReturnStore.setState({ driveChannels: importedSimulationSetup().driveChannels,
    channelDrivers: importedSimulationSetup().channelDrivers, driveVoltageV: 4.2, maxDriveVoltageV: 18 });
  expect(simulationDriverGeometry(useCadReturnStore.getState())).toEqual(simulationDriverGeometry(importedSimulationSetup()));
  await flushImportedSetupWrites();
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toBe(`/api/models/${encodeURIComponent(mesh.model_id)}/setup`);
  expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string).setup.driver_references).toEqual(setup.driver_references);
});

it('restores a frozen file driver for editing and preserves the other retained physics', () => {
  importedSimulationSetup().setChannelDriverPreset('source:high', preset);
  const setup = fileMeshSetup();
  show(setup);
  expect(importedSimulationSetup().channelDrivers['source:high'].frozen).toBe(true);
  importedSimulationSetup().setDriveVoltage(7);
  expect(fileMeshSetup().geometry.drive_voltage_v).toBe(7);
  expect(fileMeshSetup().geometry.drive_channels).toEqual(setup.geometry.drive_channels);
  importedSimulationSetup().setChannelDriverPreset('source:high', null);
  expect(fileMeshSetup().geometry.drive_channels.find(channel => channel.id === 'source:high')!).not.toHaveProperty('driver');
});

it('blocks incomplete file drivers instead of silently solving unit-driven', () => {
  importedSimulationSetup().setChannelDriverPreset('source:high', { ...preset, base: {} });
  expect(() => fileMeshSetup()).toThrow('HF driver still needs');
});

it('opens old motion-only per-source setups and solves their exact request before and after unrelated edits', () => {
  const options = fileMeshSetup().options;
  const setup: ImportedSetup = { geometry: { skipped_source_ids: [], drive_channels: [
    { id: 'old-left', source_ids: ['left'], motion: 'normal' },
    { id: 'old-right', source_ids: ['right'], motion: 'axial' },
    { id: 'old-high', source_ids: ['high'], motion: 'normal' },
  ] }, options };
  show(setup);
  expect(importedSimulationSetup().driveChannels).toEqual(setup.geometry.drive_channels);
  expect(fileMeshSubmission()).toEqual(bindImportedSetup(mesh, setup));
  importedSimulationSetup().setDriveVoltage(5);
  expect(fileMeshSubmission()).toEqual(bindImportedSetup(mesh, setup));
});

it('migrates encoded legacy membership on save without renaming its physical channels', async () => {
  const legacy: ImportedSetup = { geometry: { skipped_source_ids: [], drive_channels: [
    { id: 'drive-mf::left', source_ids: ['left'], motion: 'normal' },
    { id: 'drive-mf::right', source_ids: ['right'], motion: 'normal' },
    { id: 'drive-hf', source_ids: ['high'], motion: 'normal' },
  ] }, options: fileMeshSetup().options };
  show(legacy);
  expect(fileMeshSubmission()).toEqual(bindImportedSetup(mesh, legacy));
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ revision_id: 'wgs_migrated' })));
  stopPublisher = startCadSetupPublisher({ fetcher });
  importedSimulationSetup().setChannelMotion('drive-mf::left', 'axial');
  await flushImportedSetupWrites();
  const saved = JSON.parse(fetcher.mock.calls[0][1]?.body as string).setup as ImportedSetup;
  expect(saved.geometry.drive_channels.map(channel => channel.id)).toEqual(['drive-mf::left', 'drive-mf::right', 'drive-hf']);
  expect(saved.geometry.driver_groups).toEqual([
    { id: 'drive-mf', channel_ids: ['drive-mf::left', 'drive-mf::right'] },
    { id: 'drive-hf', channel_ids: ['drive-hf'] },
  ]);
  show(saved);
  expect(importedSimulationSetup().driverGroups.map(group => group.channelIds)).toEqual([
    ['drive-mf::left', 'drive-mf::right'], ['drive-hf'],
  ]);
  expect(fileMeshSubmission()).toEqual(bindImportedSetup(mesh, saved));
});

it.each(['resistance', 'physical crossover'] as const)(
  'migrates legacy encoded IDs as separate channels when using %s', reason => {
    const legacy: ImportedSetup = { geometry: { skipped_source_ids: [], drive_channels: [
      { id: 'drive-mf::left', source_ids: ['left'], motion: 'normal' },
      { id: 'drive-mf::right', source_ids: ['right'], motion: 'normal' },
      { id: 'drive-hf', source_ids: ['high'], motion: 'normal' },
    ], ...(reason === 'resistance' ? { rg_ohm: 0.5 } : {
      combine: { members: ['drive-mf::left', 'drive-hf'], crossovers_hz: [1200] },
    }) }, options: fileMeshSetup().options };
    show(legacy);
    expect(fileMeshSubmission()).toEqual(bindImportedSetup(mesh, legacy));
    expect(importedSimulationSetup().driverGroups.map(group => group.channelIds)).toEqual([
      ['drive-mf::left'], ['drive-mf::right'], ['drive-hf'],
    ]);
    const saved = fileSetupForSave(fileMeshSetup());
    expect(saved.geometry.driver_groups?.every(group => group.channel_ids.length === 1)).toBe(true);
    expect(saved.geometry.combine).toEqual(legacy.geometry.combine);
    expect(saved.geometry.rg_ohm).toEqual(legacy.geometry.rg_ohm);
    if (reason === 'resistance') {
      importedSimulationSetup().setRoleSharing!('MF', true);
      expect(() => fileMeshSetup()).toThrow('Set generator resistance to 0 ohms or drive the surfaces separately.');
    }
  },
);

it('keeps source IDs and gives a newly split role its own crossover branch', () => {
  importedSimulationSetup().setCombineEnabled(true);
  const saved = fileMeshSetup();
  expect(saved.geometry.combine?.members).toHaveLength(2);
  const changed = { ...mesh, sources: mesh.sources.map(source => source.id === 'right' ? { ...source, role: 'HF' } : source) };
  void openImported({ kind: 'file', prepared: changed, setup: saved });
  importedSimulationSetup().setChannelMotion('source:right', 'axial');
  const next = fileMeshSetup();
  expect(next.geometry.drive_channels.map(channel => channel.id)).toEqual(['source:left', 'source:right', 'source:high']);
  expect(next.geometry.driver_groups).toHaveLength(3);
  expect(next.geometry.combine?.members).toHaveLength(3);
  expect(next.geometry.combine?.members).toEqual(expect.arrayContaining(next.geometry.driver_groups!.map(group => group.id)));
});

it('preserves the saved crossover form on an unrelated group edit', () => {
  importedSimulationSetup().setCombineEnabled(true);
  const saved = fileMeshSetup();
  const members = saved.geometry.combine!.members;
  saved.geometry.combine = { members, crossovers_hz: [1200], level_match: false, align: false };
  show(saved);
  importedSimulationSetup().setChannelMotion('source:high', 'axial');
  expect(fileMeshSetup().geometry.combine).toEqual(saved.geometry.combine);
});

it('reorders crossover branches after their roles swap without renaming channels', () => {
  importedSimulationSetup().setCombineEnabled(true);
  const saved = fileMeshSetup();
  const [mid, high] = saved.geometry.combine!.members;
  const swapped = { ...mesh, sources: mesh.sources.map(source => ({ ...source, role: source.role === 'MF' ? 'HF' : 'MF' })) };
  void openImported({ kind: 'file', prepared: swapped, setup: saved });
  importedSimulationSetup().setChannelMotion('source:high', 'axial');
  const next = fileMeshSetup();
  expect(next.geometry.combine?.members).toEqual([high, mid]);
  expect(next.geometry.drive_channels.map(channel => channel.id)).toEqual(['source:left', 'source:right', 'source:high']);
});

it('keeps file driver state with the file model when switching imported inputs', () => {
  importedSimulationSetup().setChannelDriverPreset('source:high', preset);
  workspaceModeStore.setImportedInput('cad');
  expect(importedSimulationSetup().input).toBe('cad');
  expect(importedSimulationSetup().channelDrivers).toEqual({});
  workspaceModeStore.setImportedInput('file');
  expect(importedSimulationSetup().channelDrivers['source:high'].preset?.id).toBe(preset.id);
});

// Preparation must not discard a valid or unfinished driver the user just entered.
it.each([preset, { ...preset, base: {} }])('retains file driver controls through re-preparation ($base)', (driver) => {
  importedSimulationSetup().setChannelDriverPreset('source:high', driver);
  importedSimulationSetup().setDriveVoltage(6);
  editFileDraft({ units: 'cm' });
  const retained = useImportedModelStore.getState().setupEditBase?.retained ?? null;
  void openImported({ kind: 'file', prepared: mesh, setup: retained });
  expect(importedSimulationSetup().channelDrivers['source:high'].preset?.id).toBe(driver.id);
  expect(importedSimulationSetup().driveVoltageV).toBe(6);
  if (Object.keys(driver.base).length) expect(fileMeshSetup().geometry.drive_channels.find(channel => channel.id === 'source:high')!.driver).toMatchObject(driver.base);
  else expect(() => fileMeshSetup()).toThrow('still needs');
});

it('uses the CAD exterior-driver readiness check for newly configured file drivers', () => {
  importedSimulationSetup().setExteriorTransducer('source:high', { version: 1, motion_axis: [0, 0, 1] });
  expect(() => fileMeshSetup()).toThrow('Choose BEAT CPU or BEAT Metal');
});

it.each([true, false])('keeps every surface after a retained channel spans roles (share: %s)', (share) => {
  const setup = fileMeshSetup();
  delete setup.geometry.driver_groups;
  setup.geometry.drive_channels = [{ id: 'retained-mf', source_ids: ['left', 'right'], motion: 'normal' },
    setup.geometry.drive_channels.find(channel => channel.id === 'source:high')!];
  const changed = { ...mesh, sources: mesh.sources.map(source => source.id === 'right' ? { ...source, role: 'HF' } : source) };
  void openImported({ kind: 'file', prepared: changed, setup });
  importedSimulationSetup().setRoleSharing!('HF', share);
  const channels = importedSimulationSetup().driveChannels;
  expect(channels.flatMap(channel => channel.source_ids).sort()).toEqual(['high', 'left', 'right']);
  expect(new Set(channels.map(channel => channel.id)).size).toBe(channels.length);
  expect(channels.find(channel => channel.source_ids.includes('left'))?.source_ids).toEqual(['left']);
  expect(channels.every(channel => channel.source_ids.length === 1)).toBe(true);
  expect(importedSimulationSetup().driverGroups).toHaveLength(share ? 2 : 3);
});

it('regrouping weighted sources keeps other roles and discards the selected old coordinate', () => {
  const setup = fileMeshSetup();
  delete setup.geometry.driver_groups;
  setup.geometry.drive_channels = [{ id: 'weighted', source_ids: ['left', 'right'], motion: 'normal', physical_source_id: 'diaphragm', patch_weights: { left: .25, right: .75 } },
    setup.geometry.drive_channels.find(channel => channel.id === 'source:high')!];
  const changed = { ...mesh, sources: mesh.sources.map(source => source.id === 'right' ? { ...source, role: 'HF' } : source) };
  void openImported({ kind: 'file', prepared: changed, setup });
  importedSimulationSetup().setRoleSharing!('HF', false);
  const channels = importedSimulationSetup().driveChannels;
  expect(channels.find(channel => channel.source_ids.includes('left'))).toMatchObject({ physical_source_id: 'diaphragm', patch_weights: { left: .25 } });
  for (const channel of channels.filter(channel => !channel.source_ids.includes('left'))) {
    expect(channel).not.toHaveProperty('physical_source_id');
    expect(channel).not.toHaveProperty('patch_weights');
  }
});

it('opens older preparations without role metadata as separate motion-only sources', () => {
  const { sources: _sources, ...old } = mesh;
  void openImported({ kind: 'file', prepared: old });
  expect(importedSimulationSetup().driveChannels.map(channel => ({ id: channel.id, source_ids: channel.source_ids }))).toEqual([
    { id: 'left', source_ids: ['left'] }, { id: 'right', source_ids: ['right'] }, { id: 'high', source_ids: ['high'] },
  ]);
});

it.each([false, true])('never replays a complete driver beneath an incomplete re-prepared form (saved: %s)', (saved) => {
  importedSimulationSetup().setChannelDriverPreset('source:high', preset);
  if (saved) show(fileMeshSetup());
  const prepare = () => {
    editFileDraft({ units: 'cm' });
    const retained = useImportedModelStore.getState().setupEditBase?.retained ?? null;
    void openImported({ kind: 'file', prepared: mesh, setup: retained });
  };
  prepare();
  importedSimulationSetup().setChannelDriverPreset('source:high', { ...preset, base: {} });
  importedSimulationSetup().setDriveVoltage(7);
  prepare();
  expect(() => fileMeshSetup()).toThrow('still needs');
  importedSimulationSetup().setChannelDriverPreset('source:high', preset);
  expect(fileMeshSetup().geometry.drive_channels.find(channel => channel.id === 'source:high')!.driver).toMatchObject(preset.base);
  expect(fileMeshSetup().geometry.drive_voltage_v).toBe(7);
});

it('shared cards fan out presets, overrides, resets, motion and clearing to identical single-source drivers', () => {
  importedSimulationSetup().setChannelDriverPreset('source:left', preset);
  importedSimulationSetup().setChannelDriverField('source:left', 're_ohm', 7);
  const members = () => fileMeshSetup().geometry.drive_channels.filter(channel => ['source:left', 'source:right'].includes(channel.id));
  expect(members().map(channel => channel.source_ids)).toEqual([['left'], ['right']]);
  expect(members()[0].driver).toEqual(members()[1].driver);
  expect(members()[1].driver?.re_ohm).toBe(7);
  importedSimulationSetup().clearChannelDriverOverrides('source:left');
  expect(members()[0].driver).toEqual(members()[1].driver);
  expect(members()[1].driver?.re_ohm).toBe(6);
  importedSimulationSetup().setChannelMotion('source:left', 'axial');
  expect(members().map(channel => channel.motion)).toEqual(['axial', 'axial']);
  expect(members().every(channel => !channel.driver)).toBe(true);
  importedSimulationSetup().setChannelMotion('source:left', 'normal');
  importedSimulationSetup().setChannelDriverPreset('source:left', preset);
  importedSimulationSetup().setChannelDriverPreset('source:left', null);
  expect(members().every(channel => !channel.driver)).toBe(true);
});

it('separate cards retain their values and shared cards explicitly copy the first driver to every member', () => {
  importedSimulationSetup().setChannelDriverPreset('source:left', preset);
  importedSimulationSetup().setRoleSharing!('MF', false);
  importedSimulationSetup().setChannelDriverField('source:right', 're_ohm', 9);
  const setup = fileMeshSetup();
  expect(setup.geometry.drive_channels.find(channel => channel.id === 'source:left')?.driver?.re_ohm).toBe(6);
  expect(setup.geometry.drive_channels.find(channel => channel.id === 'source:right')?.driver?.re_ohm).toBe(9);
  show(setup);
  expect(importedSimulationSetup().driverGroups).toHaveLength(3);
  importedSimulationSetup().setRoleSharing!('MF', true);
  const shared = fileMeshSetup().geometry.drive_channels.filter(channel => ['source:left', 'source:right'].includes(channel.id));
  expect(shared[0].driver).toEqual(shared[1].driver);
  expect(shared[1].driver?.re_ohm).toBe(6);
});

it.each(['normal', 'axial'] as const)('shares the first populated driver when the first separate surface has no driver (%s motion)', (motion) => {
  importedSimulationSetup().setRoleSharing!('MF', false);
  importedSimulationSetup().setChannelMotion('source:left', motion);
  importedSimulationSetup().setChannelDriverPreset('source:right', preset);
  importedSimulationSetup().setRoleSharing!('MF', true);
  const members = fileMeshSetup().geometry.drive_channels.filter(channel => ['source:left', 'source:right'].includes(channel.id));
  expect(members.map(channel => channel.driver)).toEqual([expect.objectContaining(preset.base), expect.objectContaining(preset.base)]);
  expect(members.map(channel => channel.motion)).toEqual(['normal', 'normal']);
  expect(importedSimulationSetup().channelDrivers['source:left']).toEqual(importedSimulationSetup().channelDrivers['source:right']);
});

it('keeps exterior transducers on individual channels even when invoked from a shared card', () => {
  importedSimulationSetup().setExteriorTransducer('source:left', { version: 1, motion_axis: [1, 0, 0] });
  const simulation = importedSimulationSetup();
  expect(simulation.driveChannels.find(channel => channel.id === 'source:left')?.exterior_transducer?.motion_axis).toEqual([1, 0, 0]);
  expect(simulation.driveChannels.find(channel => channel.id === 'source:right')?.exterior_transducer).toBeUndefined();
  expect(simulation.driverGroups.filter(group => group.channelIds.some(id => ['source:left', 'source:right'].includes(id))).map(group => group.channelIds))
    .toEqual([['source:left'], ['source:right']]);
  simulation.setExteriorTransducer('source:right', { version: 1, motion_axis: [0, 1, 0] });
  expect(importedSimulationSetup().driveChannels.find(channel => channel.id === 'source:left')?.exterior_transducer?.motion_axis).toEqual([1, 0, 0]);
  expect(importedSimulationSetup().driveChannels.find(channel => channel.id === 'source:right')?.exterior_transducer?.motion_axis).toEqual([0, 1, 0]);
});

it('saves and reopens a stable shared band key using existing setup channel ids', async () => {
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ revision_id: 'wgs_shared' })));
  stopPublisher = startCadSetupPublisher({ fetcher });
  importedSimulationSetup().setChannelDriverPreset('source:left', preset);
  importedSimulationSetup().setChannelDriverField('source:left', 're_ohm', 7);
  const groups = importedSimulationSetup().driverGroups;
  await flushImportedSetupWrites();
  expect(fetcher).toHaveBeenCalledOnce();
  const saved = JSON.parse(fetcher.mock.calls[0][1]?.body as string).setup as ImportedSetup;
  expect(saved.geometry.drive_channels.every(channel => channel.source_ids.length === 1)).toBe(true);
  expect(saved.driver_references?.['source:right']).toEqual({ driver_id: preset.id, source: 'database' });
  show(saved);
  expect(importedSimulationSetup().driverGroups).toEqual(groups);
  expect(fileMeshSetup()).toEqual(saved);
  importedSimulationSetup().setChannelDriverField('source:left', 're_ohm', 8);
  const channels = fileMeshSubmission().geometry.drive_channels.filter(channel => ['source:left', 'source:right'].includes(channel.id));
  expect(channels[0].driver).toEqual(channels[1].driver);
  expect(channels[1].driver?.re_ohm).toBe(8);
});

it('never merges an old independent channel id with another role group after a role edit', () => {
  const setup = fileMeshSetup();
  delete setup.geometry.driver_groups;
  setup.geometry.drive_channels = [
    { id: 'drive-mf', source_ids: ['high'], motion: 'normal' },
    { id: 'drive-mf::left', source_ids: ['left'], motion: 'normal' },
    { id: 'drive-mf::right', source_ids: ['right'], motion: 'normal' },
  ];
  show(setup);
  expect(importedSimulationSetup().driverGroups).toEqual([
    { id: 'channel:drive-mf', channelIds: ['drive-mf'] },
    { id: 'drive-mf:2', channelIds: ['drive-mf::left', 'drive-mf::right'] },
  ]);
  importedSimulationSetup().setChannelDriverPreset('drive-mf::left', preset);
  const channels = fileMeshSetup().geometry.drive_channels;
  expect(channels.find(channel => channel.id === 'drive-mf')).not.toHaveProperty('driver');
  expect(channels.find(channel => channel.id === 'drive-mf::right')?.driver).toMatchObject(preset.base);
});

it('keeps an old singleton independent when roles swap and later return to an existing shared band', () => {
  importedSimulationSetup().setChannelDriverPreset('source:left', preset);
  importedSimulationSetup().setChannelDriverPreset('source:high', { ...preset, base: { ...preset.base, re_ohm: 9 } });
  const swapped = { ...mesh, sources: mesh.sources.map(source => ({ ...source, role: source.id === 'high' ? 'MF' : 'HF' })) };
  void openImported({ kind: 'file', prepared: swapped, setup: fileMeshSetup() });
  importedSimulationSetup().setRoleSharing!('HF', true);
  expect(importedSimulationSetup().driverGroups.map(group => group.channelIds).sort()).toEqual([['source:high'], ['source:left', 'source:right']].sort());
  const allHigh = { ...mesh, sources: mesh.sources.map(source => ({ ...source, role: 'HF' })) };
  void openImported({ kind: 'file', prepared: allHigh, setup: fileMeshSetup() });
  expect(importedSimulationSetup().driverGroups.map(group => group.channelIds).sort()).toEqual([['source:high'], ['source:left', 'source:right']].sort());
  importedSimulationSetup().setChannelDriverField('source:left', 're_ohm', 7);
  const channels = fileMeshSetup().geometry.drive_channels;
  expect(channels.find(channel => channel.id === 'source:high')?.driver?.re_ohm).toBe(9);
  expect(channels.find(channel => channel.id === 'source:right')?.driver?.re_ohm).toBe(7);
});

it('keeps shared edits together while unprepared and through the next preparation', () => {
  importedSimulationSetup().setChannelDriverPreset('source:left', preset);
  editFileDraft({ units: 'cm' });
  expect(useImportedModelStore.getState().prepared).toBeNull();
  expect(importedSimulationSetup().driverGroups.find(group => group.id === 'group:source%3Aleft')?.channelIds).toEqual(['source:left', 'source:right']);
  importedSimulationSetup().setChannelDriverField('source:left', 're_ohm', 7);
  const retained = useImportedModelStore.getState().setupEditBase?.retained ?? null;
  void openImported({ kind: 'file', prepared: mesh, setup: retained });
  const members = fileMeshSetup().geometry.drive_channels.filter(channel => ['source:left', 'source:right'].includes(channel.id));
  expect(members[0].driver).toEqual(members[1].driver);
  expect(members[1].driver?.re_ohm).toBe(7);
});

it('keeps a suffixed member independent after moving away, editing it, and moving back to its role', () => {
  importedSimulationSetup().setChannelDriverPreset('source:left', preset);
  const changed = { ...mesh, sources: mesh.sources.map(source => source.id === 'right' ? { ...source, role: 'HF' } : source) };
  void openImported({ kind: 'file', prepared: changed, setup: fileMeshSetup() });
  importedSimulationSetup().setChannelDriverField('source:right', 're_ohm', 9);
  show(fileMeshSetup());
  expect(importedSimulationSetup().driverGroups).toHaveLength(3);
  importedSimulationSetup().setChannelDriverField('source:left', 're_ohm', 7);
  let channels = fileMeshSetup().geometry.drive_channels;
  expect(channels.find(channel => channel.id === 'source:left')?.driver?.re_ohm).toBe(7);
  expect(channels.find(channel => channel.id === 'source:right')?.driver?.re_ohm).toBe(9);
  show(fileMeshSetup());
  expect(importedSimulationSetup().driverGroups).toHaveLength(3);
  importedSimulationSetup().setRoleSharing!('MF', true);
  expect(importedSimulationSetup().driverGroups).toHaveLength(2);
  channels = fileMeshSetup().geometry.drive_channels;
  expect(channels.find(channel => channel.id === 'source:left')?.driver).toEqual(channels.find(channel => channel.id === 'source:right')?.driver);
});
