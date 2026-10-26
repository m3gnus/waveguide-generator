import { expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { fieldPlaneMemberResponses } from '../viewport/fieldPlaneStore';
import { channelLabel } from './channelLabel';
import { resultViewEntries } from './ResultViewSwitch';
import { summaryGroups } from './summary';
import { selectResultChannels } from './mappers';
import type { ResultPayload } from './types';

const payload = {
  frequencies: [1000], channel_order: ['author-left', 'author-right', 'combined'],
  channels: {
    'author-left': { frequencies: [1000], metadata: { role: 'MF' } },
    'author-right': { frequencies: [1000], metadata: { role: 'MF' } },
    combined: { frequencies: [1000], metadata: { combine: {
      members: ['author-left', 'author-right'], crossovers_hz: [1000], member_roles: ['MF', 'MF'],
    } } },
  }, metadata: { geometry_type: 'imported' },
} as unknown as ResultPayload;

it('preserves CAD-authored IDs and numbers only file channels with shared roles', () => {
  expect(channelLabel(payload, 'author-left')).toBe('author-left');
  expect(channelLabel(payload, 'author-right', 'cad')).toBe('author-right');
  expect(channelLabel(payload, 'author-left', 'mesh-file')).toBe('MF 1');
  expect(channelLabel(payload, 'author-right', 'mesh-file')).toBe('MF 2');
  expect(resultViewEntries(payload).map(entry => entry.label)).toEqual(['Combined', 'author-left', 'author-right']);
  expect(resultViewEntries(payload, 'mesh-file').map(entry => entry.label)).toEqual(['Combined', 'MF 1', 'MF 2']);
  expect(fieldPlaneMemberResponses(payload, 'mesh-file').map(entry => entry.label)).toEqual(['MF 1', 'MF 2']);
  expect(fieldPlaneMemberResponses(payload).map(entry => entry.label)).toEqual(['author-left', 'author-right']);
});

it('uses the run origin when naming the selected channel in the summary', () => {
  const job = (origin: string) => ({ config_summary: { import_origin: origin } }) as unknown as JobItem;
  const channelRow = (origin: string) => summaryGroups({ result: payload.channels!['author-left'] as ResultPayload,
    wrapper: payload, channelId: 'author-left', job: job(origin) })
    .flatMap(group => group.rows).find(row => row.label === 'Channel')?.value;
  expect(channelRow('cad')).toContain('author-left');
  expect(channelRow('mesh-file')).toBe('MF 1 · 3 channels');
  const filePayload = { ...payload, metadata: { ...payload.metadata, import_origin: 'mesh-file' } };
  const metadataRow = summaryGroups({ result: filePayload.channels!['author-left'] as ResultPayload,
    wrapper: filePayload, channelId: 'author-left' })
    .flatMap(group => group.rows).find(row => row.label === 'Channel')?.value;
  expect(metadataRow).toBe('MF 1 · 3 channels');
});

it('names group branches from saved labels and roles instead of opaque IDs', () => {
  const grouped = { ...payload, channels: { ...payload.channels, combined: { frequencies: [1000], metadata: { combine: {
    members: ['group:1', 'group:2'], member_channels: { 'group:1': ['author-left', 'author-right'], 'group:2': ['high'] },
    member_labels: ['Low array', null], member_roles: ['MF', 'HF'], crossovers_hz: [1000],
  } } } } } as unknown as ResultPayload;
  expect(channelLabel(grouped, 'group:1', 'mesh-file')).toBe('Low array');
  expect(channelLabel(grouped, 'group:2', 'mesh-file')).toBe('HF');
  expect(fieldPlaneMemberResponses(grouped, 'mesh-file').map(entry => entry.label)).toEqual(['Low array', 'HF']);
  expect(selectResultChannels('job', 'Run', grouped, 'combined', 'mesh-file').map(entry => entry.channelId))
    .toEqual(['combined', 'author-left', 'author-right']);
});
