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
 * The band, when the run has exactly one channel in it. A role shared by two
 * channels names neither of them, and an id authored in CAD (`drive-hf`) is
 * still a true name even when it is an ugly one, so both of those fall back to
 * the id rather than to a label that cannot be told apart from its neighbour.
 */
export function channelLabel(result: ResultPayload, channelId: string): string {
  if (channelId === combinedChannelId(result)) return 'Combined';
  if (channelId === PASSIVE_CARDIOID_CHANNEL_ID) return 'Cardioid';
  const role = channelRole(result, channelId);
  if (!role) return channelId;
  const sharing = resultChannels(result).filter(({ id }) => channelRole(result, id) === role);
  return sharing.length === 1 ? role : channelId;
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
