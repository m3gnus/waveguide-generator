import { useSyncExternalStore } from 'react';
import type { RegisteredMesh } from '../api/meshImport';
import type { CadSetup } from '../api/jobsSocket';
import type { ImportedSetup } from '../jobs/importedSetup';
import { fromResult, parseWire } from '../results/crossoverSpec';
import { savedChannelDrivers, savedPassiveCardioid } from './cadSetupRestore';
import { driverGroupsFromWire } from './importedDriverGroups';
import {
  PASSIVE_CARDIOID_DEFAULTS, cadPassiveCardioidTopology, channelAcceptsDriver, channelDriverPresent, retainedChannelDrivers, simulationActions, useCadReturnStore,
  type CadDriveChannel, type CadReturnState, type PassiveCardioidTopology, type RoleSource, type SimulationActions, type SimulationData, type SimulationDriverGroup,
} from './cadReturn';
import { useImportedModelStore } from './importedModel';
import { useImportedSolveOptionsStore } from './solveOptions';
import { DEFAULT_DRIVE_VOLTAGE_V } from './solveDefaults';
import { withEditSignals } from './solveSettingsEdits';
import { workspaceModeStore } from './workspaceMode';
import { sourceRoleLabel } from '../model/cadLabels';
import { roleChannelLabel } from '../results/channelLabel';

export interface ImportedSimulationSetup extends SimulationData, SimulationActions {
  input: 'cad' | 'file' | 'assembly';
  sources: readonly (RoleSource & { defaultDriveChannelId?: string })[];
  /** One amplifier/band branch, with one physical driver channel per surface. */
  driverGroups: ImportedDriverGroup[];
  skippedSourceIds: string[];
  passiveTopology: PassiveCardioidTopology | null;
  setSourceChannel?: CadReturnState['setSourceChannel'];
  setRoleSharing?: (role: string, share: boolean) => void;
  setCombineEnabled: CadReturnState['setCombineEnabled'];
  setCombineSpec: CadReturnState['setCombineSpec'];
}

export type ImportedDriverGroup = SimulationDriverGroup;

/** A file's card or blocker names the source role, even when its ID is opaque. */
export function importedDriverLabel(state: Pick<ImportedSimulationSetup, 'sources' | 'driveChannels' | 'driverGroups'>,
  channelId: string): string {
  const groups = state.driverGroups.map(group => {
    const channel = state.driveChannels.find(item => group.channelIds.includes(item.id));
    const role = state.sources.find(source => channel?.source_ids.includes(source.id))?.role;
    return { id: group.id, role: group.label || (role ? sourceRoleLabel(role, { driver: true }) : null) };
  });
  const group = state.driverGroups.find(item => item.id === channelId || item.channelIds.includes(channelId));
  return roleChannelLabel(group?.id ?? channelId, groups, 'number');
}

const roleGroupId = (role: string) => `drive-${role.toLowerCase()}`;
const sourceChannelId = (sourceId: string) => `source:${encodeURIComponent(sourceId)}`;
const channelGroupId = (channelId: string) => `group:${encodeURIComponent(channelId)}`;

/** Read F5a/F5b's old encoded membership once, retaining all physical IDs. */
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

/** Reconcile explicit membership; file role edits detach the changed surface. */
function resolveDriverGroups(sources: readonly RoleSource[], channels: readonly CadDriveChannel[],
  drivers: SimulationData['channelDrivers'], saved?: ImportedDriverGroup[], splitByRole = true): ImportedDriverGroup[] {
  const input = saved ?? fileDriverGroups(sources, channels, drivers);
  const used = new Set<string>();
  const assigned = new Set<string>();
  const unique = (wanted: string, members: string[]) => {
    let id = wanted;
    for (let n = 2; used.has(id) || (channels.some(channel => channel.id === id) && !(members.length === 1 && members[0] === id)); n += 1) id = `${wanted}:${n}`;
    used.add(id); return id;
  };
  const result: ImportedDriverGroup[] = [];
  for (const group of input) {
    const buckets = new Map<string, string[]>();
    for (const id of group.channelIds) {
      const channel = channels.find(item => item.id === id);
      if (!channel || assigned.has(id)) continue;
      const roles = [...new Set(channel.source_ids.map(sourceId => sources.find(source => source.id === sourceId)?.role).filter(Boolean))];
      const key = !splitByRole ? 'group' : channel.exterior_transducer ? `exterior:${id}` : roles.length === 1 ? roles[0]!
        : !sources.length ? 'unprepared' : `channel:${id}`;
      const bucket = buckets.get(key) ?? [];
      bucket.push(id); buckets.set(key, bucket); assigned.add(id);
    }
    let first = true;
    for (const ids of buckets.values()) {
      const id = unique(first ? group.id : channelGroupId(ids[0]), ids);
      result.push({ id, channelIds: ids, ...(group.label ? { label: group.label } : {}) });
      first = false;
    }
  }
  for (const channel of channels) if (!assigned.has(channel.id)) {
    result.push({ id: unique(channelGroupId(channel.id), [channel.id]), channelIds: [channel.id] });
  }
  return result.sort((a, b) => channels.findIndex(channel => channel.id === a.channelIds[0])
    - channels.findIndex(channel => channel.id === b.channelIds[0]));
}

