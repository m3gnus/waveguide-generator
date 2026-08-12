import { serializeDesign, type DesignDocument } from '../stores/design';
import type { DesignIdentity } from '../stores/document';

export interface CadReturnSourceSummary {
  id: string;
  role: string;
  required: boolean;
  suggestedResolutionMm: number;
  defaultDriveChannelId: string;
}

export interface CadReturnBundle {
  name: string;
  bundlePath: string;
  modifiedAt: string;
  readable: boolean;
  documentName: string | null;
  sourceCount: number | null;
  instanceCount: number | null;
  sources: CadReturnSourceSummary[];
  reason?: string | null;
}

export interface CadReturnListing { items: CadReturnBundle[] }

export type FusionCadState = 'closed' | 'no_document' | 'not_linked' | 'current' | 'stale';

export interface FusionCadLink {
  instanceId: string;
  bundlePath: string | null;
  designId: string | null;
  lineageId: string | null;
  editVersion: string | null;
  designHash: string | null;
  designName: string | null;
  formula: string | null;
  configPresent: boolean;
  parameterCount: number;
  parameterDriftCount: number;
  localBodyState: 'unmodified' | 'modified' | 'missing' | 'unknown';
  exportId: string | null;
  exportSequence: string | null;
}

export interface FusionCadStatus {
  cadApplication: 'fusion360';
  state: FusionCadState;
  running: boolean;
  updatedAt: string | null;
  documentName: string | null;
  currentFormula: string;
  fusionFormula: string | null;
  link: FusionCadLink | null;
}

export interface CadReturnIngestRequest {
  bundlePath: string;
  mesh: {
    rigidSizeMm: number;
    transitionMm: number;
    sourceSizeMm: Record<string, number>;
  };
  skippedSourceIds: string[];
  areaDriftOverrides: string[];
}

export interface CadReturnSource {
  id: string;
  role: string;
  instance_id: string | null;
  required: boolean;
  default_drive_channel_id: string;
  suggested_resolution_mm: number;
  observed?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface CadReturnFinding {
  id: string;
  kind: string;
  blocking: boolean;
  source_id?: string;
  instance_id?: string;
  reason?: string;
  verdict?: string;
  [key: string]: unknown;
}

export interface CadReturnFreshnessInstance {
  instance_id: string;
  verdict: 'current' | 'body_modified' | 'missing_design' | 'design_changed' | 'generator_changed' | 'unknown';
  local_body_state?: string;
  fingerprints_match?: boolean | null;
  error?: string | null;
  facts?: unknown[];
}

export interface CadReturnIngestRecord {
  ingest_id: string;
  created_at: string;
  return_id: string;
  manifest_sha256: string;
  artifact_sha256: string;
  report_sha256: string;
  acoustic_domain: string;
  scope: {
    status: string;
    degraded_skip_count: number;
    included?: Array<{ object_id?: string; name?: string; body_kind?: string; [key: string]: unknown }>;
    skipped?: Array<{ object_id?: string; name?: string; kind?: string; severity?: string; reason?: string; [key: string]: unknown }>;
    [key: string]: unknown;
  };
  evidence?: { instances?: unknown[]; fem_air_volumes?: unknown[]; [key: string]: unknown };
  sources: CadReturnSource[];
  mesh_sizes: { rigid_size_mm: number; transition_mm: number; source_size_mm: Record<string, number> };
  skipped_source_ids: string[];
  freshness: {
    verdict: 'unlinked' | 'per-instance';
    instances: CadReturnFreshnessInstance[];
    finding_id?: string;
  };
  findings: CadReturnFinding[];
  symmetry: {
    cut_planes?: string[];
    planes?: Record<string, { accepted?: boolean; residuals?: unknown; [key: string]: unknown }>;
    [key: string]: unknown;
  };
  healing: { performed?: boolean; attempted?: boolean; mode?: string; [key: string]: unknown };
  sizing_estimate: Record<string, unknown>;
  polar_grid_derivation: Record<string, unknown>;
  tag_map: Record<string, unknown>;
  role_findings?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export class CadLinkApiError extends Error {
  readonly areaDriftSources: string[];

  constructor(message: string, areaDriftSources: string[] = []) {
    super(message);
    this.name = 'CadLinkApiError';
    this.areaDriftSources = areaDriftSources;
  }
}

async function responseError(response: Response): Promise<CadLinkApiError> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return new CadLinkApiError(body.detail);
    if (body.detail && typeof body.detail === 'object') {
      const detail = body.detail as { message?: unknown; msg?: unknown; area_drift_sources?: unknown };
      const message = typeof detail.message === 'string'
        ? detail.message
        : typeof detail.msg === 'string' ? detail.msg : null;
      const sources = Array.isArray(detail.area_drift_sources)
        ? detail.area_drift_sources.filter((value): value is string => typeof value === 'string')
        : [];
      if (message) return new CadLinkApiError(message, sources);
    }
  } catch { /* status remains useful */ }
  return new CadLinkApiError(`Request failed: ${response.status} ${response.statusText}`.trim());
}

async function jsonRequest<T>(path: string, init: RequestInit | undefined, fetcher: typeof fetch): Promise<T> {
  const response = await fetcher(path, init);
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

export function listReturns(fetcher: typeof fetch = fetch): Promise<CadReturnListing> {
  return jsonRequest('/api/cadlink/returns', undefined, fetcher);
}

export function getFusionCadStatus(
  design: DesignDocument,
  identity: DesignIdentity | null,
  fetcher: typeof fetch = fetch,
): Promise<FusionCadStatus> {
  return jsonRequest('/api/cadlink/fusion-status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: serializeDesign(design), identity }),
  }, fetcher);
}

export function ingestReturn(request: CadReturnIngestRequest, fetcher: typeof fetch = fetch): Promise<CadReturnIngestRecord> {
  return jsonRequest('/api/cadlink/ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }, fetcher);
}

export function getIngest(id: string, fetcher: typeof fetch = fetch): Promise<CadReturnIngestRecord> {
  return jsonRequest(`/api/cadlink/ingest/${encodeURIComponent(id)}`, undefined, fetcher);
}
