import { beforeEach, describe, expect, it } from 'vitest';
import type { CompiledAssembly } from '../api/assembly';
import { resolveAssemblySetup } from '../jobs/importedSubmission';
import { addWoofer, commitAssembly, emptyAssembly, setMemberProcessing, setWooferEnabled } from './assembly';
import { resetAssemblyBuild, useAssemblyBuildStore } from './assemblyBuild';
import { applyAssemblySimulation, assemblyImportedSetup, assemblySimulationData } from './assemblySimulation';
import { designForFamily, resetDesignStore, useDesignStore } from './design';

let counter = 0;
const ids = () => `00000000-0000-4000-8000-${String(counter += 1).padStart(12, '0')}`;
const DRIVER = { version: 1, re_ohm: 6, le_h: 0.0005, bl_n_per_a: 7, mmd_kg: 0.02, cms_m_per_n: 0.001, rms_n_s_per_m: 1.5 };

function compiledFor(woofers: string[]): CompiledAssembly {
  return {
    ingest_id: 'wgi_1', model_id: 'design-assembly:wgu_x', producer: 'design-assembly', manifest_sha256: 'm', artifact_sha256: 'a',
    msh_text: '$MeshFormat', design: { design_uid: 'wgu_x', name: 'x', revision: 1, content_sha256: 'c', assembly_sha256: 'a', geometry_sha256: 'g' },
    radiators: [], signal_groups: [], source_tags: {},
    sources: [{ id: 'horn-throat', role: 'HF', radiator_id: 'horn-throat', segment_id: null },
      ...woofers.map(id => ({ id: `${id}/piston`, role: 'LF', radiator_id: id, segment_id: 'piston' }))],
    excitations: [{ id: 'horn-throat', source_ids: ['horn-throat'], motion: 'normal', patch_weights: { 'horn-throat': 1 } },
      ...woofers.map(id => ({ id, source_ids: [`${id}/piston`], motion: 'axial' as const, motion_axis: [0, 0, 1] as [number, number, number], patch_weights: { [`${id}/piston`]: 1 } }))],
  };
}

function setup(): { first: string; second: string } {
  const one = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
  const two = addWoofer(one.assembly, { anchor: 'above' }, ids);
  const design = { ...designForFamily('R-OSSE'), assembly: two.assembly };
  useDesignStore.getState().replaceDocument(design);
  useAssemblyBuildStore.setState({ status: 'published', published: { revision: 1, contentKey: null,
    record: compiledFor([one.id, two.id]), model: { model_id: 'design-assembly:wgu_x', producer: 'design-assembly', display_name: 'x' } } });
  return { first: one.id, second: two.id };
}

