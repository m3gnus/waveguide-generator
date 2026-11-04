import { beforeEach, describe, expect, it } from 'vitest';
import type { CompiledAssembly } from '../api/assembly';
import { hydrateDesignDocument } from '../api/designIo';
import { resolveAssemblySetup } from '../jobs/importedSubmission';
import { addWoofer, commitAssembly, emptyAssembly, moveToGroup, setDriver, setMemberProcessing, setWiringGroup, setWooferEnabled, type DesignAssembly } from './assembly';
import { resetAssemblyBuild } from './assemblyBuild';
import { adoptDesignBuild } from './importedModel';
import { combineBaseSpec, combineEnabledEffective, combineWire } from './cadReturn';
import { applyAssemblySimulation, assemblyImportedSetup, assemblySimulationData } from './assemblySimulation';
import { currentDocumentLoad, designForFamily, resetDesignStore, serializeDesign, useDesignStore } from './design';

let counter = 0;
const ids = () => `00000000-0000-4000-8000-${String(counter += 1).padStart(12, '0')}`;
const DRIVER = { version: 1, re_ohm: 6, le_h: 0.0005, bl_n_per_a: 7, mmd_kg: 0.02, cms_m_per_n: 0.001, rms_n_s_per_m: 1.5 };

// Saved two-group assembly from before crossovers were persisted. Keep this
// independent of addWoofer, whose creation defaults must not migrate old files.
const savedAssembly: DesignAssembly = {
  hf_source: { radiator_id: 'horn-throat', kind: 'classic' },
  woofers: [{ id: '00000000-0000-4000-8000-000000000002', label: 'Woofer 1', enabled: true,
    radius_mm: 80, shape: { kind: 'piston' }, placement: { anchor: 'below', gap_mm: 10 }, drive: 'prescribed' }],
  signal_groups: [
    { id: '00000000-0000-4000-8000-000000000001', label: 'HF', members: [{ radiator_id: 'horn-throat' }] },
    { id: '00000000-0000-4000-8000-000000000003', label: 'LF', members: [{ radiator_id: '00000000-0000-4000-8000-000000000002' }] },
  ],
};

function compiledFor(woofers: string[]): CompiledAssembly {
  return {
    ingest_id: 'wgi_1', model_id: 'design-assembly:wgu_x', producer: 'design-assembly', manifest_sha256: 'm', artifact_sha256: 'a',
    msh_text: '$MeshFormat', design: { design_uid: 'wgu_x', name: 'x', revision: 1, content_sha256: 'c', assembly_sha256: 'a', geometry_sha256: 'g' },
    radiators: [], signal_groups: [], source_tags: {},
    sources: [{ id: 'horn-throat', role: 'HF', radiator_id: 'horn-throat', segment_id: null },
      ...woofers.flatMap(id => ['cap', 'cone'].map(segment => ({ id: `${id}/${segment}`, role: 'LF', radiator_id: id, segment_id: segment })))],
    excitations: [{ id: 'horn-throat', source_ids: ['horn-throat'], motion: 'normal', patch_weights: { 'horn-throat': 1 } },
      ...woofers.map(id => ({ id, source_ids: [`${id}/cap`, `${id}/cone`], motion: 'axial' as const, motion_axis: [0, 0, 1] as [number, number, number], patch_weights: { [`${id}/cap`]: 1, [`${id}/cone`]: 1 } }))],
  };
}

function setup(): { first: string; second: string } {
  const one = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
  const two = addWoofer(one.assembly, { anchor: 'above' }, ids);
  const design = { ...designForFamily('R-OSSE'), assembly: two.assembly };
  useDesignStore.getState().replaceDocument(design);
  adoptDesignBuild({ record: compiledFor([one.id, two.id]),
    model: { model_id: 'design-assembly:wgu_x', producer: 'design-assembly', display_name: 'x' } },
    { designUid: 'wgu_x', revision: 1, contentKey: null, load: currentDocumentLoad() });
  return { first: one.id, second: two.id };
}

