/**
 * The design assembly endpoints (`server/jobs/models_api.py`): the front
 * view's solver-free context, and compiling one captured design revision into
 * its design model's immutable prepared mesh.
 */
import { ImportedModelApiError, requestImportedModel, type ImportedModelSummary, type PreparedSummary } from './importedModels';

/** Live outlines and the design mesher's own woofer placement, in horn-throat mm. */
export interface AssemblyFrontContext {
  refusal?: string;
  frame?: 'horn-throat-pre-placement-mm';
  mouth_bottom_mm?: number;
  mouth_top_mm?: number;
  woofers?: Array<{ radiator_id: string; x_mm: number; y_mm: number; radius_mm: number }>;
  mouth_outline_mm?: Array<[number, number]>;
  baffle?: { x0_mm: number; x1_mm: number; y0_mm: number; y1_mm: number; edge_mm: number } | null;
  /** Reference distances; the build certifies the 0.1 mm minimum exactly. */
  clearances?: Record<string, { mouth_mm: number; baffle_edge_mm?: number; woofers: Record<string, number> }>;
  minimum_clearance_mm?: number;
}

export interface AssemblyRadiator {
  radiator_id: string;
  kind: 'classic' | 'piston' | 'ath_cone';
  label: string;
  state: 'compiled' | 'disabled' | 'rigid';
  role: 'HF' | 'LF';
  motion: 'normal' | 'axial';
  motion_axis?: [number, number, number];
  source_ids: string[];
  center_mm?: [number, number];
  radius_mm?: number;
  projected_area_m2?: number;
}

/** A compiled assembly record's metadata, with mesh text when the client needs it. */
export interface CompiledAssemblyMetadata extends PreparedSummary {
  producer: 'design-assembly';
  design: { design_uid: string; name: string; revision: number; content_sha256: string;
    assembly_sha256: string; geometry_sha256: string };
  radiators: AssemblyRadiator[];
  excitations: Array<{ id: string; source_ids: string[]; motion: 'normal' | 'axial';
    motion_axis?: [number, number, number]; patch_weights: Record<string, number> }>;
  signal_groups: Array<{ id: string; label: string; members: string[] }>;
  sources: Array<{ id: string; role: string; radiator_id: string; segment_id: string | null }>;
  source_tags: Record<string, number>;
}

/** A compiled assembly record with its solver mesh available locally. */
export interface CompiledAssembly extends CompiledAssemblyMetadata { msh_text: string }

export type AssemblyCompileResult =
  | { status: 'published'; reused: boolean; model: ImportedModelSummary; prepared: CompiledAssembly }
  | { status: 'superseded' };

const post = <T>(path: string, body: unknown, fetcher: typeof fetch, what: string, signal?: AbortSignal) =>
  requestImportedModel<T>(path, fetcher, what, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), ...(signal ? { signal } : {}),
  });

/**
 * What to tell the user about a failed assembly request. A refusal the server
 * words is about their design; anything else is ours, and `fallback` says so.
 */
export function assemblyProblemText(reason: unknown, fallback: string): string {
  return reason instanceof ImportedModelApiError && reason.code !== null ? reason.message : fallback;
}

/** Whether a failed front context is the server's refusal of the design, not a check that could not run. */
export const assemblyContextRefused = (reason: unknown) => reason instanceof ImportedModelApiError && reason.code === 'assembly_refused';

/** The most one fit check may take; Build and Solve wait on it. The answer normally takes well under 100 ms. */
export const FRONT_CONTEXT_LIMIT_MS = 5000;

/** The solver-free context of a design, or a rejection within `FRONT_CONTEXT_LIMIT_MS`. */
export function fetchAssemblyContext(design: Record<string, unknown>, fetcher: typeof fetch = fetch, signal?: AbortSignal): Promise<AssemblyFrontContext> {
  const request = new AbortController();
  signal?.addEventListener('abort', () => request.abort(), { once: true });
  return new Promise((resolve, reject) => {
    const limit = setTimeout(() => {
      reject(new Error(`No front context after ${FRONT_CONTEXT_LIMIT_MS} ms.`));
      request.abort();
    }, FRONT_CONTEXT_LIMIT_MS);
    post<AssemblyFrontContext>('/api/models/assembly/context', { design }, fetcher, 'The front view', request.signal)
      .then(resolve, reject).finally(() => clearTimeout(limit));
  });
}

/** The server may omit only the mesh text of the immutable record we already hold. */
type AssemblyCompileResponse =
  | { status: 'published'; reused: boolean; model: ImportedModelSummary;
    prepared: CompiledAssemblyMetadata }
  | { status: 'superseded' };

export async function compileAssembly(body: { design: Record<string, unknown>; design_uid: string; design_name: string;
  design_revision: number; build_session: string },
  fetcher: typeof fetch = fetch, signal?: AbortSignal, knownPrepared: CompiledAssembly | null = null): Promise<AssemblyCompileResult> {
  const result = await post<AssemblyCompileResponse>('/api/models/assembly/compile', {
    ...body, ...(knownPrepared ? { known_prepared_id: knownPrepared.ingest_id } : {}),
  }, fetcher, 'The assembly', signal);
  if (result.status !== 'published') return result;
  const mshText = result.prepared.msh_text
    ?? (knownPrepared && knownPrepared.ingest_id === result.prepared.ingest_id ? knownPrepared.msh_text : undefined);
  if (mshText === undefined) throw new Error('The assembly mesh was not returned. Build the assembly again, then press Solve.');
  return { ...result, prepared: { ...result.prepared, msh_text: mshText } };
}
