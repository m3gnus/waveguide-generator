import { useSyncExternalStore } from 'react';
import type { RegisteredMesh } from '../api/meshImport';
import type { CadSetup } from '../api/jobsSocket';
import type { ImportedSetup } from '../jobs/importedSetup';
import { fromResult, parseWire } from '../results/crossoverSpec';
import { savedChannelDrivers, savedPassiveCardioid } from './cadSetupRestore';
import {
  PASSIVE_CARDIOID_DEFAULTS, channelAcceptsDriver, retainedChannelDrivers, simulationActions, useCadReturnStore,
  type CadDriveChannel, type CadReturnState, type RoleSource, type SimulationActions, type SimulationData,
} from './cadReturn';
import { useImportedModelStore } from './importedModel';
import { useImportedSolveOptionsStore } from './solveOptions';
import { DEFAULT_DRIVE_VOLTAGE_V } from './solveDefaults';
import { withEditSignals } from './solveSettingsEdits';
import { workspaceModeStore } from './workspaceMode';

export interface ImportedSimulationSetup extends SimulationData, SimulationActions {
  input: 'cad' | 'file';
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
      return JSON.stringify({ motion: channel.motion, exterior: channel.exterior_transducer, driver: drivers[id] });
    });
    return settings.every(value => value === settings[0]) ? [group]
      : group.channelIds.map(id => ({ id: `channel:${id}`, channelIds: [id] }));
  });
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
  // An unfinished driver has no wire setup yet. Keep its form and channel
  // choices through preparation when the same source inventory survives.
  if (previous && !setup) {
    const assigned = previous.driveChannels.flatMap(channel => channel.source_ids);
    if (assigned.length === sources.length && assigned.every(id => sources.some(source => source.id === id))) {
      driveChannels = structuredClone(previous.driveChannels);
    }
  }
  const geometry = setup?.geometry as unknown as CadSetup | undefined;
  return {
    driveChannels, channelDrivers: previous ? retainedChannelDrivers(previous, driveChannels) : savedChannelDrivers(geometry, driveChannels, setup?.driver_references),
    passiveCardioid: geometry ? savedPassiveCardioid(geometry) : { ...PASSIVE_CARDIOID_DEFAULTS },
    driveVoltageV: previous?.driveVoltageV ?? setup?.geometry.drive_voltage_v ?? DEFAULT_DRIVE_VOLTAGE_V,
    maxDriveVoltageV: previous?.maxDriveVoltageV ?? setup?.geometry.max_drive_voltage_v ?? null,
    combineEnabled: !!geometry?.combine, combineSpec: parseWire(geometry?.combine) ?? fromResult(geometry?.combine),
    frequencyStartHz: 200, frequencyEndHz: 20_000, frequencyCount: 36,
  };
}

function fileSources(record: RegisteredMesh | null): RoleSource[] {
  return Object.keys(record?.source_tags ?? {}).map(id => ({ id, role: record?.sources?.find(source => source.id === id)?.role ?? 'HF' }));
}

function fileData(): SimulationData {
  const model = useImportedModelStore.getState();
  return model.fileSimulation ?? seedFileSimulation(model.prepared as RegisteredMesh | null, model.setup ?? model.setupEditBase?.retained ?? null);
}

function updateFile(update: Partial<SimulationData> | ((state: SimulationData) => Partial<SimulationData>)): void {
  const current = fileData();
  const patch = typeof update === 'function' ? update(current) : update;
  if (patch !== current) useImportedModelStore.setState({ fileSimulation: { ...current, ...patch } });
}
const singleFileActions = simulationActions(updateFile);
const groupedActionNames = ['setChannelMotion', 'setExteriorTransducer', 'setChannelDriverField',
  'setChannelDriverPreset', 'clearChannelDriverOverrides'] as const;
