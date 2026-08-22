import { serializeSolveDesign, type DesignDocument } from '../stores/design';
import { useSolveOptionsStore, type SolveOptions } from '../stores/solveOptions';

export interface EngineCapability {
  name: string;
  available: boolean;
  reason: string | null;
  version: string | null;
  fast_paths: string[];
  formulations?: string[];
  mountings?: string[];
  geometry_sources?: string[];
  symmetry_domains?: string[];
  field_traces?: boolean;
  di_sphere?: boolean;
  cancellation_granularity?: string;
}

export interface EngineSelection {
  readonly default: string;
  readonly resolvedDefault: string | null;
  readonly full3dOrder: readonly string[];
  readonly axisymmetricRunner: string;
}

export interface Capabilities {
  engines: EngineCapability[];
  engineSelection: EngineSelection;
}
export interface SolveSubmissionMetadata { label: string; designRevision: number }
export interface ImportedGeometrySubmission {
  type: 'imported';
  ingest_id: string;
  manifest_sha256: string;
  artifact_sha256: string;
  drive_channels: Array<{
    id: string;
    source_ids: string[];
    motion: 'normal' | 'axial';
    driver?: Record<string, number>;
  }>;
  combine?: {
    id?: string;
    members: string[];
    crossovers_hz: number[];
    level_match?: boolean;
    align?: boolean;
  };
  drive_voltage_v?: number;
  rg_ohm?: number;
  mesh: { rigid_size_mm: number; transition_mm: number; source_size_mm: Record<string, number> };
  acknowledged_findings: string[];
  skipped_source_ids: string[];
  exterior_only?: boolean;
  // Passive-cardioid campaign inputs. Every one is optional on the wire and
  // they travel as a set: `passive_cardioid_rear_volume_l` is the opt-in
  // boundary, and any other field without it is refused by name.
  passive_cardioid_rear_volume_l?: number;
  passive_cardioid_port_length_mm?: number;
  model_port_area_m2?: number;
  bem_port_area_m2?: number;
  port_area_source?: 'user' | 'bem_aperture';
  passive_cardioid_foam_resistance_pa_s_m3?: number;
  passive_cardioid_invert_port?: boolean;
  passive_cardioid_coupled?: boolean;
}
export interface ImportedSolveSubmission {
  geometry: ImportedGeometrySubmission;
  options: SolveOptions & { frequency_range?: [number, number]; num_frequencies?: number };
}
export interface SymmetryResolution {
  quadrants: 1 | 12 | 14 | 1234;
  xz: boolean;
  yz: boolean;
  reasons: { xz: string[]; yz: string[] };
  tolerance_mm: number;
  relative_tolerance: number;
}

export function toSolveDesign(design: DesignDocument): Record<string, unknown> {
  return serializeSolveDesign(design);
}

export function formatApiDetail(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const items = value.map((item) => formatApiDetail(item)).filter((item): item is string => Boolean(item));
    return items.length ? items.join('; ') : null;
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const message = typeof record.msg === 'string' ? record.msg : typeof record.message === 'string' ? record.message : null;
    const location = Array.isArray(record.loc) ? record.loc.map(String).join('.') : null;
    if (message) return location ? `${location}: ${message}` : message;
    const entries = Object.entries(record).flatMap(([key, item]) => {
      const formatted = formatApiDetail(item);
      return formatted ? [`${key}: ${formatted}`] : [];
    });
    return entries.length ? entries.join('; ') : null;
  }
  return value === null || value === undefined ? null : String(value);
}

async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    const formatted = formatApiDetail(body.detail);
    if (formatted) return formatted;
  } catch { /* fall through */ }
  return `${response.status} ${response.statusText}`.trim();
}

export async function getCapabilities(fetcher: typeof fetch = fetch): Promise<Capabilities> {
  const response = await fetcher('/api/capabilities');
  if (!response.ok) throw new Error(await detail(response));
  return response.json() as Promise<Capabilities>;
}

