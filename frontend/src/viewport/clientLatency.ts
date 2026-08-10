import type { FrameHeader } from '../api/frame';

type PreviewLod = NonNullable<FrameHeader['lod']>;

function requestKey(epoch: number, revision: number, lod: PreviewLod): string {
  return `${epoch}:${revision}:${lod}`;
}

export class ClientLatencyClock {
  private epoch: number | null = null;
  private epochStartedAt = 0;
  private readonly requests = new Map<string, number>();

  constructor(private readonly maxRequests = 256) {}

  beginEpoch(epoch: number, revision: number, startedAt: number): void {
    if (this.epoch === epoch) return;
    this.epoch = epoch;
    this.epochStartedAt = startedAt;
    this.requests.clear();
    this.recordRequest(epoch, revision, 'coarse', startedAt);
    this.recordRequest(epoch, revision, 'fine', startedAt);
  }

  disconnect(): void {
    this.epoch = null;
    this.requests.clear();
  }

  recordRequest(epoch: number, revision: number, lod: PreviewLod, startedAt: number): void {
    if (epoch !== this.epoch || startedAt < this.epochStartedAt) return;
    const key = requestKey(epoch, revision, lod);
    // Refresh insertion order when a request supersedes the same revision/LOD.
    this.requests.delete(key);
    this.requests.set(key, startedAt);
    while (this.requests.size > Math.max(1, this.maxRequests)) {
      const oldest = this.requests.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.requests.delete(oldest);
    }
  }

  requestStartedAt(header: Pick<FrameHeader, 'epoch' | 'designRevision' | 'lod'>): number | null {
    const { epoch, designRevision, lod } = header;
    if (epoch === undefined || designRevision === undefined || lod === undefined || epoch !== this.epoch) return null;
    const startedAt = this.requests.get(requestKey(epoch, designRevision, lod));
    if (startedAt === undefined || startedAt < this.epochStartedAt) return null;
    return startedAt;
  }
}

export function formatClientLatency(milliseconds: number | null): string {
  if (milliseconds === null || !Number.isFinite(milliseconds)) return '—';
  // Past a second, switch units rather than clamping. '>999' was a clamp
  // presented as a measurement: it sat beside a real reading, read as a fault
  // condition, and stayed identical however slow the frame actually was.
  if (milliseconds >= 1000) return `${(milliseconds / 1000).toFixed(milliseconds >= 10_000 ? 0 : 1)} s`;
  return Math.max(0, milliseconds).toFixed(1);
}
