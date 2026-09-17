import { jsonRequest } from './cadlink';

/**
 * CAD operations as the backend owns them (docs/architecture/CAD-OPERATIONS.md).
 *
 * The backend consumes Fusion's solve commands and prepares each one from its
 * project's own setup. The UI records that setup and the solver selection,
 * issues prepare, approvals and cancel, and observes the result.
 */
export interface CadOperationSummary {
  operationId: string;
  kind: string;
  /** "States": `received`, `processing`, `needs_user_input`, `accepted`,
   * `rejected`, `cancelled`, `recovery_required`, `cancel_requested`. */
  state: string;
  /** "Preparation": `received`, `validating`, `preparing-mesh`, `ready`, `submitted`.
   * A Fusion-bound request claimed live: `adapter-received`, `queued-for-fusion`,
   * `executing` (docs/reference/CADLINK-LIVE-PROTOCOL.md, section 7). */
  stage: string | null;
  /** A reason code ("Outcomes"), or null. */
  reason: string | null;
  message: string | null;
  jobId: string | null;
  attemptGeneration: number;
  setupRevisionId: string | null;
  preparationId: string | null;
  /** The snapshot the operation solves: its manifest and, once known, the CAD
   * document's name and the project it belongs to. A first-time document has
   * no project yet. */
  snapshot: {
    manifestSha256: string | null;
    documentName?: string | null;
    projectLineageId?: string | null;
  } | null;
  legacy: boolean;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface CadOperationPreparation {
  preparationId: string;
  ingestId: string;
  snapshotSha256: string;
  setupRevisionId: string;
  reportSha256: string;
  blockingFindingIds: string[];
  attemptGeneration: number;
}

export interface CadOperationDetail extends CadOperationSummary {
  /** `<report_sha256>:<finding_id>` for each approved blocking finding. */
  approvals: string[];
  preparation: CadOperationPreparation | null;
}

export interface CadSourceInventoryEntry {
  id: string;
  role: string;
  required: boolean;
}

/** One setup revision's content ("Setup revisions"): everything a solve of a
 * snapshot needs except the snapshot itself. */
export interface CadSolveSetup {
  schema_version: 1;
  geometry: Record<string, unknown>;
  options: Record<string, unknown>;
  preparation?: {
    area_drift_overrides: string[];
    symmetry_mode: 'auto' | 'full';
    surface_deviation_mm?: number | null;
  };
  driver_references?: Record<string, { driver_id: string; source?: string | null }>;
  label?: string | null;
}

/** Blocking findings the user reviewed, on the one preparation that reported them. */
export interface CadOperationApprovals {
  preparationId: string;
  findingIds: string[];
}

export interface PrepareCadOperationRequest {
  /** Omitted: the backend resolves the snapshot's project setup itself. */
  setupRevisionId?: string;
  submit?: boolean;
  approvals?: CadOperationApprovals;
}

export interface SetupRevisionSummary {
  revisionId: string;
  contentSha256: string;
  createdAt: string;
}

export interface SetupRevisionDetail extends SetupRevisionSummary {
  setup: CadSolveSetup;
}

const TERMINAL_STATES: ReadonlySet<string> = new Set(['accepted', 'rejected', 'cancelled']);

export function isPendingCadOperation(operation: Pick<CadOperationSummary, 'state'>): boolean {
  return !TERMINAL_STATES.has(operation.state);
}

const operationPath = (operationId: string): string =>
  `/api/cadlink/operations/${encodeURIComponent(operationId)}`;

function jsonBody(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

/** Store the exact setup a manual CAD solve will bind. */
export function createSetupRevision(
  setup: CadSolveSetup,
  fetcher: typeof fetch = fetch,
): Promise<SetupRevisionSummary> {
  return jsonRequest('/api/cadlink/setup-revisions', jsonBody('POST', { setup }), fetcher);
}

/** Read the immutable inputs a named setup revision bound. */
export function getSetupRevision(
  revisionId: string,
  fetcher: typeof fetch = fetch,
): Promise<SetupRevisionDetail> {
  return jsonRequest(`/api/cadlink/setup-revisions/${encodeURIComponent(revisionId)}`, undefined, fetcher);
}

/** Create or recover a backend-owned solve for one retained ingestion. */
export async function createCadOperation(
  request: { operationId: string; ingestId: string },
  fetcher: typeof fetch = fetch,
): Promise<CadOperationSummary> {
  const response = await jsonRequest<{ operation: CadOperationSummary }>(
    '/api/cadlink/operations', jsonBody('POST', request), fetcher,
  );
  return response.operation;
}

/** Record a project's solve settings for its source inventory. */
export function putProjectSetup(
  request: { lineageId: string; inventory: CadSourceInventoryEntry[]; setup: CadSolveSetup },
  fetcher: typeof fetch = fetch,
): Promise<{ lineageId: string; inventorySha256: string; revisionId: string }> {
  return jsonRequest('/api/cadlink/project-setups', jsonBody('PUT', request), fetcher);
}

/** Record the engine selected in WG's solver selector. */
export function putSolverSelection(
  engine: string,
  fetcher: typeof fetch = fetch,
): Promise<{ engine: string }> {
  return jsonRequest('/api/cadlink/solver-selection', jsonBody('PUT', { engine }), fetcher);
}

/** The unfinished operations, unless `pending` is false. */
export async function listCadOperations(
  options: { pending?: boolean } = {},
  fetcher: typeof fetch = fetch,
): Promise<CadOperationSummary[]> {
  const path = options.pending === false
    ? '/api/cadlink/operations?pending=false'
    : '/api/cadlink/operations';
  const body = await jsonRequest<{ operations: CadOperationSummary[] }>(path, undefined, fetcher);
  return body.operations;
}

export function getCadOperation(
  operationId: string,
  fetcher: typeof fetch = fetch,
): Promise<CadOperationDetail> {
  return jsonRequest(operationPath(operationId), undefined, fetcher);
}

/** Start a preparation, which takes over any attempt already running. */
export async function prepareCadOperation(
  operationId: string,
  request: PrepareCadOperationRequest = {},
  fetcher: typeof fetch = fetch,
): Promise<CadOperationSummary> {
  const body = {
    ...(request.setupRevisionId ? { setupRevisionId: request.setupRevisionId } : {}),
    submit: request.submit ?? true,
    ...(request.approvals ? { approvals: request.approvals } : {}),
  };
  const response = await jsonRequest<{ operation: CadOperationSummary }>(
    `${operationPath(operationId)}/prepare`, jsonBody('POST', body), fetcher,
  );
  return response.operation;
}

export function approveCadOperationFindings(
  operationId: string,
  approvals: CadOperationApprovals,
  fetcher: typeof fetch = fetch,
): Promise<CadOperationDetail> {
  return jsonRequest(`${operationPath(operationId)}/approvals`, jsonBody('POST', approvals), fetcher);
}

/** Dismiss: at once when idle, at the attempt's next step when one runs. */
export function cancelCadOperation(
  operationId: string,
  fetcher: typeof fetch = fetch,
): Promise<CadOperationSummary> {
  return jsonRequest(`${operationPath(operationId)}/cancel`, { method: 'POST' }, fetcher);
}

/** Re-read Fusion's heartbeat and link evidence for an interrupted mutation. */
export async function reconcileCadOperation(
  operationId: string,
  fetcher: typeof fetch = fetch,
): Promise<CadOperationSummary> {
  const response = await jsonRequest<{ operation: CadOperationSummary }>(
    `${operationPath(operationId)}/reconcile`, { method: 'POST' }, fetcher,
  );
  return response.operation;
}
