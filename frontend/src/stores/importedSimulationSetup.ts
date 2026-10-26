import { useSyncExternalStore } from 'react';
import type { RegisteredMesh } from '../api/meshImport';
import type { CadSetup } from '../api/jobsSocket';
import type { ImportedSetup } from '../jobs/importedSetup';
import { fromResult, parseWire } from '../results/crossoverSpec';
import { savedChannelDrivers, savedPassiveCardioid } from './cadSetupRestore';
import {
  PASSIVE_CARDIOID_DEFAULTS, channelAcceptsDriver, channelDriverPresent, retainedChannelDrivers, simulationActions, useCadReturnStore,
  type CadDriveChannel, type CadReturnState, type RoleSource, type SimulationActions, type SimulationData,
} from './cadReturn';
import { useImportedModelStore } from './importedModel';
import { useImportedSolveOptionsStore } from './solveOptions';
import { DEFAULT_DRIVE_VOLTAGE_V } from './solveDefaults';
import { withEditSignals } from './solveSettingsEdits';
import { workspaceModeStore } from './workspaceMode';

export interface ImportedSimulationSetup extends SimulationData, SimulationActions {
  input: 'cad' | 'file' | 'assembly';
  sources: readonly (RoleSource & { defaultDriveChannelId?: string })[];
  /** One amplifier/band branch, with one physical driver channel per surface. */
  driverGroups: ImportedDriverGroup[];
  skippedSourceIds: string[];
  setSourceChannel?: CadReturnState['setSourceChannel'];
  setRoleSharing?: (role: string, share: boolean) => void;
  setCombineEnabled: CadReturnState['setCombineEnabled'];
  setCombineSpec: CadReturnState['setCombineSpec'];
}

export interface ImportedDriverGroup {
  /** Persisted in the existing channel ids; F5b uses this as the band branch. */
  id: string;
  channelIds: string[];
}

const roleGroupId = (role: string) => `drive-${role.toLowerCase()}`;
const sharedChannelId = (role: string, sourceId: string) => `${roleGroupId(role)}::${encodeURIComponent(sourceId)}`;

/** A role's shared card still submits one single-source channel per surface.
 * The reserved ::source suffix records membership in the setup's existing
 * channel ids, without adding a field the server's strict schema would reject.
 * Older channel ids remain separate and their requests remain exact. */
export function fileDriverGroups(sources: readonly RoleSource[], channels: readonly CadDriveChannel[], drivers: SimulationData['channelDrivers']): ImportedDriverGroup[] {
  const groups = new Map<string, ImportedDriverGroup>();
  for (const channel of channels) {
    const source = channel.source_ids.length === 1 ? sources.find(source => source.id === channel.source_ids[0]) : undefined;
    // A suffixed id is explicit membership. A bare role id was seeded
    // for one surface and must not silently join a later multi-surface group.
    const suffix = channel.source_ids.length === 1 ? `::${encodeURIComponent(channel.source_ids[0])}` : null;
    const storedKey = suffix && channel.id.endsWith(suffix) ? channel.id.slice(0, -suffix.length) : null;
    const roleKey = source ? roleGroupId(source.role) : storedKey;
    const shared = storedKey?.startsWith('drive-') && storedKey === roleKey
      || source && channel.id === roleKey && sources.filter(other => other.role === source.role).length === 1;
    // Separate channel ids can equal a role key after a role edit. Keep
    // these namespaces distinct so unlike drivers never become one card.
    const id = shared ? roleKey! : `channel:${channel.id}`;
    const group = groups.get(id) ?? { id, channelIds: [] };
    group.channelIds.push(channel.id);
    groups.set(id, group);
  }
  return [...groups.values()].flatMap(group => {
    // A role edit can temporarily detach a suffixed member. If its settings
    // changed independently, returning to the role must not hide that change
    // under a card claiming identical drivers. Explicit Share reunifies them.
    const settings = group.channelIds.map(id => {
      const channel = channels.find(channel => channel.id === id)!;
      const driver = drivers[id];
      // A labelled saved driver without a library reference receives a synthetic
      // per-channel id on restore; it is still the same shared driver.
      const comparable = driver?.frozen && driver.preset?.source === 'manual' && driver.preset.id === `manual:${id}`
        ? { ...driver, preset: { ...driver.preset, id: 'manual' } } : driver;
      return JSON.stringify({ motion: channel.motion, exterior: channel.exterior_transducer, driver: comparable });
    });
    return !(group.channelIds.length > 1 && group.channelIds.some(id => channels.find(channel => channel.id === id)?.exterior_transducer))
      && settings.every(value => value === settings[0]) ? [group]
      : group.channelIds.map(id => ({ id: `channel:${id}`, channelIds: [id] }));
  });
}

