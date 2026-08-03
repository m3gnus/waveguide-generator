import type { FrameHeader } from '../api/frame';

type PreviewLod = NonNullable<FrameHeader['lod']>;

function requestKey(epoch: number, revision: number, lod: PreviewLod): string {
  return `${epoch}:${revision}:${lod}`;
}

export class ClientLatencyClock {
  private epoch: number | null = null;
  private epochStartedAt = 0;
  private readonly requests = new Map<string, number>();

  beginEpoch(epoch: number, revision: number, startedAt: number): void {
    if (this.epoch === epoch) return;
    this.epoch = epoch;
    this.epochStartedAt = startedAt;
    this.requests.clear();
    this.recordRequest(epoch, revision, 'fine', startedAt);
  }

  disconnect(): void {
    this.epoch = null;
    this.requests.clear();
  }

  recordRequest(epoch: number, revision: number, lod: PreviewLod, startedAt: number): void {
    if (epoch !== this.epoch || startedAt < this.epochStartedAt) return;
    this.requests.set(requestKey(epoch, revision, lod), startedAt);
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
  if (milliseconds > 999) return '>999';
  return Math.max(0, milliseconds).toFixed(1);
}