/** Reconcile prepared surfaces using the existing role's sharing choice. */
export function reconcileFileChannels<T extends CadDriveChannel>(record: RegisteredMesh, original: T[],
  drivers: SimulationData['channelDrivers'], skipped: readonly string[], savedGroups?: ImportedDriverGroup[]): {
    channels: T[]; from: Record<string, string>; groups: ImportedDriverGroup[];
  } {
  const from: Record<string, string> = {};
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
  const groups = resolveDriverGroups(existing, channels, drivers, savedGroups);
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
    const group = groups.find(candidate => candidate.channelIds.length === oldSources.length
      && oldChannels.every(channel => candidate.channelIds.includes(channel.id) && channel.source_ids.length === 1));
    const template = group && channels.find(channel => channel.id === group.channelIds[0]);
    const share = !!record.sources?.length && (!oldSources.length || !!template)
      && !oldChannels.some(channel => channel.exterior_transducer || channel.physical_source_id !== undefined || channel.patch_weights !== undefined);
    let target = share ? group : undefined;
    for (const source of newSources) {
      const id = unique(record.sources?.length ? sourceChannelId(source.id) : source.id);
      if (share && template) from[id] = template.id;
      channels.push({ id, source_ids: [source.id], motion: share && template ? template.motion : 'normal',
        ...(share && template && 'driver' in template ? { driver: structuredClone(template.driver) } : {}) } as T);
      if (!target) {
        target = { id: channelGroupId(id), channelIds: [] };
        groups.push(target);
      }
      target.channelIds.push(id);
      if (!share) target = undefined;
    }
  }
  return { channels, from, groups: resolveDriverGroups(sources, channels, drivers, groups) };
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
      id: record?.sources?.length ? sourceChannelId(source.id) : source.id,
      source_ids: [source.id], motion: 'normal' as const,
    }));
  // Keep live forms and channel choices through preparation, including an
  // unfinished driver which has no wire setup yet.
  let previousDrivers = previous?.channelDrivers;
  let previousGroups = previous?.driverGroups;
  if (previous && record) {
    const extended = reconcileFileChannels(record, previous.driveChannels, previous.channelDrivers, setup?.geometry.skipped_source_ids ?? [], previous.driverGroups);
    previousDrivers = { ...retainedChannelDrivers(previous, extended.channels) };
    for (const [id, source] of Object.entries(extended.from)) {
      if (previous.channelDrivers[source]) previousDrivers[id] = structuredClone(previous.channelDrivers[source]);
    }
    if (!setup) driveChannels = extended.channels;
    previousGroups = extended.groups;
  }
  const geometry = setup?.geometry as unknown as CadSetup | undefined;
  const savedGroups = setup?.geometry.driver_groups && driverGroupsFromWire(setup.geometry.driver_groups);
  const initialGroups = !setup && !previous && record?.sources?.length
    ? [...new Set(sources.map(source => source.role))].map(role => {
      const channelIds = driveChannels.filter(channel => channel.source_ids.some(id => sources.find(source => source.id === id)?.role === role)).map(channel => channel.id);
      return { id: channelGroupId(channelIds[0]), channelIds };
    }) : undefined;
  const channelDrivers = previous && previousDrivers
    ? retainedChannelDrivers({ driveChannels, channelDrivers: previousDrivers }, driveChannels)
    : savedChannelDrivers(geometry, driveChannels, setup?.driver_references);
  let groups = resolveDriverGroups(sources, driveChannels, channelDrivers, previousGroups ?? savedGroups ?? initialGroups);
  if (setup && !savedGroups) {
    // Legacy requests may have independent crossover branches or generator
    // resistance. Their encoded IDs must not imply an invalid shared branch.
    const members = new Set(setup.geometry.combine?.members ?? []);
    groups = groups.flatMap(group => group.channelIds.length > 1
      && ((setup.geometry.rg_ohm ?? 0) !== 0 || group.channelIds.some(id => members.has(id)))
      ? group.channelIds.map(id => ({ id: channelGroupId(id), channelIds: [id] })) : [group]);
    groups = resolveDriverGroups(sources, driveChannels, channelDrivers, groups);
  }
  return {
    driveChannels, channelDrivers,
    driverGroups: groups,
    persistDriverGroups: previous?.persistDriverGroups ?? (setup ? !!savedGroups : !!record?.sources?.length),
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
  passiveTopology?: () => PassiveCardioidTopology | null;
  setSweep: SimulationActions['setSweep'];
  /** CAD already owns its edit signals and source assignment. */
  cadActions?: () => CadReturnState;
  signal?: <T extends object>(actions: T) => T;
}) {
  const singleActions = simulationActions(config.write);
  const adapterGroups = (sources: ImportedSimulationSetup['sources'], state: SimulationData) =>
    resolveDriverGroups(sources, state.driveChannels, state.channelDrivers,
      state.driverGroups ?? (config.input === 'file' ? undefined : state.driveChannels.map(channel => ({ id: channel.id, channelIds: [channel.id] }))),
      config.input === 'file');
  /** Fan out one card edit through the existing mutations, under one edit signal. */
  function groupedAction<K extends typeof groupedActionNames[number]>(name: K): SimulationActions[K] {
    return ((channelId: string, ...args: unknown[]) => {
      const state = config.read();
      const sources = config.sources();
      const ids = adapterGroups(sources, state).find(group => group.channelIds.includes(channelId))?.channelIds ?? [channelId];
      const action = singleActions[name] as (id: string, ...args: unknown[]) => unknown;
      for (const id of ids) action(id, ...args);
    }) as SimulationActions[K];
  }
  const rawActions = {
    ...singleActions,
    ...Object.fromEntries(groupedActionNames.map(name => [name, groupedAction(name)])) as Pick<SimulationActions, typeof groupedActionNames[number]>,
    setExteriorTransducer: (channelId: string, form: Parameters<SimulationActions['setExteriorTransducer']>[1]) => {
      singleActions.setExteriorTransducer(channelId, form);
      config.write(state => ({ driverGroups: adapterGroups(config.sources(), state), persistDriverGroups: true }));
    },
    setSweep: config.setSweep,
    setCombineEnabled: (combineEnabled: boolean) => config.write({ combineEnabled, persistDriverGroups: true }),
    setCombineSpec: (combineSpec: SimulationData['combineSpec']) => config.write({ combineSpec, persistDriverGroups: true }),
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
        // Keep existing single-source IDs; splitting an old multi-source channel
        // creates a new coordinate only for the detached surfaces.
        const replacements = sources.map(source => {
          const previous = affected.find(channel => channel.source_ids.includes(source.id))!;
          return { id: unique(previous.source_ids.length === 1 ? previous.id : sourceChannelId(source.id)),
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
        const remainingIds = new Set(remaining.map(channel => channel.id));
        const groups = adapterGroups(config.sources(), state)
          .map(group => ({ ...group, channelIds: group.channelIds.filter(id => remainingIds.has(id)) }))
          .filter(group => group.channelIds.length);
        if (share) groups.push({ id: channelGroupId(replacements[0].id), channelIds: replacements.map(channel => channel.id) });
        else groups.push(...replacements.map(channel => ({ id: channelGroupId(channel.id), channelIds: [channel.id] })));
        return { driveChannels, channelDrivers,
          driverGroups: resolveDriverGroups(config.sources(), driveChannels, channelDrivers, groups, config.input === 'file'), persistDriverGroups: true };
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
          passiveTopology: config.passiveTopology?.() ?? null,
          driverGroups: data.driveChannels.map(channel => ({ id: channel.id, channelIds: [channel.id] })),
          setSweep: config.setSweep };
      }
      return { ...data, ...actions, input: config.input, sources,
        driverGroups: adapterGroups(sources, data),
        skippedSourceIds: config.skippedSourceIds(), passiveTopology: config.passiveTopology?.() ?? null };
    },
  };
}