export function resolveEngine(
  engine: string,
  capabilities: {
    engines: readonly EngineCapability[];
    engineSelection?: EngineSelection;
  },
  solverMode = 'auto',
): string {
  const selection = capabilities.engineSelection;
  if (solverMode === 'circsym') {
    const runner = selection?.axisymmetricRunner.trim().toLowerCase();
    const axisym = capabilities.engines.find((item) => item.available
      && item.name.toLowerCase() === runner);
    if (axisym) return runner!;
    const advertised = capabilities.engines.find((item) => item.name.toLowerCase() === runner);
    const detail = advertised?.reason ? ` ${advertised.reason}` : '';
    throw new Error(runner
      ? `Forced Axisymmetric mode requires the advertised ${runner} runner, but it is unavailable.${detail}`
      : 'Forced Axisymmetric mode is unavailable because the server advertised no axisymmetric runner.');
  }
  if (engine.toLowerCase() !== 'auto') return engine.toLowerCase();
  const order = selection?.full3dOrder.map((name) => name.toLowerCase())
    ?? capabilities.engines
      .filter((item) => item.formulations?.includes('full-3d'))
      .map((item) => item.name.toLowerCase());
  const resolvedDefault = selection?.resolvedDefault?.toLowerCase() ?? null;
  const available = capabilities.engines.find((item) => item.available
    && item.name.toLowerCase() === resolvedDefault)
    ?? order.flatMap((name) => capabilities.engines.filter((item) => item.available
      && item.name.toLowerCase() === name))[0];
  if (available) return available.name.toLowerCase();
  // An Axisym-only host is not a host without a solver. The server planner
  // selects the meridian runner for an eligible circular design before any
  // full-3D fallback, so throwing here left JobsCoordinator with no capability
  // and Run blocked on a design the server would have solved -- escapable only
  // by knowing to force the meridian mode by hand. Geometry eligibility is the
  // planner's to judge, not this function's.
  const runner = selection?.axisymmetricRunner.toLowerCase();
  const axisym = capabilities.engines.find((item) => item.available
    && item.name.toLowerCase() === runner);
  if (axisym) return runner!;
  throw new Error('No solver backend is currently available');
}

export async function fetchSymmetry(
  design: DesignDocument,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<SymmetryResolution> {
  return postSymmetry(JSON.stringify(toSolveDesign(design)), fetcher, signal);
}

/**
 * Resolve symmetry for an already-serialised solve design.
 *
 * Callers that cache on the wire payload need to send exactly the bytes they
 * keyed on; re-serialising the live design here would race an edit that landed
 * between keying and sending.
 *
 * `signal` abandons the response, it does not cancel the work: the endpoint
 * resolves in `asyncio.to_thread`, which runs to completion regardless.
 */
export async function postSymmetry(
  body: string,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<SymmetryResolution> {
  const response = await fetcher('/api/design/symmetry', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    signal,
  });
  if (!response.ok) throw new Error(await detail(response));
  return response.json() as Promise<SymmetryResolution>;
}

export async function submitDesign(
  design: DesignDocument,
  requestedOptions: SolveOptions = useSolveOptionsStore.getState().options(),
  fetcher: typeof fetch = fetch,
  metadata: SolveSubmissionMetadata = { label: 'waveguide', designRevision: 0 },
): Promise<string> {
  const options = structuredClone(requestedOptions);
  const wire = toSolveDesign(design);
  const response = await fetcher('/api/solve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      design: wire,
      options,
      label: metadata.label,
      design_revision: metadata.designRevision,
      design_snapshot: { version: 1, design: wire },
    }),
  });
  if (!response.ok) throw new Error(await detail(response));
  return ((await response.json()) as { job_id: string }).job_id;
}

export async function submitImported(
  submission: ImportedSolveSubmission,
  fetcher: typeof fetch = fetch,
  label = 'cad-import',
): Promise<string> {
  const response = await fetcher('/api/solve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...submission, label }),
  });
  if (!response.ok) throw new Error(await detail(response));
  return ((await response.json()) as { job_id: string }).job_id;
}
