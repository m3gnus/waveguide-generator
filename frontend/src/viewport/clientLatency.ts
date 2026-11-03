/** Bounded timestamps owned by actual sends on one socket connection. */
export class ClientLatencyClock {
  private generation = 0;
  private readonly requests = new Map<number, number>();

  constructor(private readonly maxRequests = 256) {}

  beginConnection(generation: number): void {
    this.generation = generation;
    this.requests.clear();
  }

  recordRequest(generation: number, seq: number, startedAt: number): void {
    if (generation !== this.generation) return;
    this.requests.set(seq, startedAt);
    while (this.requests.size > Math.max(1, this.maxRequests)) {
      const oldest = this.requests.keys().next().value as number | undefined;
      if (oldest === undefined) break;
      this.requests.delete(oldest);
    }
  }

  requestStartedAt(generation: number, seq: number): number | null {
    return generation === this.generation ? this.requests.get(seq) ?? null : null;
  }
}

export function formatClientLatency(milliseconds: number | null): string {
  if (milliseconds === null || !Number.isFinite(milliseconds)) return '—';
  // Past a second, switch units rather than clamping. '>999' was a clamp
  // presented as a measurement: it sat beside a real reading, read as a fault
  // condition, and stayed identical however slow the frame actually was.
  if (milliseconds >= 1000) return `${(milliseconds / 1000).toFixed(milliseconds >= 10_000 ? 0 : 1)} s`;
  return `${Math.max(0, milliseconds).toFixed(1)} ms`;
}
