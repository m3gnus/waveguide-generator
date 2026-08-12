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
  channels?: Record<string, JobResults>;
  channel_order?: string[];
}

function sortFrequencyShapedRows(result: JobResults): JobResults {
  const record = result as JobResults & Record<string, unknown>;
  const frequencies = record.frequencies;
  if (Array.isArray(frequencies) && frequencies.length > 1) {
    const order = frequencies.map((_, index) => index).sort((left, right) => Number(frequencies[left]) - Number(frequencies[right]));
    if (order.some((value, index) => value !== index)) {
      const count = frequencies.length;
      record.frequencies = order.map((index) => frequencies[index]) as number[];
      for (const blockName of ['directivity', 'directivity_phase']) {
        const block = record[blockName];
        if (!block || typeof block !== 'object' || Array.isArray(block)) continue;
        Object.entries(block as Record<string, unknown>).forEach(([plane, rows]) => {
          if (Array.isArray(rows) && rows.length === count) (block as Record<string, unknown>)[plane] = order.map((index) => rows[index]);
        });
      }
      for (const blockName of ['spl_on_axis', 'impedance', 'di']) {
        const block = record[blockName];
        if (!block || typeof block !== 'object' || Array.isArray(block)) continue;
        Object.entries(block as Record<string, unknown>).forEach(([key, values]) => {
          if (Array.isArray(values) && values.length === count) (block as Record<string, unknown>)[key] = order.map((index) => values[index]);
        });
      }
    }
  }
  Object.values(result.channels ?? {}).forEach(sortFrequencyShapedRows);
  return result;
}

export function mergeProvisionalResults(current: JobResults | undefined, delta: JobResults): JobResults {
  if (!current) return sortFrequencyShapedRows(structuredClone(delta));
  // Copy only the branches receiving a new row. Deep-cloning the accumulated
  // sweep for every frequency makes a 401-point solve quadratic in payload
  // size before ECharts even sees it.
  const merged = { ...current } as JobResults & Record<string, unknown>;
  const incoming = delta as JobResults & Record<string, unknown>;
  const append = (target: Record<string, unknown>, source: Record<string, unknown>, key: string) => {
    if (!Array.isArray(source[key])) return;
    target[key] = [...(Array.isArray(target[key]) ? target[key] as unknown[] : []), ...structuredClone(source[key] as unknown[])];
  };

  append(merged, incoming, 'frequencies');
  for (const blockName of ['directivity', 'directivity_phase']) {
    const source = incoming[blockName];
    if (!source || typeof source !== 'object' || Array.isArray(source)) continue;
    const target = merged[blockName] && typeof merged[blockName] === 'object' && !Array.isArray(merged[blockName])
      ? { ...merged[blockName] as Record<string, unknown> }
      : {};
    Object.entries(source as Record<string, unknown>).forEach(([plane, rows]) => {
      if (!Array.isArray(rows)) return;
      target[plane] = [...(Array.isArray(target[plane]) ? target[plane] as unknown[] : []), ...structuredClone(rows)];
    });
    merged[blockName] = target;
  }
  for (const blockName of ['spl_on_axis', 'impedance', 'di']) {
    const source = incoming[blockName];
    if (!source || typeof source !== 'object' || Array.isArray(source)) continue;
    const target = merged[blockName] && typeof merged[blockName] === 'object' && !Array.isArray(merged[blockName])
      ? { ...merged[blockName] as Record<string, unknown> }
      : {};
    Object.keys(source as Record<string, unknown>).forEach((key) => append(target, source as Record<string, unknown>, key));
    merged[blockName] = target;
  }
  if (incoming.channels && typeof incoming.channels === 'object') {
    const channels = { ...(merged.channels ?? {}) };
    Object.entries(incoming.channels as Record<string, JobResults>).forEach(([id, result]) => {
      channels[id] = mergeProvisionalResults(channels[id], result);
    });
    merged.channels = channels;
  }
  if (Array.isArray(delta.channel_order)) merged.channel_order = [...delta.channel_order];
  if (delta.metadata) merged.metadata = { ...(current.metadata ?? {}), ...structuredClone(delta.metadata) };
  return sortFrequencyShapedRows(merged);
}

export interface ProvisionalResultEntry {
  revision: number;
  result: JobResults;
}

export interface ProvisionalResultsSnapshot {
  version: number;
  entries: Record<string, ProvisionalResultEntry>;
}