/** Fan out one card edit through the existing CAD mutations, under one edit signal. */
function groupedAction<K extends typeof groupedActionNames[number]>(name: K): SimulationActions[K] {
  return ((channelId: string, ...args: unknown[]) => {
    const state = fileData();
    const sources = fileSources(useImportedModelStore.getState().prepared as RegisteredMesh | null);
    const ids = fileDriverGroups(sources, state.driveChannels, state.channelDrivers).find(group => group.channelIds.includes(channelId))?.channelIds ?? [channelId];
    const action = singleFileActions[name] as (id: string, ...args: unknown[]) => unknown;
    for (const id of ids) action(id, ...args);
  }) as SimulationActions[K];
}
const rawFileActions = {
  ...singleFileActions,
  ...Object.fromEntries(groupedActionNames.map(name => [name, groupedAction(name)])) as Pick<SimulationActions, typeof groupedActionNames[number]>,
  setSweep: (update: Parameters<CadReturnState['setSweep']>[0]) => useImportedSolveOptionsStore.getState().setSweep({
    ...(update.frequencyStartHz !== undefined ? { startHz: update.frequencyStartHz } : {}),
    ...(update.frequencyEndHz !== undefined ? { endHz: update.frequencyEndHz } : {}),
    ...(update.frequencyCount !== undefined ? { count: update.frequencyCount } : {}),
  }),
  setCombineEnabled: (combineEnabled: boolean) => updateFile({ combineEnabled }),
  setCombineSpec: (combineSpec: SimulationData['combineSpec']) => updateFile({ combineSpec }),
  setRoleSharing(role: string, share: boolean) {
    const sources = fileSources(useImportedModelStore.getState().prepared as RegisteredMesh | null).filter(source => source.role === role
      && fileData().driveChannels.some(channel => channel.source_ids.includes(source.id)));
    const ids = new Set(sources.map(source => source.id));
    updateFile(state => {
      const affected = state.driveChannels.filter(channel => channel.source_ids.some(id => ids.has(id)));
      const template = affected[0];
      if (!template || affected.some(channel => channel.exterior_transducer)) return state;
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
          motion: share ? template.motion : previous.motion, source_ids: [source.id] };
      });
      const first = state.driveChannels.indexOf(template);
      const before = remaining.filter(channel => state.driveChannels.findIndex(old => old.id === channel.id) <= first);
      const driveChannels = [...before, ...replacements, ...remaining.filter(channel => !before.includes(channel))];
      const channelDrivers = { ...retainedChannelDrivers(state, remaining) };
      for (const channel of replacements) {
        const previous = share ? template : affected.find(old => old.source_ids.includes(channel.source_ids[0]))!;
        const form = state.channelDrivers[previous.id];
        if (form && channelAcceptsDriver(channel)) channelDrivers[channel.id] = structuredClone(form);
      }
      return { driveChannels, channelDrivers };
    });
  },
};
const fileActions = withEditSignals(() => useImportedModelStore.getState() as unknown as typeof rawFileActions, rawFileActions, Object.keys(rawFileActions) as (keyof typeof rawFileActions)[], 'meshImport');

/** One interface for the shared imported Simulation sections and their children. */
export function importedSimulationSetup(): ImportedSimulationSetup {
  if (workspaceModeStore.getSnapshot().importedInput === 'cad') {
    const state = useCadReturnStore.getState();
    return { ...state, input: 'cad', sources: state.selectedBundle?.sources ?? [],
      driverGroups: state.driveChannels.map(channel => ({ id: channel.id, channelIds: [channel.id] })) };
  }
  const model = useImportedModelStore.getState();
  const options = useImportedSolveOptionsStore.getState();
  const data = fileData();
  const sources = fileSources(model.prepared as RegisteredMesh | null);
  return { ...data, ...fileActions, input: 'file', sources, driverGroups: fileDriverGroups(sources, data.driveChannels, data.channelDrivers),
    skippedSourceIds: (model.setup ?? model.setupEditBase?.retained)?.geometry.skipped_source_ids ?? [],
    frequencyStartHz: options.sweepStartHz, frequencyEndHz: options.sweepEndHz, frequencyCount: options.sweepCount };
}

export function useImportedSimulationSetup(): ImportedSimulationSetup {
  useCadReturnStore();
  useImportedModelStore();
  useImportedSolveOptionsStore();
  useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot);
  return importedSimulationSetup();
}
