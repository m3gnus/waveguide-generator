/**
 * The design assembly as the third input of the shared imported Simulation.
 *
 * Same sections, same mutations (`createImportedSimulationSetup`), another
 * owner of the state: the design document. Channels are the compiled record's
 * excitations, one per radiator; drivers, signal groups, the crossover and the
 * drive voltage are the design's `assembly`; the sweep is the design's own.
 * Every control action is one project history step, however many channels a
 * grouped card fans out to, so Undo, Redo, New and Open need nothing here.
 */
import type { CompiledAssembly } from '../api/assembly';
import { parseWire, toWire } from '../results/crossoverSpec';
import { HF_RADIATOR_ID, newRadiatorId, type AssemblyDriver, type AssemblySignalGroup, type DesignAssembly } from './assembly';
import { useAssemblyBuildStore } from './assemblyBuild';
import { PASSIVE_CARDIOID_DEFAULTS, type CadDriveChannel, type RoleSource, type SimulationData } from './cadReturn';
import { useDesignStore, type DesignDocument } from './design';
import { parseExteriorTransducer, type ExteriorTransducerForm } from './exteriorTransducer';
import { createImportedSimulationSetup, type ImportedSimulationSetup } from './importedSimulationSetup';
import { DEFAULT_DRIVE_VOLTAGE_V } from './solveDefaults';

/** Assembly diaphragms move along the axial Z direction of the horn frame. */
export const ASSEMBLY_MOTION_AXIS: [number, number, number] = [0, 0, 1];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function exteriorForm(driver: AssemblyDriver | undefined): ExteriorTransducerForm | undefined {
  return driver ? parseExteriorTransducer({ ...driver.exterior_transducer, motion_axis: ASSEMBLY_MOTION_AXIS }) : undefined;
}

/** The record's sources, as the shared sections name them (HF horn, LF woofers). */
export function assemblySources(record: CompiledAssembly | null): RoleSource[] {
  return (record?.sources ?? []).map(({ id, role }) => ({ id, role }));
}

/** Simulation data of a design assembly and the compiled record it is solved on. */
export function assemblySimulationData(design: DesignDocument, record: CompiledAssembly | null): SimulationData {
  const assembly = design.assembly ?? {};
  const excitations = record?.excitations ?? [];
  const compiled = new Set(excitations.map(excitation => excitation.id));
  const driveChannels: CadDriveChannel[] = excitations.map(excitation => {
    const form = exteriorForm(assembly.drivers?.[excitation.id]);
    return { id: excitation.id, source_ids: [...excitation.source_ids], motion: excitation.motion,
      ...(form ? { exterior_transducer: form } : {}) };
  });
  const spec = assembly.crossover?.spec;
  return {
    driveChannels, channelDrivers: {},
    driverGroups: (assembly.signal_groups ?? []).flatMap(group => {
      const channelIds = group.members.map(member => member.radiator_id).filter(id => compiled.has(id));
      return channelIds.length ? [{ id: group.id, channelIds, label: group.label }] : [];
    }),
    persistDriverGroups: true,
    passiveCardioid: { ...PASSIVE_CARDIOID_DEFAULTS },
    driveVoltageV: assembly.drive_voltage_v ?? DEFAULT_DRIVE_VOLTAGE_V,
    maxDriveVoltageV: null,
    combineEnabled: !!assembly.crossover?.enabled,
    combineSpec: spec ? parseWire(spec) : null,
    frequencyStartHz: design.simulation.f1, frequencyEndHz: design.simulation.f2, frequencyCount: design.simulation.num_frequencies,
  };
}

/**
 * Fold a Simulation change back into the design assembly, by identity.
 *
 * A radiator the record does not drive (disabled, rigid) keeps its driver and
 * membership; member processing follows its radiator into whatever group it
 * lands in; a group the shared rules mint gets a fresh UUID, and the crossover
 * follows it.
 */