/** Reconcile prepared surfaces using the existing role's sharing choice. */
export function reconcileFileChannels<T extends CadDriveChannel>(record: RegisteredMesh, original: T[],
  drivers: SimulationData['channelDrivers'], skipped: readonly string[]): {
    channels: T[]; from: Record<string, string>; renamed: Record<string, string>;
  } {
  const from: Record<string, string> = {};
  const renamed: Record<string, string> = {};
  const sources = fileSources(record);
  const available = new Set(sources.map(source => source.id));
  const channels = original.flatMap(channel => {
    const source_ids = channel.source_ids.filter(id => available.has(id));
    return source_ids.length ? [{ ...channel, source_ids,
      ...(channel.patch_weights ? { patch_weights: Object.fromEntries(source_ids.map(id => [id, channel.patch_weights![id]])) } : {}),
    }] : [];
  });
  const assigned = new Set(channels.flatMap(channel => channel.source_ids));
  const existing = sources.filter(source => assigned.has(source.id));
  const groups = fileDriverGroups(existing, channels, drivers);
  const used = new Set(channels.map(channel => channel.id));
  const unique = (wanted: string) => {
    let id = wanted;
    for (let suffix = 2; used.has(id); suffix += 1) id = `${wanted}-${suffix}`;
    used.add(id);
    return id;
  };
  const added = sources.filter(source => !assigned.has(source.id) && !skipped.includes(source.id));
  for (const role of new Set(added.map(source => source.role))) {
    const newSources = added.filter(source => source.role === role);
    const oldSources = existing.filter(source => source.role === role);
    const oldChannels = channels.filter(channel => channel.source_ids.some(id => oldSources.some(source => source.id === id)));
    const group = groups.find(candidate => candidate.id === roleGroupId(role)
      && candidate.channelIds.length === oldSources.length
      && oldChannels.every(channel => candidate.channelIds.includes(channel.id) && channel.source_ids.length === 1));
    const template = group && channels.find(channel => channel.id === group.channelIds[0]);
    let share = !!record.sources?.length && (!oldSources.length || !!template)
      && !oldChannels.some(channel => channel.exterior_transducer || channel.physical_source_id !== undefined || channel.patch_weights !== undefined);
    const bare = share && oldChannels.find(channel => channel.id === roleGroupId(role));
    const desired = [...newSources.map(source => sharedChannelId(role, source.id)),
      ...(bare ? [sharedChannelId(role, bare.source_ids[0])] : [])];
    if (desired.some(id => used.has(id))) share = false;
    if (share && bare) {
      const id = sharedChannelId(role, bare.source_ids[0]);
      used.delete(bare.id);
      used.add(id);
      renamed[bare.id] = id;
      from[id] = bare.id;
      channels[channels.indexOf(bare)] = { ...bare, id };
    }
    for (const source of newSources) {
      const id = unique(share ? !oldSources.length && newSources.length === 1 ? roleGroupId(role) : sharedChannelId(role, source.id) : source.id);
      if (share && template) from[id] = template.id;
      channels.push({ id, source_ids: [source.id], motion: share && template ? template.motion : 'normal',
        ...(share && template && 'driver' in template ? { driver: structuredClone(template.driver) } : {}) } as T);
    }
  }
  return { channels, from, renamed };
}

/** New files share a driver card per role, with a channel per source patch. */
export function seedFileSimulation(record: RegisteredMesh | null, setup: ImportedSetup | null, previous?: SimulationData | null): SimulationData {
  const sources = fileSources(record);
  let driveChannels = setup ? structuredClone(setup.geometry.drive_channels).map(channel => {
      const { driver, ...base } = channel;
      const projected = { ...base, motion: channel.motion ?? 'normal' as const };
      // Preserve server-valid legacy fields until a control actually changes them.
      return !channelAcceptsDriver(projected) && driver ? { ...projected, driver } : projected;
    })
    : sources.map(source => ({
      id: record?.sources?.length
        ? sources.filter(other => other.role === source.role).length > 1 ? sharedChannelId(source.role, source.id) : roleGroupId(source.role)
        : source.id,
      source_ids: [source.id], motion: 'normal' as const,
    }));
  // Keep live forms and channel choices through preparation, including an
  // unfinished driver which has no wire setup yet.
  let previousDrivers = previous?.channelDrivers;
  if (previous && record) {
    const extended = reconcileFileChannels(record, previous.driveChannels, previous.channelDrivers, setup?.geometry.skipped_source_ids ?? []);
    previousDrivers = { ...retainedChannelDrivers(previous, extended.channels) };
    for (const [id, source] of Object.entries(extended.from)) {
      if (previous.channelDrivers[source]) previousDrivers[id] = structuredClone(previous.channelDrivers[source]);
    }
    if (!setup) driveChannels = extended.channels;
  }
  const geometry = setup?.geometry as unknown as CadSetup | undefined;
  return {
    driveChannels, channelDrivers: previous && previousDrivers
      ? retainedChannelDrivers({ driveChannels, channelDrivers: previousDrivers }, driveChannels)
      : savedChannelDrivers(geometry, driveChannels, setup?.driver_references),
    passiveCardioid: geometry ? savedPassiveCardioid(geometry) : { ...PASSIVE_CARDIOID_DEFAULTS },
    driveVoltageV: previous?.driveVoltageV ?? setup?.geometry.drive_voltage_v ?? DEFAULT_DRIVE_VOLTAGE_V,
    maxDriveVoltageV: previous?.maxDriveVoltageV ?? setup?.geometry.max_drive_voltage_v ?? null,
    combineEnabled: !!geometry?.combine, combineSpec: parseWire(geometry?.combine) ?? fromResult(geometry?.combine),
    frequencyStartHz: 200, frequencyEndHz: 20_000, frequencyCount: 36,
  };
}

