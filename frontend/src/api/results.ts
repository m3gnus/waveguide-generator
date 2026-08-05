export type NullableNumber = number | null;
export type PolarSample = [number, NullableNumber | [number, number]];

export interface JobResults {
  frequencies: number[];
  directivity?: {
    horizontal?: PolarSample[][];
    vertical?: PolarSample[][];
  };
  spl_on_axis?: {
    frequencies?: number[];
    spl?: NullableNumber[];
    phase_degrees?: NullableNumber[];
  };
  impedance?: {
    frequencies?: number[];
    real?: NullableNumber[];
    imaginary?: NullableNumber[];
  };
  metadata?: Record<string, unknown>;
}

export interface CompareSelection {
  primary: string | null;
  overlays: string[];
}

export class ResultsLruCache {
  private readonly entries = new Map<string, JobResults>();
  readonly maxEntries: number;

  constructor(maxEntries = 15) {
    this.maxEntries = Number.isFinite(maxEntries) ? Math.max(1, Math.min(15, Math.floor(maxEntries))) : 15;
  }

  get(jobId: string): JobResults | undefined {
    const value = this.entries.get(jobId);
    if (!value) return undefined;
    this.entries.delete(jobId);
    this.entries.set(jobId, value);
    return value;
  }

  set(jobId: string, results: JobResults): void {
    this.entries.delete(jobId);
    this.entries.set(jobId, results);
    while (this.entries.size > this.maxEntries) {
      const oldest = this.entries.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  has(jobId: string): boolean { return this.entries.has(jobId); }
  keys(): string[] { return [...this.entries.keys()]; }
  clear(): void { this.entries.clear(); }
}

export const resultsCache = new ResultsLruCache(15);
const inFlightResults = new Map<string, Promise<JobResults>>();

export async function fetchJobResults(jobId: string, fetcher: typeof fetch = fetch): Promise<JobResults> {
  const cached = resultsCache.get(jobId);
  if (cached) return cached;
  const existing = inFlightResults.get(jobId);
  if (existing) return existing;
  const request = (async () => {
    const response = await fetcher(`/api/results/${encodeURIComponent(jobId)}`);
    if (!response.ok) {
      let detail = `Results request failed: ${response.status}`;
      try {
        const body = await response.json() as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch { /* status is enough */ }
      throw new Error(detail);
    }
    const result = await response.json() as JobResults;
    resultsCache.set(jobId, result);
    return result;
  })();
  inFlightResults.set(jobId, request);
  try {
    return await request;
  } finally {
    if (inFlightResults.get(jobId) === request) inFlightResults.delete(jobId);
  }
}

export class CompareStore {
  private value: CompareSelection = { primary: null, overlays: [] };
  private readonly listeners = new Set<() => void>();

  getSnapshot = (): CompareSelection => this.value;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  setPrimary(jobId: string | null): void {
    this.set({ primary: jobId, overlays: this.value.overlays.filter((id) => id !== jobId) });
  }
  toggleOverlay(jobId: string): void {
    if (jobId === this.value.primary) return;
    const overlays = this.value.overlays.includes(jobId)
      ? this.value.overlays.filter((id) => id !== jobId)
      : [...this.value.overlays, jobId];
    this.set({ ...this.value, overlays });
  }
  remove(jobId: string): void {
    if (this.value.primary === jobId) {
      const [primary = null, ...overlays] = this.value.overlays;
      this.set({ primary, overlays });
    } else {
      this.set({ ...this.value, overlays: this.value.overlays.filter((id) => id !== jobId) });
    }
  }
  clear(): void { this.set({ primary: null, overlays: [] }); }
  prune(validJobIds: ReadonlySet<string>): void {
    const primary = this.value.primary && validJobIds.has(this.value.primary) ? this.value.primary : null;
    const overlays = this.value.overlays.filter((id) => validJobIds.has(id) && id !== primary);
    if (primary === this.value.primary && overlays.length === this.value.overlays.length) return;
    this.set({ primary, overlays });
  }
  private set(value: CompareSelection): void {
    this.value = value;
    this.listeners.forEach((listener) => listener());
  }
}

export const compareSelection = new CompareStore();
