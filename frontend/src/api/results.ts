import type { CrossoverChannelWire } from '../results/crossoverSpec';
export type NullableNumber = number | null;
export type PolarSample = [number, NullableNumber | [number, number]];

/** Quantity payload shared by final envelopes, live deltas, and nested channels. */
export interface ResultData {
  result_kind?: 'parametric' | 'multi_channel';
  result_contract_version?: number;
  client_request_id?: string | null;
  client_metadata?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
  /**
   * May be absent at the top level of a stored multi_channel envelope (older
   * solves kept frequencies per channel only); every frequency-shaped consumer
   * must guard with Array.isArray. The type stays non-optional because making
   * it `number[] | undefined` ripples through dozens of parametric-path
   * consumers that are only ever handed frequency-shaped payloads.
   */
  frequencies: number[];
  directivity?: {
    horizontal?: PolarSample[][];
    vertical?: PolarSample[][];
    diagonal?: PolarSample[][];
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
  channels?: Record<string, ResultData>;
  channel_order?: string[];
}

/** A durable result returned by the final-result HTTP endpoints. */
export interface JobResults extends ResultData {
  result_kind: 'parametric' | 'multi_channel';
  result_contract_version: 1 | 2;
}

export interface RadiationImpedanceAperture {
  name: string;
  area_m2: number;
  tag: number;
}

export interface RadiationImpedancePresentation {
  schema_version: 1;
  quantity: 'average_aperture_pressure_per_volume_velocity';
  units: 'Pa*s/m^3';
  phase_time_convention: 'engineering_exp_plus_jwt';
  frequencies_hz: number[];
  apertures: RadiationImpedanceAperture[];
  engineering_matrix: {
    real: number[][][];
    imaginary: number[][][];
  };
  in_phase_termination: {
    aperture_names: string[];
    real: number[][];
    imaginary: number[][];
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isSafeJson(value: unknown, depth = 0): boolean {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (isFiniteNumber(value)) return true;
  if (depth >= 40) return false;
  if (Array.isArray(value)) return value.every((item) => isSafeJson(item, depth + 1));
  if (!isRecord(value)) return false;
  return Object.entries(value).every(([key, item]) => (
    key !== '__proto__' && key !== 'constructor' && key !== 'prototype' && isSafeJson(item, depth + 1)
  ));
}

function isResultData(value: unknown, depth = 0): value is ResultData {
  if (depth >= 8 || !isRecord(value) || !isSafeJson(value)) return false;
  // ``frequencies`` is per-channel in a multi_channel envelope; the top level
  // may omit it, so validate the field only when present.
  if ('frequencies' in value && !(
    Array.isArray(value.frequencies) && value.frequencies.every(isFiniteNumber)
  )) return false;
  if ('metadata' in value && !isRecord(value.metadata)) return false;
  if ('channel_order' in value && !(
    Array.isArray(value.channel_order) && value.channel_order.every((item) => typeof item === 'string')
  )) return false;
  if ('channels' in value && !(
    isRecord(value.channels) && Object.values(value.channels).every((channel) => isResultData(channel, depth + 1))
  )) return false;
  return true;
}

function isResultProvenance(value: unknown): boolean {
  if (!isRecord(value) || !isSafeJson(value)) return false;
  const shaFields = [
    'request_sha256', 'geometry_sha256', 'solve_options_sha256',
    'execution_request_sha256', 'execution_geometry_sha256', 'execution_solve_options_sha256',
    'effective_request_sha256', 'effective_geometry_sha256', 'effective_solve_options_sha256',
  ];
  return value.schema_version === 1
    && typeof value.wg_version === 'string'
    && isRecord(value.dependency_shas)
    && Object.values(value.dependency_shas).every((sha) => typeof sha === 'string')
    && value.request_identity === 'execution'
    && typeof value.resolved_engine === 'string'
    && shaFields.every((key) => typeof value[key] === 'string' && /^[0-9a-f]{64}$/.test(value[key]));
}

/** Validate the OpenAPI result union before a durable response can enter UI state. */
export function parseFinalResultEnvelope(value: unknown): JobResults {
  if (!isRecord(value)) throw new Error('invalid final result envelope');
  if (!('result_contract_version' in value) && !('result_kind' in value) && !('channels' in value)) {
    // Results migrated from the original application predate the envelope
    // fields; their shape is the parametric v1 contract, so adopt that
    // identity rather than refusing a documented, supported input. A payload
    // that declares either field is held to the declared contract below.
    // Top-level ``frequencies`` is the identity of that frequency-shaped
    // contract, so require it explicitly before adopting the payload.
    if (!Array.isArray(value.frequencies) || !isResultData(value)) {
      throw new Error('invalid final result envelope');
    }
    return { ...value, result_kind: 'parametric', result_contract_version: 1 } as JobResults;
  }
  if (!('result_contract_version' in value)) {
    throw new Error('final result is missing result_contract_version');
  }
  const version = value.result_contract_version;
  if (version !== 1 && version !== 2) {
    throw new Error(`unsupported result version ${String(version)}`);
  }
  if (!('result_kind' in value)) throw new Error('final result is missing result_kind');
  const supportedIdentity = (value.result_kind === 'parametric' && version === 1)
    || (value.result_kind === 'multi_channel' && version === 2);
  if (!supportedIdentity) throw new Error(`unsupported result version ${String(version)}`);
  // Provenance and client fields are validated when present; results persisted
  // before a field existed are still readable, only the contract identity is
  // mandatory.
  if (!isResultData(value)
    || !('client_request_id' in value
      ? value.client_request_id === null || typeof value.client_request_id === 'string' : true)
    || ('client_metadata' in value && !(isRecord(value.client_metadata) && isSafeJson(value.client_metadata)))
    || !isRecord(value.metadata)
    || ('provenance' in value && value.provenance !== null && !isResultProvenance(value.provenance))) {
    throw new Error('invalid final result envelope');
  }
  if (value.result_kind === 'multi_channel' && !(
    isRecord(value.channels) && Array.isArray(value.channel_order)
  )) throw new Error('invalid final result envelope');
  return value as unknown as JobResults;
}

function sortFrequencyShapedRows(result: ResultData): ResultData {
  const record = result as ResultData & Record<string, unknown>;
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

export function mergeProvisionalResults(current: ResultData | undefined, delta: ResultData): ResultData {
  if (!current) return sortFrequencyShapedRows(structuredClone(delta));
  // Copy only the branches receiving a new row. Deep-cloning the accumulated
  // sweep for every frequency makes a 401-point solve quadratic in payload
  // size before ECharts even sees it.
  const merged = { ...current } as ResultData & Record<string, unknown>;
  const incoming = delta as ResultData & Record<string, unknown>;
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
    Object.entries(incoming.channels as Record<string, ResultData>).forEach(([id, result]) => {
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
  result: ResultData;
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
  apply(jobId: string, revision: number, result: ResultData, snapshot = false): boolean {
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
  /**
   * The run a submission is waiting on: the solve the user just started, which
   * takes the primary slot the moment its results exist even while another
   * result is pinned. Pressing Solve is a request to see that solve, and a
   * comparison pinned earlier must not swallow it silently.
   */
  awaiting: string | null;
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
    const result = parseFinalResultEnvelope(await response.json());
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

export interface JobArchiveSnapshot {
  schema_version: 1;
  results: JobResults;
  results_sha256: string;
  mesh_artifact: string | null;
  pressure_bases: Array<{ channel_id: string; content_base64: string }>;
  radiation_impedance: {
    content_base64: string;
    presentation: RadiationImpedancePresentation;
  } | null;
}

/**
 * Fetch every retention-managed input for a permanent run archive at once.
 *
 * The server copies these members inside one store transaction.  Callers must
 * not mix this payload with the ordinary per-artifact endpoints: doing so
 * would reintroduce a window where retention can delete a later member.
 */
export async function fetchJobArchiveSnapshot(
  jobId: string,
  fetcher: typeof fetch = fetch,
): Promise<JobArchiveSnapshot> {
  const response = await fetcher(`/api/jobs/${encodeURIComponent(jobId)}/archive-snapshot`);
  if (!response.ok) {
    let detail = `Archive snapshot request failed: ${response.status}`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch { /* status is enough */ }
    throw new Error(detail);
  }
  const value = await response.json();
  if (!isRecord(value)) throw new Error('invalid archive snapshot');
  const snapshot = { ...value, results: parseFinalResultEnvelope(value.results) } as unknown as JobArchiveSnapshot;
  resultsCache.set(jobId, snapshot.results);
  return snapshot;
}

/** The optional matrix artifact is stored separately from the result JSON.
 * A 404 means the job simply has no retained matrix; other failures remain
 * visible to callers that are explicitly exporting it. */
export async function fetchRadiationImpedancePresentation(
  jobId: string,
  fetcher: typeof fetch = fetch,
): Promise<RadiationImpedancePresentation | null> {
  const response = await fetcher(`/api/radiation-impedance/${encodeURIComponent(jobId)}/presentation`);
  if (response.status === 404) return null;
  if (!response.ok) {
    let detail = `Radiation-impedance request failed: ${response.status}`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch { /* status is enough */ }
    throw new Error(detail);
  }
  return response.json() as Promise<RadiationImpedancePresentation>;
}

/** The recombine body. New requests always send the per-channel v2 form; the
 * legacy triple stays typed because the server still accepts it and because a
 * stored job's own combine can be replayed as one. */
export interface RecombineSpec {
  id?: string;
  members: string[];
  reference?: string;
  channels?: Record<string, CrossoverChannelWire>;
  crossovers_hz?: number[];
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
  const result = parseFinalResultEnvelope(await response.json());
  resultsCache.set(jobId, result);
  return result;
}

export class CompareStore {
  private value: CompareSelection = { primary: null, overlays: [], following: true, awaiting: null };
  private readonly listeners = new Set<() => void>();

  getSnapshot = (): CompareSelection => this.value;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  /** Deliberate selection: pins the slot so later solves do not steal it. */
  setPrimary(jobId: string | null): void {
    this.set({ primary: jobId, overlays: this.value.overlays.filter((id) => id !== jobId), following: false, awaiting: null });
  }
  /** Automatic selection of the newest finished solve; keeps tracking it. */
  followLatest(jobId: string | null): void {
    this.set({ primary: jobId, overlays: this.value.overlays.filter((id) => id !== jobId), following: true, awaiting: null });
  }
  /**
   * Claim the primary slot for a run that was just submitted.
   *
   * The slot is not taken now -- the run has no results yet, and yanking the
   * charts to some other solve at submission time would be worse than what
   * this fixes. It is taken by whoever resolves the claim once results exist
   * (shell/ResultsPanel), so a pinned comparison stays on screen for the
   * length of the solve and is replaced by the run the user asked for.
   */
  awaitRun(jobId: string | null): void {
    this.set({ ...this.value, awaiting: jobId });
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
      this.set({ primary, overlays, following: primary === null, awaiting: this.value.awaiting });
    } else {
      this.set({ ...this.value, overlays: this.value.overlays.filter((id) => id !== jobId) });
    }
  }
  clear(): void { this.set({ primary: null, overlays: [], following: true, awaiting: null }); }
  prune(validJobIds: ReadonlySet<string>): void {
    const primary = this.value.primary && validJobIds.has(this.value.primary) ? this.value.primary : null;
    const overlays = this.value.overlays.filter((id) => validJobIds.has(id) && id !== primary);
    if (primary === this.value.primary && overlays.length === this.value.overlays.length) return;
    // `awaiting` is deliberately not pruned: this runs on every jobs message,
    // and a run claimed at submission time is routinely not in the list yet.
    this.set({ primary, overlays, following: primary === null ? true : this.value.following, awaiting: this.value.awaiting });
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
      && value.awaiting === this.value.awaiting
      && value.overlays.length === this.value.overlays.length
      && value.overlays.every((id, index) => id === this.value.overlays[index])
    ) return;
    this.value = value;
    this.listeners.forEach((listener) => listener());
  }
}

export const compareSelection = new CompareStore();