function fileSources(record: RegisteredMesh | null): RoleSource[] {
  // Older retained file records predate sources[]. Their source tags were HF.
  return record?.sources ? record.sources.map(({ id, role }) => ({ id, role }))
    : Object.keys(record?.source_tags ?? {}).map(id => ({ id, role: 'HF' }));
}

function fileData(): SimulationData {
  const model = useImportedModelStore.getState();
  return model.fileSimulation ?? seedFileSimulation(model.prepared as RegisteredMesh | null, model.setup ?? model.setupEditBase?.retained ?? null);
}

const groupedActionNames = ['setChannelMotion', 'setChannelDriverField',
  'setChannelDriverPreset', 'clearChannelDriverOverrides'] as const;
type SimulationUpdate = Partial<SimulationData> | ((state: SimulationData) => Partial<SimulationData>);

/** Bind the shared simulation controls to an imported input's own state and sources. */
export function createImportedSimulationSetup(config: {
  input: ImportedSimulationSetup['input'];
  read: () => SimulationData;
  write: (update: SimulationUpdate) => void;
  sources: () => ImportedSimulationSetup['sources'];
  skippedSourceIds: () => string[];
  setSweep: SimulationActions['setSweep'];
  /** CAD already owns its edit signals and source assignment. */
  cadActions?: () => CadReturnState;
  signal?: <T extends object>(actions: T) => T;
}) {
  const singleActions = simulationActions(config.write);
  /** Fan out one card edit through the existing mutations, under one edit signal. */
  function groupedAction<K extends typeof groupedActionNames[number]>(name: K): SimulationActions[K] {
    return ((channelId: string, ...args: unknown[]) => {
      const state = config.read();
      const sources = config.sources();
      const ids = fileDriverGroups(sources, state.driveChannels, state.channelDrivers).find(group => group.channelIds.includes(channelId))?.channelIds ?? [channelId];
      const action = singleActions[name] as (id: string, ...args: unknown[]) => unknown;
      for (const id of ids) action(id, ...args);
    }) as SimulationActions[K];
  }
  const rawActions = {
    ...singleActions,
    ...Object.fromEntries(groupedActionNames.map(name => [name, groupedAction(name)])) as Pick<SimulationActions, typeof groupedActionNames[number]>,
    setSweep: config.setSweep,
    setCombineEnabled: (combineEnabled: boolean) => config.write({ combineEnabled }),
    setCombineSpec: (combineSpec: SimulationData['combineSpec']) => config.write({ combineSpec }),
    setRoleSharing(role: string, share: boolean) {
      const sources = config.sources().filter(source => source.role === role
        && config.read().driveChannels.some(channel => channel.source_ids.includes(source.id)));
      const ids = new Set(sources.map(source => source.id));
      config.write(state => {
        const affected = state.driveChannels.filter(channel => channel.source_ids.some(id => ids.has(id)));
        const template = affected[0];
        if (!template || affected.some(channel => channel.exterior_transducer)) return state;
        const driverTemplate = affected.find(channel => channelDriverPresent(state.channelDrivers[channel.id])) ?? template;
        // A retained channel can span roles after a Geometry edit. Only detach
        // the selected role, preserving every other surface and its coordinate.
        const remaining = state.driveChannels.flatMap(channel => {
          const source_ids = channel.source_ids.filter(id => !ids.has(id));
          return source_ids.length ? [{ ...channel, source_ids,
            ...(channel.patch_weights ? { patch_weights: Object.fromEntries(source_ids.map(id => [id, channel.patch_weights![id]])) } : {}),
          }] : [];
        });
        const used = new Set(remaining.map(channel => channel.id));
        const unique = (wanted: string) => {
          let id = wanted;
          for (let suffix = 2; used.has(id); suffix += 1) id = `${wanted}-${suffix}`;
          used.add(id);
          return id;
        };
        // Each new member is one prescribed source, never a multi-patch driver.
        const replacements = sources.map(source => {
          const previous = affected.find(channel => channel.source_ids.includes(source.id))!;
          return { id: unique(share ? sharedChannelId(role, source.id) : source.id),
            motion: share ? driverTemplate.motion : previous.motion, source_ids: [source.id] };
        });
        const first = state.driveChannels.indexOf(template);
        const before = remaining.filter(channel => state.driveChannels.findIndex(old => old.id === channel.id) <= first);
        const driveChannels = [...before, ...replacements, ...remaining.filter(channel => !before.includes(channel))];
        const channelDrivers = { ...retainedChannelDrivers(state, remaining) };
        for (const channel of replacements) {
          const previous = share ? driverTemplate : affected.find(old => old.source_ids.includes(channel.source_ids[0]))!;
          const form = state.channelDrivers[previous.id];
          if (form && channelAcceptsDriver(channel)) channelDrivers[channel.id] = structuredClone(form);
        }
        return { driveChannels, channelDrivers };
      });
    },
  };
  const actions = config.signal ? config.signal(rawActions) : rawActions;
  return {
    get(): ImportedSimulationSetup {
      const data = config.read();
      const sources = config.sources();
      if (config.cadActions) {
        const cad = config.cadActions();
        return { ...data, ...cad, input: config.input, sources, skippedSourceIds: config.skippedSourceIds(),
          driverGroups: data.driveChannels.map(channel => ({ id: channel.id, channelIds: [channel.id] })),
          setSweep: config.setSweep };
      }
      return { ...data, ...actions, input: config.input, sources,
        driverGroups: fileDriverGroups(sources, data.driveChannels, data.channelDrivers),
        skippedSourceIds: config.skippedSourceIds() };
    },
  };
}

