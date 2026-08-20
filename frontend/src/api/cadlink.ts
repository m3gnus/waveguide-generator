import { serializeDesign, type DesignDocument } from '../stores/design';
import { currentDesignWire } from '../stores/designWire';
import type { DesignIdentity } from '../stores/document';

export interface CadReturnSourceSummary {
  id: string;
  role: string;
  instanceId?: string | null;
  required: boolean;
  suggestedResolutionMm: number;
  defaultDriveChannelId: string;
}

export interface CadReturnInstanceSummary {
  instanceId: string;
  designId: string | null;
  occurrencePath: string | null;
  bodyObjectIds: string[];
  bodyFingerprintHash: string | null;
  transformHash: string | null;
  sourceIds: string[];
  driveChannelIds: string[];
}

export interface CadReturnBundle {
  name: string;
  bundlePath: string;
  modifiedAt: string;
  readable: boolean;
  documentName: string | null;
  requestId: string | null;
  sourceCount: number | null;
  instanceCount: number | null;
  /** Registry projects represented by linked instances in this return. */
  designIds?: string[];
  solverAnchorInstanceId?: string | null;
  instances?: CadReturnInstanceSummary[];
  sources: CadReturnSourceSummary[];
  reason?: string | null;
}

export interface CadReturnListing { items: CadReturnBundle[]; cadFolderConfigured: boolean }

export interface CadLinkedDesignSummary {
  designId: string;
  lineageId: string;
  editVersion: number;
  designHash: string;
  filename: string;
  branchedFromDesignId: string | null;
  branchedFromEditVersion: number | null;
  exportCount: number;
  lastExportedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface CadLinkedDesignSnapshot {
  designId: string;
  lineageId: string;
  editVersion: number;
  filename: string;
  updatedAt: string;
  text: string;
}

export type FusionCadState = 'closed' | 'addin_offline' | 'no_document' | 'not_linked' | 'instance_selection_required' | 'current' | 'stale';

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
  /** Absent when the active document is reported by an older WGLink add-in. */
  driftedParameters?: string[];
  localBodyState: 'unmodified' | 'modified' | 'missing' | 'unknown';
  bodyFingerprintHash: string | null;
  documentSignatureHash: string | null;
  documentBodyCount: number;
  sourceStateHash: string | null;
  exportId: string | null;
  exportSequence: string | null;
}

export type CadRealizedDimensionsState = 'no_link' | 'link_unavailable' | 'export_missing' | 'not_captured' | 'unavailable' | 'current' | 'stale';

export interface CadRealizedParameter {
  /** Values are document-global in CAD, so identity travels with every row. */
  instanceId: string | null;
  name: string;
  value: number;
  unit: string | null;
  role: string;
}

export interface CadRealizedDimensions {
  state: CadRealizedDimensionsState;
  instanceId: string | null;
  exportId: string | null;
  parameters: CadRealizedParameter[];
}

export interface FusionCadStatus {
  cadApplication: 'fusion360';
  cadFolderConfigured: boolean;
  cadFolderPath: string | null;
  cadConnectionIssue?: 'addin_upgrade_required' | 'folder_unreadable' | 'folder_mismatch' | null;
  adapterVersion?: string | null;
  workspaceRoot?: string | null;
  state: FusionCadState;
  /** Fusion process detection is separate from the WGLink heartbeat. */
  processRunning: boolean;
  running: boolean;
  updatedAt: string | null;
  documentName: string | null;
  documentId: string | null;
  currentFormula: string;
  fusionFormula: string | null;
  link: FusionCadLink | null;
  /** Every active-document link matching this design; repeated placements are not collapsed. */
  matchingLinks?: FusionCadLink[];
  selectedInstanceId?: string | null;
  wgChangesAvailable: boolean;
  fusionChangesAvailable: boolean;
  documentChanged: boolean;
  documentChangeDetectable: boolean;
  staleDetectionExplanation: string | null;
  realizedDimensions: CadRealizedDimensions;
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
  expectedDesignId: string | null;
  expectedInstanceId?: string | null;
  symmetryMode: 'auto' | 'full';
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

export interface CadMeshTriangleStats extends Record<string, unknown> {
  triangle_count: number;
  full_domain_triangle_count?: number;
  domain_multiplier?: number;
  vertex_count?: number;
}

export interface CadViewportMeshMetadata {
  available: boolean;
  content_sha256?: string;
  cache_key?: string;
  cache_hit?: boolean;
  transformed_geometry_hash?: string;
  stats?: CadMeshTriangleStats & { domain?: string; units?: string };
  metadata?: Record<string, unknown>;
  reason?: string;
}

export interface CadReturnIngestRecord {
  ingest_id: string;
  created_at: string;
  return_id: string;
  manifest_sha256: string;
  artifact_sha256: string;
  report_sha256: string;
  mesh_content_sha256?: string;
  acoustic_domain: string;
  scope: {
    status: string;
    degraded_skip_count: number;
    included?: Array<{ object_id?: string; name?: string; body_kind?: string; [key: string]: unknown }>;
    skipped?: Array<{ object_id?: string; name?: string; kind?: string; severity?: string; reason?: string; [key: string]: unknown }>;
    [key: string]: unknown;
  };
  evidence?: { instances?: unknown[]; fem_air_volumes?: unknown[]; [key: string]: unknown };
  identity?: {
    schema_version: 1;
    selected_instance_id: string | null;
    solver_anchor_instance_id: string | null;
    instances: Array<{
      instance_id: string;
      design_id: string | null;
      body_object_ids: string[];
      assembly_from_link: number[][];
      source_ids: string[];
      default_drive_channel_ids: string[];
    }>;
  };
  sources: CadReturnSource[];
  mesh_sizes: { rigid_size_mm: number; transition_mm: number; source_size_mm: Record<string, number> };
  mesh?: {
    stats: CadMeshTriangleStats;
    metadata?: Record<string, unknown>;
    integrity?: Record<string, unknown>;
  };
  viewport_mesh?: CadViewportMeshMetadata;
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
  readonly status: number | null;

