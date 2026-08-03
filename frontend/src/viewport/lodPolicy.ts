import type { DecodedFrame } from '../api/frame';

function revision(frame: DecodedFrame): number {
  return frame.header.designRevision ?? -1;
}

function sequence(frame: DecodedFrame): number {
  return frame.header.seq ?? -1;
}

export function selectPreferredFrame(current: DecodedFrame | null, incoming: DecodedFrame | null): DecodedFrame | null {
  if (!incoming) return current;
  if (!current) return incoming;
  // Sequence numbers restart with each socket epoch. An accepted frame from a
  // new epoch must replace the retained reconnect fallback even at a lower seq.
  if (incoming.header.epoch !== current.header.epoch) return incoming;
  const currentRevision = revision(current);
  const incomingRevision = revision(incoming);
  if (incomingRevision < currentRevision) return current;
  if (incomingRevision > currentRevision) return incoming;
  if (current.header.lod === 'fine' && incoming.header.lod === 'coarse') return current;
  if (current.header.lod === incoming.header.lod && sequence(incoming) < sequence(current)) return current;
  return incoming;
}