describe('assembly as the shared Simulation input', () => {
  beforeEach(() => { counter = 0; resetDesignStore(); resetAssemblyBuild(); });

  it('projects passive wiring into the saved setup and refuses unsupported engines and partial networks', () => {
    const { first, second } = setup();
    let assembly = useDesignStore.getState().design.assembly!;
    const target = assembly.signal_groups!.find(group => group.members[0].radiator_id === first)!.id;
    assembly = moveToGroup(setDriver(setDriver(assembly, first, { exterior_transducer: DRIVER }), second, { exterior_transducer: DRIVER }), second, target, ids);
    assembly = setWiringGroup(assembly, { id: ids(), label: 'LF', network: { kind: 'series', children: [
      { kind: 'driver', radiator_id: first }, { kind: 'driver', radiator_id: second }, { kind: 'R', value: 1 },
    ] } });
    const design = { ...useDesignStore.getState().design, assembly };
    const record = compiledFor([first, second]);
    const data = assemblySimulationData(design, record);
    const state = { ...data, sources: record.sources };
    const resolved = resolveAssemblySetup(state, assembly, { engine: 'beat-cpu' } as never, design.simulation);
    expect(resolved.setup.geometry.wiring_groups).toEqual(assembly.wiring_groups);
    expect(applyAssemblySimulation(assembly, data).wiring_groups).toEqual(assembly.wiring_groups);
    for (const engine of ['auto', 'metal', 'bempp']) {
      expect(() => resolveAssemblySetup(state, assembly, { engine } as never, design.simulation)).toThrow('Wired woofers need BEAT CPU or BEAT Metal');
    }
    expect(() => resolveAssemblySetup({ ...state, driveChannels: state.driveChannels.filter(channel => channel.id !== first) },
      assembly, { engine: 'beat-metal' } as never, design.simulation)).toThrow(/disabled or rigid/);
  });

  it('reads one channel per compiled radiator, groups by identity and the design sweep', () => {
    const { first, second } = setup();
    const state = assemblyImportedSetup();
    expect(state.input).toBe('assembly');
    expect(state.driveChannels.map(channel => [channel.id, channel.motion])).toEqual([['horn-throat', 'normal'], [first, 'axial'], [second, 'axial']]);
    expect(state.driverGroups.map(group => group.channelIds)).toEqual([['horn-throat'], [first], [second]]);
    expect(state.sources.map(source => source.role)).toEqual(['HF', 'LF', 'LF', 'LF', 'LF']);
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

  it.each(['omitted', 'off'] as const)('keeps a saved %s crossover off in serialized solves before and after save/reopen', choice => {
    const assembly = structuredClone(savedAssembly);
    if (choice === 'off') assembly.crossover = { enabled: false };
    const design = { ...designForFamily('R-OSSE'), assembly };
    useDesignStore.getState().replaceDocument(design);
    const reopened = hydrateDesignDocument(JSON.parse(JSON.stringify(serializeDesign(design))));
    for (const document of [design, reopened]) {
      const state = assemblySimulationData(document, compiledFor([assembly.woofers![0].id]));
      expect(state.combineEnabled).toBe(false);
      const request = resolveAssemblySetup({ ...state, sources: compiledFor([assembly.woofers![0].id]).sources },
        document.assembly!, { engine: 'metal' } as never, document.simulation);
      expect(JSON.parse(JSON.stringify(request.setup)).geometry).not.toHaveProperty('combine');
      expect(document.assembly?.crossover).toEqual(assembly.crossover);
    }
    assemblyImportedSetup().setExteriorTransducer(assembly.woofers![0].id, { ...DRIVER, motion_axis: [0, 0, 1] });
    expect(combineWire(assemblyImportedSetup())).toBeUndefined();
  });

  it('opts the first woofer into the shared CAD default as one undoable creation', () => {
    const before = structuredClone(useDesignStore.getState().design);
    const added = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    expect(added.assembly.crossover).toEqual({ enabled: true });
    commitAssembly(added.assembly);
    const design = useDesignStore.getState().design;
    const state = { ...assemblySimulationData(design, compiledFor([added.id])), sources: compiledFor([added.id]).sources };
    const cad = { ...state, input: 'cad', combineEnabled: null };
    const request = resolveAssemblySetup(state, design.assembly!, { engine: 'metal' } as never, design.simulation);
    expect(request.setup.geometry.combine).toEqual(combineWire(cad));
    expect(request.setup.geometry.combine?.members).toEqual([added.assembly.signal_groups![1].id, added.assembly.signal_groups![0].id]);
    expect(JSON.parse(JSON.stringify(serializeDesign(design))).assembly.crossover).toEqual({ enabled: true });
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design).toEqual(before);
    expect(useDesignStore.getState().design.assembly?.crossover).toBeUndefined();
  });

  it('keeps an explicit off choice across edits, save/reopen and undo', () => {
    const { first, second } = setup();
    assemblyImportedSetup().setCombineEnabled(false);
    expect(useDesignStore.getState().design.assembly?.crossover).toEqual({ enabled: false });
    expect(combineWire(assemblyImportedSetup())).toBeUndefined();
    const saved = JSON.parse(JSON.stringify(useDesignStore.getState().design));
    const reopened = assemblySimulationData(saved, compiledFor([first, second]));
    expect(combineEnabledEffective(reopened)).toBe(false);
    assemblyImportedSetup().setExteriorTransducer(first, { ...DRIVER, motion_axis: [0, 0, 1] });
    expect(combineWire(assemblyImportedSetup())).toBeUndefined();
    useDesignStore.getState().undo();
    useDesignStore.getState().undo();
    expect(combineEnabledEffective(assemblyImportedSetup())).toBe(true);
    assemblyImportedSetup().setCombineEnabled(true);
    expect(useDesignStore.getState().design.assembly?.crossover?.enabled).toBe(true);
  });

  it('keeps an explicit on choice and custom values in serialized solves after save/reopen', () => {
    setup();
    const spec = combineBaseSpec(assemblyImportedSetup())!;
    spec.channels[spec.members[0]].invert = true;
    spec.channels[spec.members[0]].gain = { mode: 'manual', db: -3 };
    spec.channels[spec.members[0]].delay = { mode: 'manual', ms: 0.75 };
    spec.channels[spec.members[0]].lp = { family: 'butterworth', order: 3, fcHz: 725 };
    assemblyImportedSetup().setCombineSpec(spec);
    const saved = hydrateDesignDocument(JSON.parse(JSON.stringify(serializeDesign(useDesignStore.getState().design))));
    expect(saved.assembly?.crossover?.enabled).toBe(true);
    const reopened = assemblySimulationData(saved, compiledFor(saved.assembly!.woofers!.map(woofer => woofer.id)));
    const request = resolveAssemblySetup({ ...reopened, sources: assemblyImportedSetup().sources }, saved.assembly!, { engine: 'metal' } as never, saved.simulation);
    expect(JSON.parse(JSON.stringify(request.setup)).geometry.combine).toEqual(combineWire(assemblyImportedSetup()));
    expect(request.setup.geometry.combine?.channels?.[spec.members[0]]).toMatchObject({
      invert: true, gain: { mode: 'manual', db: -3 }, delay: { mode: 'manual', ms: 0.75 },
      lp: { family: 'butterworth', order: 3, fc_hz: 725 },
    });
  });

  it('has no combination with just the horn or one shared signal group', () => {
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: emptyAssembly(ids) });
    expect(combineWire(assemblyImportedSetup())).toBeUndefined();
    setup();
    const state = assemblyImportedSetup();
    const single = { ...state, driverGroups: [{ id: 'one', channelIds: state.driveChannels.map(channel => channel.id) }] };
    expect(combineWire(single)).toBeUndefined();
    const design = useDesignStore.getState().design;
    expect(JSON.parse(JSON.stringify(resolveAssemblySetup(single, design.assembly!, { engine: 'metal' } as never, design.simulation).setup)).geometry)
      .not.toHaveProperty('combine');
  });

  it('saves accepted Mmd provenance and undoes both the value and derivation in one step', () => {
    const { first } = setup();
    const derived = { method: 'mms_free_air' as const, mms_g: 17.9, sd_cm2: 124.7, mmd_kg: .0162940347065,
      correction_kg: .0016059652935, correction_fraction: .08971873148, rho_kg_m3: 1.2041 };
    const incomplete = { ...DRIVER, mmd_kg: undefined, motion_axis: [0, 0, 1] as [number, number, number] };
    assemblyImportedSetup().setExteriorTransducer(first, incomplete, { mms_g: 17.9, driver_id: 'library:rs180' });
    const before = structuredClone(useDesignStore.getState().design);
    assemblyImportedSetup().setExteriorTransducer(first, { ...incomplete, mmd_kg: derived.mmd_kg },
      { mms_g: 17.9, driver_id: 'library:rs180', derived_mmd: derived });
    const saved = JSON.parse(JSON.stringify(useDesignStore.getState().design));
    const reopened = assemblySimulationData(saved, compiledFor([first]));
    expect(reopened.driveChannels.find(channel => channel.id === first)?.exterior_driver_inputs?.derived_mmd).toEqual(derived);
    const geometry = resolveAssemblySetup(assemblyImportedSetup(), saved.assembly, { engine: 'beat-cpu', polar_config: {} } as never, saved.simulation).setup.geometry;
    const channel = geometry.drive_channels.find(channel => channel.id === first)!;
    expect(channel.exterior_transducer?.mmd_kg).toBe(derived.mmd_kg);
    expect(channel.exterior_transducer).not.toHaveProperty('mms_g');
    expect(channel.exterior_transducer).not.toHaveProperty('derived_mmd');
    expect(channel.exterior_driver_inputs?.derived_mmd).toEqual(derived);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design).toEqual(before);
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
    expect(channels.find(channel => channel.id === first)?.source_ids).toEqual([`${first}/cap`, `${first}/cone`]);
    expect(channels.find(channel => channel.id === first)?.exterior_transducer?.motion_axis).toEqual([0, 0, 1]);
    expect(coupled.setup.geometry.drive_voltage_v).toBe(2.83);
    expect(coupled.setup.geometry.driver_groups?.find(group => group.channel_ids.includes(second))?.member_processing)
      .toEqual({ [second]: { weight: 0.5, delay_ms: 0.25, invert: true } });
    expect(coupled.setup.options).toMatchObject({ frequency_range: [design.simulation.f1, design.simulation.f2], solver_mode: 'full_3d' });
    const prescribed = resolveAssemblySetup(assemblyImportedSetup(), design.assembly!, { engine: 'metal', polar_config: {} } as never, design.simulation);
    expect(prescribed.notice).toMatch(/fixed velocity \(1 m\/s RMS\).*BEAT CPU or BEAT Metal/);
    expect(prescribed.setup.geometry.drive_channels.some(channel => channel.exterior_transducer)).toBe(false);
    // The driver stays bound to its radiator in the design either way.
    expect(useDesignStore.getState().design.assembly!.drivers?.[first]).toBeDefined();
  });
});
