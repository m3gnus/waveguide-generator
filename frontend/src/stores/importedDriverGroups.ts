import type { ImportedSetup } from '../jobs/importedSetup';
import type { SimulationDriverGroup } from './cadReturn';

type DriverGroupWire = NonNullable<ImportedSetup['geometry']['driver_groups']>[number];

/** The one boundary between saved group records and the simulation adapter. */
export function driverGroupsFromWire(groups: readonly DriverGroupWire[]): SimulationDriverGroup[] {
  return groups.map(group => ({ id: group.id, channelIds: [...group.channel_ids],
    ...(group.label ? { label: group.label } : {}) }));
}

export function driverGroupsToWire(groups: readonly SimulationDriverGroup[]): DriverGroupWire[] {
  return groups.map(group => ({ id: group.id, channel_ids: [...group.channelIds],
    ...(group.label ? { label: group.label } : {}) }));
}