export class ProvisionalResultsStore {
  private snapshot: ProvisionalResultsSnapshot = { version: 0, entries: {} };
  private entries: Record<string, ProvisionalResultEntry> = {};
  private readonly listeners = new Set<() => void>();
  private notifyTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly refreshIntervalMs = 250) {}

  getSnapshot = (): ProvisionalResultsSnapshot => this.snapshot;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  get(jobId: string): ProvisionalResultEntry | undefined { return this.entries[jobId]; }

  /** False means a delta gap; the caller should fetch the process-local snapshot. */
  apply(jobId: string, revision: number, result: JobResults, snapshot = false): boolean {
    const current = this.entries[jobId];
    if (!Number.isInteger(revision) || revision < 1) return true;
    if (current && revision <= current.revision) return true;
    if (!snapshot && current && revision !== current.revision + 1) return false;
    if (!snapshot && !current && revision !== 1) return false;
    this.publish({
      ...this.entries,
      [jobId]: {
        revision,
        result: snapshot
          ? sortFrequencyShapedRows(structuredClone(result))
          : mergeProvisionalResults(current?.result, result),
      },
    }, !current || snapshot);
    return true;
  }

  remove(jobId: string): void {
    if (!this.entries[jobId]) return;
    const entries = { ...this.entries };
    delete entries[jobId];
    this.publish(entries, true);
  }

  prune(validJobIds: ReadonlySet<string>): void {
    const entries = Object.fromEntries(Object.entries(this.entries).filter(([id]) => validJobIds.has(id)));
    if (Object.keys(entries).length === Object.keys(this.entries).length) return;
    this.publish(entries, true);
  }

  clear(): void {
    if (!Object.keys(this.entries).length) return;
    this.publish({}, true);
  }

  private publish(entries: Record<string, ProvisionalResultEntry>, immediate = false): void {
    this.entries = entries;
    if (!immediate && this.refreshIntervalMs > 0) {
      if (this.notifyTimer === null) {
        this.notifyTimer = setTimeout(() => {
          this.notifyTimer = null;
          this.snapshot = { version: this.snapshot.version + 1, entries: this.entries };
          this.listeners.forEach((listener) => listener());
        }, this.refreshIntervalMs);
      }
      return;
    }
    if (this.notifyTimer !== null) {
      clearTimeout(this.notifyTimer);
      this.notifyTimer = null;
    }
    this.snapshot = { version: this.snapshot.version + 1, entries: this.entries };
    this.listeners.forEach((listener) => listener());
  }
}

export const provisionalResults = new ProvisionalResultsStore();

export interface CompareSelection {
  primary: string | null;
  overlays: string[];
  /**
   * Whether the primary slot tracks the newest finished solve. It does until a
   * result is picked by hand, so a solve started from the workspace paints its
   * charts the moment its results land instead of waiting to be selected.
   */
  following: boolean;
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

export interface RecombineSpec {
  id?: string;
  members: string[];
  crossovers_hz: number[];
  level_match?: boolean;
  align?: boolean;
}

/** Recompute a job's combined channel from its stored complex bases. The
 * server persists the updated results, so the cache entry is replaced too. */
export async function recombineJobResults(
  jobId: string,
  spec: RecombineSpec,
  fetcher: typeof fetch = fetch,
): Promise<JobResults> {
  const response = await fetcher(`/api/results/${encodeURIComponent(jobId)}/combine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
  });
  if (!response.ok) {
    let detail = `Recombine request failed: ${response.status}`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* status is enough */ }
    throw new Error(detail);
  }
  const result = await response.json() as JobResults;
  resultsCache.set(jobId, result);
  return result;
}

export class CompareStore {
  private value: CompareSelection = { primary: null, overlays: [], following: true };
  private readonly listeners = new Set<() => void>();

  getSnapshot = (): CompareSelection => this.value;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  /** Deliberate selection: pins the slot so later solves do not steal it. */
  setPrimary(jobId: string | null): void {
    this.set({ primary: jobId, overlays: this.value.overlays.filter((id) => id !== jobId), following: false });
  }
  /** Automatic selection of the newest finished solve; keeps tracking it. */
  followLatest(jobId: string | null): void {
    this.set({ primary: jobId, overlays: this.value.overlays.filter((id) => id !== jobId), following: true });
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
      // Dropping the pinned result hands the slot back to the newest solve.
      this.set({ primary, overlays, following: primary === null });
    } else {
      this.set({ ...this.value, overlays: this.value.overlays.filter((id) => id !== jobId) });
    }
  }
  clear(): void { this.set({ primary: null, overlays: [], following: true }); }
  prune(validJobIds: ReadonlySet<string>): void {
    const primary = this.value.primary && validJobIds.has(this.value.primary) ? this.value.primary : null;
    const overlays = this.value.overlays.filter((id) => validJobIds.has(id) && id !== primary);
    if (primary === this.value.primary && overlays.length === this.value.overlays.length) return;
    this.set({ primary, overlays, following: primary === null ? true : this.value.following });
  }
  private set(value: CompareSelection): void {
    // A selection that did not change must not get a new identity. This store
    // is pruned on every jobs message, so during a solve it was handing out a
    // fresh snapshot several times a second; everything downstream that keys
    // off it -- the id list, the overlay list, and through them each chart's
    // ECharts option -- was rebuilt each time for an unchanged selection.
    if (
      value.primary === this.value.primary
      && value.following === this.value.following
      && value.overlays.length === this.value.overlays.length
      && value.overlays.every((id, index) => id === this.value.overlays[index])
    ) return;
    this.value = value;
    this.listeners.forEach((listener) => listener());
  }
}

export const compareSelection = new CompareStore();
