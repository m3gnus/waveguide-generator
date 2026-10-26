import { combinedChannelId, resultChannels, type ResultPayload } from './types';

/** The coupled campaign's own channel, which is a port pair rather than a driver. */
const PASSIVE_CARDIOID_CHANNEL_ID = 'passive_cardioid';

/** Bands a channel may speak for, highest first: the order the crossovers are
 * spoken in ("HF–MF, MF–LF") and therefore the order the switch reads in. */
export const BAND_ROLES = ['HF', 'MF', 'LF'] as const;

function channelPayload(result: ResultPayload, channelId: string): ResultPayload | undefined {
  return result.channels?.[channelId] as ResultPayload | undefined;
}

/** The band this channel speaks for, or null when the solve named none. */
export function channelRole(result: ResultPayload, channelId: string): string | null {
  const role = channelPayload(result, channelId)?.metadata?.role;
  return typeof role === 'string' && role.trim() ? role.trim() : null;
}

/**
 * What this channel is called in the dock.
 *
 * CAD keeps its authored names when roles are shared. File imports number
 * same-band channels because their IDs are implementation details.
 */
export function channelLabel(result: ResultPayload, channelId: string, importOrigin?: unknown): string {
  if (channelId === combinedChannelId(result)) return 'Combined';
  if (channelId === PASSIVE_CARDIOID_CHANNEL_ID) return 'Cardioid';
  const file = (importOrigin ?? result.metadata?.import_origin) === 'mesh-file';
  const combinedId = combinedChannelId(result);
  const combine = combinedId ? channelPayload(result, combinedId)?.metadata?.combine : null;
  if (file && combine && typeof combine === 'object' && 'members' in combine && Array.isArray(combine.members)) {
    const index = combine.members.indexOf(channelId);
    if (index >= 0 && 'member_channels' in combine) {
      const labels = 'member_labels' in combine && Array.isArray(combine.member_labels) ? combine.member_labels : [];
      const roles = 'member_roles' in combine && Array.isArray(combine.member_roles) ? combine.member_roles : [];
      const branches = combine.members.map((id, i) => ({ id: String(id), role: typeof labels[i] === 'string' && labels[i] ? labels[i] : typeof roles[i] === 'string' ? roles[i] : null }));
      return roleChannelLabel(channelId, branches, 'number');
    }
  }
  return roleChannelLabel(channelId, resultChannels(result).map(({ id }) => ({ id, role: channelRole(result, id) })),
    file ? 'number' : 'id');
}

/**
 * The tooltip: the authored id and the sources behind it.
 *
 * The label above is deliberately short, so this is where the identity the
 * user authored in CAD stays reachable — without it a run with two MF-ish
 * sources offers no way to tell which physical driver a curve came from.
 */
export function channelTitle(result: ResultPayload, channelId: string): string {
  const metadata = channelPayload(result, channelId)?.metadata;
  const ids = Array.isArray(metadata?.source_ids) ? metadata.source_ids : [];
  const labels = Array.isArray(metadata?.source_labels) ? metadata.source_labels : [];
  const sources = ids.map((id, index) => {
    const label = labels[index];
    return label && label !== id ? `${label} (${id})` : id;
  });
  return sources.length ? `${channelId} · sources: ${sources.join(', ')}` : channelId;
}

/** A unique role names a channel; a shared role needs the authored id. */
export function roleChannelLabel(channelId: string, channels: readonly { id: string; role: string | null | undefined }[],
  fallback: 'id' | 'number' = 'id'): string {
  const role = channels.find(({ id }) => id === channelId)?.role;
  const peers = channels.filter(channel => channel.role === role);
  if (role && peers.length === 1) return role;
  if (fallback === 'id') return channelId;
  return `${role || 'Driver'} ${Math.max(0, peers.findIndex(channel => channel.id === channelId)) + 1}`;
}