const cadSimulationSetup = createImportedSimulationSetup({
  input: 'cad', read: () => useCadReturnStore.getState(),
  write: update => useCadReturnStore.setState(update),
  sources: () => useCadReturnStore.getState().selectedBundle?.sources ?? [],
  skippedSourceIds: () => useCadReturnStore.getState().skippedSourceIds,
  passiveTopology: () => cadPassiveCardioidTopology(useCadReturnStore.getState()),
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
  passiveTopology: () => {
    const model = useImportedModelStore.getState();
    const record = model.prepared as RegisteredMesh | null;
    if (!record) return null;
    return { sources: fileSources(record), sourceTags: record.source_tags ?? null,
      skippedSourceIds: (model.setup ?? model.setupEditBase?.retained)?.geometry.skipped_source_ids ?? [] };
  },
  setSweep: update => useImportedSolveOptionsStore.getState().setSweep({
    ...(update.frequencyStartHz !== undefined ? { startHz: update.frequencyStartHz } : {}),
    ...(update.frequencyEndHz !== undefined ? { endHz: update.frequencyEndHz } : {}),
    ...(update.frequencyCount !== undefined ? { count: update.frequencyCount } : {}),
  }),
  signal: actions => withEditSignals(() => useImportedModelStore.getState() as unknown as typeof actions,
    actions, Object.keys(actions).filter(name => name !== 'refreshChannelDriverBases') as (keyof typeof actions)[], 'meshImport'),
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
