import { beforeEach, describe, expect, it } from 'vitest';
import type { RegisteredMesh } from '../api/meshImport';
import { PASSIVE_CARDIOID_DEFAULTS, passiveCardioidBlocker, resetCadReturnStore, useCadReturnStore,
  type ChannelDriverForm, type PassiveCardioidForm, type PassiveCardioidTopology, type SimulationData } from '../stores/cadReturn';
import { resetImportedModelStore, useImportedModelStore } from '../stores/importedModel';
import { importedSimulationSetup, seedFileSimulation } from '../stores/importedSimulationSetup';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { fileSetupControls } from '../stores/meshImport';
import { importedSubmissionBlocker } from './importedSubmission';
import type { ImportedSetup } from './importedSetup';

const form: PassiveCardioidForm = { ...PASSIVE_CARDIOID_DEFAULTS, enabled: true, rearVolumeL: 6,
  portLengthMm: 25, modelPortAreaM2: 0.05, bemPortAreaM2: 0.01, foamResistancePaSM3: 1000 };
const driver: ChannelDriverForm = { preset: null, fields: { sd_cm2: 100, bl_t_m: 10, re_ohm: 6, mms_g: 20, fs_hz: 50 } };
const channels = [
  { id: 'mid', source_ids: ['mid'], motion: 'normal' as const },
  { id: 'left', source_ids: ['left'], motion: 'normal' as const },
  { id: 'right', source_ids: ['right'], motion: 'normal' as const },
];
const topology: PassiveCardioidTopology = {
  sources: [{ id: 'mid', role: 'MF' }, { id: 'left', role: 'PASSIVE_CARDIOID' }, { id: 'right', role: 'PASSIVE_CARDIOID' }],
  sourceTags: { mid: 1, left: 2, right: 3 }, skippedSourceIds: [],
};

describe.each(['cad', 'file'] as const)('%s cardioid topology', input => {
  beforeEach(() => {
    workspaceModeStore.setImportedInput(input);
    resetCadReturnStore(); resetImportedModelStore(); resetSolveOptionsStore();
    const record = { ingest_id: 'wgi_test', manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact',
      source_tags: topology.sourceTags, sources: topology.sources, skipped_source_ids: [], findings: [],
      evidence: { fem_air_volumes: [] }, mesh_sizes: { rigid_size_mm: 5, transition_mm: 3, source_size_mm: {} } };
    if (input === 'cad') {
      useCadReturnStore.setState({ selectedBundle: { sources: topology.sources } as never, ingestRecord: record as never,
        driveChannels: channels, passiveCardioid: form, channelDrivers: {}, needsIngest: false, combineEnabled: false });
    } else {
      const file = { ...record, producer: 'mesh-file' } as unknown as RegisteredMesh;
      useImportedModelStore.setState({ input: 'file', phase: 'ready', prepared: file,
        fileSimulation: { ...seedFileSimulation(file, null), driveChannels: channels, passiveCardioid: form } });
    }
  });

  const check = (value: PassiveCardioidTopology, coupled = false, driveChannels = channels, forms = {}) => {
    const state = importedSimulationSetup();
    return passiveCardioidBlocker({ ...state, passiveCardioid: { ...form, coupled }, driveChannels, channelDrivers: forms }, value);
  };

  it('counts only active tagged sources, including the legacy MF fallback', () => {
    const base = importedSimulationSetup().passiveTopology!;
    expect(check(base)).toBeNull();
    const extra = { ...base, sources: [...base.sources, { id: 'extra', role: 'MF' }] };
    expect(check(extra)).toBeNull(); // No physical tag: the server ignores this role.
    expect(check({ ...extra, sourceTags: { ...base.sourceTags!, extra: 4 } })).toContain('exactly one mid-frequency');
    expect(check({ ...extra, sourceTags: { ...base.sourceTags!, extra: 4 }, skippedSourceIds: ['extra'] })).toBeNull();
    expect(check({ ...base, skippedSourceIds: ['mid'] })).toContain('exactly one mid-frequency');
    expect(check({ ...base, sourceTags: null })).toContain('Prepare the model again');
    expect(check({ ...base, sourceTags: { mid: 1 }, skippedSourceIds: [] })).toContain('Add a port');
    expect(check({ ...base, sourceTags: { mid: 1, left: 1, right: 3 } })).toContain('separate surfaces');
    expect(check({ sources: [{ id: 'MF', role: 'HF' }, { id: 'PORT_EXIT_L', role: 'HF' }],
      sourceTags: { MF: 8, PORT_EXIT_L: 9 }, skippedSourceIds: [] })).toBeNull();
  });

  it('requires a complete mid driver and one channel for all coupled ports', () => {
    const base = importedSimulationSetup().passiveTopology!;
    expect(check(base, true)).toContain('Add a complete driver');
    expect(check(base, true, channels, { mid: driver })).toContain('all port openings');
    const joined = [channels[0], { id: 'ports', source_ids: ['left', 'right'], motion: 'normal' as const }];
    expect(check(base, true, joined, { mid: driver })).toBeNull();
    expect(check({ ...base, skippedSourceIds: ['right'] }, true, channels, { mid: driver })).toBeNull();
  });

  it('reads skipped openings from the input setup', () => {
    if (input === 'cad') useCadReturnStore.setState({ skippedSourceIds: ['right'] });
    else useImportedModelStore.setState({ setup: { geometry: { skipped_source_ids: ['right'] } } as ImportedSetup });
    const active = importedSimulationSetup().passiveTopology!;
    expect(active.skippedSourceIds).toEqual(['right']);
    expect(check(active, true, channels, { mid: driver })).toBeNull();
  });

  it('checks the coupled requirements on the actual submission path', () => {
    const update = (patch: Partial<SimulationData>) => {
      if (input === 'cad') useCadReturnStore.setState(patch);
      else {
        const current = useImportedModelStore.getState().fileSimulation!;
        useImportedModelStore.setState({ fileSimulation: { ...current, ...patch } });
      }
    };
    const refusal = () => {
      if (input === 'cad') return importedSubmissionBlocker();
      try { fileSetupControls(); return null; } catch (error) { return (error as Error).message; }
    };
    update({ passiveCardioid: { ...form, coupled: true } });
    expect(refusal()).toContain('Add a complete driver');
    update({ channelDrivers: { mid: driver } });
    expect(refusal()).toContain('all port openings');
    update({ driveChannels: [channels[0], { id: 'ports', source_ids: ['left', 'right'], motion: 'normal' }] });
    expect(refusal()).toBeNull();
  });

  it('blocks a solve through the shared submission path when there are two tagged mid sources', () => {
    const state = importedSimulationSetup();
    state.setPassiveCardioid({ ...form, coupled: false });
    const current = importedSimulationSetup();
    const extra = { id: 'other-mid', role: 'MF' };
    if (input === 'cad') {
      const record = useCadReturnStore.getState().ingestRecord!;
      useCadReturnStore.setState({ ingestRecord: { ...record, sources: [...record.sources, extra as never],
        source_tags: { ...record.source_tags, 'other-mid': 4 } } });
      expect(importedSubmissionBlocker()).toContain('exactly one mid-frequency');
    } else {
      const file = useImportedModelStore.getState().prepared as RegisteredMesh;
      useImportedModelStore.setState({ prepared: { ...file, sources: [...file.sources!, extra],
        source_tags: { ...file.source_tags, 'other-mid': 4 } } as RegisteredMesh });
      expect(() => fileSetupControls()).toThrow('exactly one mid-frequency');
    }
    expect(current.passiveCardioid.enabled).toBe(true);
  });
});