  constructor(message: string, areaDriftSources: string[] = [], status: number | null = null) {
    super(message);
    this.name = 'CadLinkApiError';
    this.areaDriftSources = areaDriftSources;
    this.status = status;
  }
}

async function responseError(response: Response): Promise<CadLinkApiError> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return new CadLinkApiError(body.detail, [], response.status);
    if (body.detail && typeof body.detail === 'object') {
      const detail = body.detail as { message?: unknown; msg?: unknown; area_drift_sources?: unknown };
      const message = typeof detail.message === 'string'
        ? detail.message
        : typeof detail.msg === 'string' ? detail.msg : null;
      const sources = Array.isArray(detail.area_drift_sources)
        ? detail.area_drift_sources.filter((value): value is string => typeof value === 'string')
        : [];
      if (message) return new CadLinkApiError(message, sources, response.status);
    }
  } catch { /* status remains useful */ }
  return new CadLinkApiError(`Request failed: ${response.status} ${response.statusText}`.trim(), [], response.status);
}

async function jsonRequest<T>(path: string, init: RequestInit | undefined, fetcher: typeof fetch): Promise<T> {
  const response = await fetcher(path, init);
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

export function listReturns(fetcher: typeof fetch = fetch): Promise<CadReturnListing> {
  return jsonRequest('/api/cadlink/returns', undefined, fetcher);
}

export function listCadLinkedDesigns(
  fetcher: typeof fetch = fetch,
): Promise<{ items: CadLinkedDesignSummary[] }> {
  return jsonRequest('/api/cadlink/designs', undefined, fetcher);
}

export function getCadLinkedDesign(
  designId: string,
  fetcher: typeof fetch = fetch,
): Promise<CadLinkedDesignSnapshot> {
  return jsonRequest(`/api/cadlink/designs/${encodeURIComponent(designId)}`, undefined, fetcher);
}

export function getFusionCadStatus(
  design: DesignDocument,
  identity: DesignIdentity | null,
  returnBundlePath: string | null = null,
  fetcher: typeof fetch = fetch,
  instanceId: string | null = null,
): Promise<FusionCadStatus> {
  return jsonRequest('/api/cadlink/fusion-status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      design: currentDesignWire(serializeDesign(design)),
      identity,
      instanceId,
      returnBundlePath,
    }),
  }, fetcher);
}

export function requestFusionReturn(
  target: {
    designId: string;
    documentId: string;
    instanceId: string;
    expectedReturnStateHash: string | null;
  },
  fetcher: typeof fetch = fetch,
): Promise<{ status: 'requested'; requestId: string; documentName: string }> {
  return jsonRequest('/api/cadlink/request-fusion-return', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(target),
  }, fetcher);
}

export function ingestReturn(
  request: CadReturnIngestRequest,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<CadReturnIngestRecord> {
  return jsonRequest('/api/cadlink/ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  }, fetcher);
}

export function getIngest(id: string, fetcher: typeof fetch = fetch): Promise<CadReturnIngestRecord> {
  return jsonRequest(`/api/cadlink/ingest/${encodeURIComponent(id)}`, undefined, fetcher);
}

export interface PendingSolveCommand {
  commandId: string;
  returnId: string;
  bundlePath: string;
  manifestSha256: string;
  requestedAt: string;
}

export interface SolveCommandOutcome {
  state: 'accepted' | 'refused';
  jobId: string | null;
  reason: string | null;
  at: string;
}

/** The CAD-authored "solve this in WG" request, if one is pending.
 *
 * `outcome` is non-null when the command is already terminal — accepted (its
 * job exists) or refused — so the caller reports it instead of acting again. */
export function getSolveCommand(
  fetcher: typeof fetch = fetch,
): Promise<{ command: PendingSolveCommand | null; outcome?: SolveCommandOutcome | null }> {
  return jsonRequest('/api/cadlink/solve-command', undefined, fetcher);
}

export function reportSolveCommandOutcome(
  report: { commandId: string; state: 'accepted' | 'refused' | 'blocked'; jobId?: string | null; reason?: string | null },
  fetcher: typeof fetch = fetch,
): Promise<Record<string, unknown>> {
  return jsonRequest('/api/cadlink/solve-command/outcome', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(report),
  }, fetcher);
}