describe('assembly as the shared Simulation input', () => {
  beforeEach(() => { counter = 0; resetDesignStore(); resetAssemblyBuild(); });

  it('reads one channel per compiled radiator, groups by identity and the design sweep', () => {
    const { first, second } = setup();
    const state = assemblyImportedSetup();
    expect(state.input).toBe('assembly');
    expect(state.driveChannels.map(channel => [channel.id, channel.motion])).toEqual([['horn-throat', 'normal'], [first, 'axial'], [second, 'axial']]);
    expect(state.driverGroups.map(group => group.channelIds)).toEqual([['horn-throat'], [first], [second]]);
    expect(state.sources.map(source => source.role)).toEqual(['HF', 'LF', 'LF']);
    expect(state.frequencyStartHz).toBe(useDesignStore.getState().design.simulation.f1);
  });

  it('binds a driver to its radiator and makes each control action one history step', () => {
    const { first } = setup();
    const before = structuredClone(useDesignStore.getState().design.assembly);
    assemblyImportedSetup().setExteriorTransducer(first, { ...DRIVER, motion_axis: [0, 0, 1] });
    const assembly = useDesignStore.getState().design.assembly!;
    expect(assembly.drivers?.[first].exterior_transducer).toMatchObject(DRIVER);
    expect(assembly.drivers?.[first].exterior_transducer).not.toHaveProperty('motion_axis');
    expect(assemblyImportedSetup().driveChannels[1].exterior_transducer?.motion_axis).toEqual([0, 0, 1]);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.assembly).toEqual(before);
  });

  it('keeps member processing, disabled radiators and the crossover when groups change', () => {
    const { first, second } = setup();
    commitAssembly(setMemberProcessing(useDesignStore.getState().design.assembly!, second, { delay_ms: 0.5, invert: true }));
    const assembly = useDesignStore.getState().design.assembly!;
    const data = assemblySimulationData(useDesignStore.getState().design, compiledFor([first, second]));
    const merged = applyAssemblySimulation(assembly, { ...data, driverGroups: [
      data.driverGroups![0], { id: 'group:new', channelIds: [first, second], label: 'LF' }],
    combineEnabled: true, combineSpec: null }, ids);
    expect(merged.signal_groups).toHaveLength(2);
    const lf = merged.signal_groups![1];
    expect(lf.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(lf.members).toEqual([{ radiator_id: first }, { radiator_id: second, delay_ms: 0.5, invert: true }]);
    // A disabled woofer leaves the record but keeps its group and driver.
    const disabled = setWooferEnabled(assembly, second, false);
    const withDriver = { ...disabled, drivers: { [second]: { exterior_transducer: DRIVER } } };
    const kept = applyAssemblySimulation(withDriver, assemblySimulationData({ ...useDesignStore.getState().design, assembly: withDriver }, compiledFor([first])), ids);
    expect(kept.drivers?.[second]).toEqual({ exterior_transducer: DRIVER });
    expect(kept.signal_groups!.flatMap(group => group.members.map(member => member.radiator_id)).sort())
      .toEqual(['horn-throat', first, second].sort());
  });

  it('writes the sweep into the design', () => {
    setup();
    assemblyImportedSetup().setSweep({ frequencyStartHz: 120, frequencyEndHz: 4000, frequencyCount: 24 });
    expect(useDesignStore.getState().design.simulation).toMatchObject({ f1: 120, f2: 4000, num_frequencies: 24 });
  });
});

describe('assembly solve setup', () => {
  beforeEach(() => { counter = 0; resetDesignStore(); resetAssemblyBuild(); });

  it('negotiates the assembly, carries member processing and falls back to prescribed off BEAT', () => {
    const { first, second } = setup();
    commitAssembly(setMemberProcessing(useDesignStore.getState().design.assembly!, second, { weight: 0.5, delay_ms: 0.25, invert: true }));
    assemblyImportedSetup().setExteriorTransducer(first, { ...DRIVER, motion_axis: [0, 0, 1] });
    const design = useDesignStore.getState().design;
    const options = { engine: 'beat-cpu', polar_config: {} } as never;
    const coupled = resolveAssemblySetup(assemblyImportedSetup(), design.assembly!, options, design.simulation);
    expect(coupled.notice).toBeNull();
    expect(coupled.setup.geometry.required_features).toEqual(['wg-design-assembly-v1']);
    const channels = coupled.setup.geometry.drive_channels;
    expect(channels.find(channel => channel.id === first)?.exterior_transducer?.motion_axis).toEqual([0, 0, 1]);
    expect(coupled.setup.geometry.drive_voltage_v).toBe(2.83);
    expect(coupled.setup.geometry.driver_groups?.find(group => group.channel_ids.includes(second))?.member_processing)
      .toEqual({ [second]: { weight: 0.5, delay_ms: 0.25, invert: true } });
    expect(coupled.setup.options).toMatchObject({ frequency_range: [design.simulation.f1, design.simulation.f2], solver_mode: 'full_3d' });
    const prescribed = resolveAssemblySetup(assemblyImportedSetup(), design.assembly!, { engine: 'metal', polar_config: {} } as never, design.simulation);
    expect(prescribed.notice).toMatch(/prescribed velocity/);
    expect(prescribed.setup.geometry.drive_channels.some(channel => channel.exterior_transducer)).toBe(false);
    // The driver stays bound to its radiator in the design either way.
    expect(useDesignStore.getState().design.assembly!.drivers?.[first]).toBeDefined();
  });
});
