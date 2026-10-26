import { expect, it } from 'vitest';
import { driverGroupsFromWire, driverGroupsToWire } from './importedDriverGroups';

it('round-trips membership and optional labels without aliasing the saved records', () => {
  const wire = [{ id: 'mid', channel_ids: ['left', 'right'], label: 'Mid pair' },
    { id: 'high', channel_ids: ['top'], label: null }];
  const groups = driverGroupsFromWire(wire);
  expect(groups).toEqual([{ id: 'mid', channelIds: ['left', 'right'], label: 'Mid pair' },
    { id: 'high', channelIds: ['top'] }]);
  const saved = driverGroupsToWire(groups);
  expect(saved).toEqual([{ id: 'mid', channel_ids: ['left', 'right'], label: 'Mid pair' },
    { id: 'high', channel_ids: ['top'] }]);
  groups[0].channelIds.push('other');
  expect(wire[0].channel_ids).toEqual(['left', 'right']);
  expect(saved[0].channel_ids).toEqual(['left', 'right']);
});