export function applyAssemblySimulation(assembly: DesignAssembly, next: SimulationData,
  newId: () => string = newRadiatorId): DesignAssembly {
  const result = structuredClone(assembly);
  const compiled = new Set(next.driveChannels.map(channel => channel.id));
  const drivers: Record<string, AssemblyDriver> = Object.fromEntries(Object.entries(result.drivers ?? {})
    .filter(([id]) => !compiled.has(id)));
  for (const channel of next.driveChannels) {
    if (!channel.exterior_transducer || channel.id === HF_RADIATOR_ID) continue;
    const { motion_axis: _axis, ...exterior } = channel.exterior_transducer;
    drivers[channel.id] = { exterior_transducer: exterior as AssemblyDriver['exterior_transducer'],
      ...(result.drivers?.[channel.id]?.reference ? { reference: result.drivers[channel.id].reference } : {}) };
  }
  if (Object.keys(drivers).length) result.drivers = drivers; else delete result.drivers;

  const renamed = new Map<string, string>();
  if (next.driverGroups) {
    const previous = result.signal_groups ?? [];
    const member = new Map(previous.flatMap(group => group.members.map(item => [item.radiator_id, item] as const)));
    const placed = new Set(next.driverGroups.flatMap(group => group.channelIds));
    const groups: AssemblySignalGroup[] = next.driverGroups.map(group => {
      const id = UUID.test(group.id) && !renamed.has(group.id) ? group.id : newId();
      renamed.set(group.id, id);
      const old = previous.find(item => item.id === id);
      const kept = old ? old.members.filter(item => !compiled.has(item.radiator_id) && !placed.has(item.radiator_id)) : [];
      return { id, label: group.label || old?.label || 'Group',
        members: [...group.channelIds.map(channelId => member.get(channelId) ?? { radiator_id: channelId }), ...kept] };
    });
    // Radiators outside the record keep the group they were in, or their own.
    for (const group of previous) {
      const orphans = group.members.filter(item => !compiled.has(item.radiator_id)
        && !groups.some(candidate => candidate.members.some(other => other.radiator_id === item.radiator_id)));
      if (orphans.length) groups.push({ ...group, id: groups.some(candidate => candidate.id === group.id) ? newId() : group.id, members: orphans });
    }
    result.signal_groups = groups;
  }

  const spec = next.combineSpec ? toWire(next.combineSpec) : null;
  if (spec) {
    spec.members = spec.members.map(id => renamed.get(id) ?? id);
    spec.reference = renamed.get(spec.reference) ?? spec.reference;
    spec.channels = Object.fromEntries(Object.entries(spec.channels).map(([id, value]) => [renamed.get(id) ?? id, value]));
  }
  if (next.combineEnabled || spec) {
    result.crossover = { enabled: !!next.combineEnabled, ...(spec ? { spec: spec as unknown as Record<string, unknown> } : {}) };
  } else delete result.crossover;
  if (next.driveVoltageV === DEFAULT_DRIVE_VOLTAGE_V) delete result.drive_voltage_v;
  else result.drive_voltage_v = next.driveVoltageV;
  return result;
}

let batch: SimulationData | null = null;

function record(): CompiledAssembly | null {
  return useAssemblyBuildStore.getState().published?.record ?? null;
}

function current(): SimulationData {
  return batch ?? assemblySimulationData(useDesignStore.getState().design, record());
}

function commit(next: SimulationData): void {
  const { design } = useDesignStore.getState();
  if (!design.assembly) return;
  const assembly = applyAssemblySimulation(design.assembly, next);
  if (JSON.stringify(assembly) !== JSON.stringify(design.assembly)) {
    useDesignStore.getState().commitEmbedded({ assembly }, useDesignStore.getState().dragSnapshot ? 'drag' : 'step');
  }
}

/** One control action is one design edit, however many channels it reaches. */
function batched<T extends object>(actions: T): T {
  return Object.fromEntries(Object.entries(actions).map(([name, action]) => [name,
    typeof action !== 'function' ? action : (...args: unknown[]) => {
      if (batch) return (action as (...values: unknown[]) => unknown)(...args);
      batch = current();
      try {
        const value = (action as (...values: unknown[]) => unknown)(...args);
        commit(batch);
        return value;
      } finally { batch = null; }
    }])) as T;
}

export const assemblySimulationSetup = createImportedSimulationSetup({
  input: 'assembly',
  read: current,
  write: update => {
    const state = current();
    const patch = typeof update === 'function' ? update(state) : update;
    if (patch === state) return;
    const next = { ...state, ...patch };
    if (batch) batch = next; else commit(next);
  },
  sources: () => assemblySources(record()),
  skippedSourceIds: () => [],
  setSweep: update => {
    const values: Record<string, number> = {};
    if (update.frequencyStartHz !== undefined) values['simulation.f1'] = update.frequencyStartHz;
    if (update.frequencyEndHz !== undefined) values['simulation.f2'] = update.frequencyEndHz;
    if (update.frequencyCount !== undefined) values['simulation.num_frequencies'] = update.frequencyCount;
    if (Object.keys(values).length) useDesignStore.getState().updateValues(values);
  },
  signal: batched,
});

export function assemblyImportedSetup(): ImportedSimulationSetup {
  return assemblySimulationSetup.get();
}