const cadSimulationSetup = createImportedSimulationSetup({
  input: 'cad', read: () => useCadReturnStore.getState(),
  write: update => useCadReturnStore.setState(update),
  sources: () => useCadReturnStore.getState().selectedBundle?.sources ?? [],
  skippedSourceIds: () => useCadReturnStore.getState().skippedSourceIds,
  setSweep: update => useCadReturnStore.getState().setSweep(update),
  cadActions: () => useCadReturnStore.getState(),
});

export const fileSimulationSetup = createImportedSimulationSetup({
  input: 'file', read: () => {
    const data = fileData();
    const options = useImportedSolveOptionsStore.getState();
    return { ...data, frequencyStartHz: options.sweepStartHz, frequencyEndHz: options.sweepEndHz, frequencyCount: options.sweepCount };
  },
  write: update => {
    const current = fileData();
    const patch = typeof update === 'function' ? update(current) : update;
    if (patch !== current) useImportedModelStore.setState({ fileSimulation: { ...current, ...patch } });
  },
  sources: () => fileSources(useImportedModelStore.getState().prepared as RegisteredMesh | null),
  skippedSourceIds: () => {
    const model = useImportedModelStore.getState();
    return (model.setup ?? model.setupEditBase?.retained)?.geometry.skipped_source_ids ?? [];
  },
  setSweep: update => useImportedSolveOptionsStore.getState().setSweep({
    ...(update.frequencyStartHz !== undefined ? { startHz: update.frequencyStartHz } : {}),
    ...(update.frequencyEndHz !== undefined ? { endHz: update.frequencyEndHz } : {}),
    ...(update.frequencyCount !== undefined ? { count: update.frequencyCount } : {}),
  }),
  signal: actions => withEditSignals(() => useImportedModelStore.getState() as unknown as typeof actions,
    actions, Object.keys(actions) as (keyof typeof actions)[], 'meshImport'),
});

/** One interface for the shared imported Simulation sections and their children. */
export function importedSimulationSetup(): ImportedSimulationSetup {
  return (workspaceModeStore.getSnapshot().importedInput === 'cad' ? cadSimulationSetup : fileSimulationSetup).get();
}

export function useImportedSimulationSetup(): ImportedSimulationSetup {
  useCadReturnStore();
  useImportedModelStore();
  useImportedSolveOptionsStore();
  useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot);
  return importedSimulationSetup();
}
