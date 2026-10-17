import { formatApiDetail } from '../jobs/actions';
import type { ImportedSetup } from '../jobs/importedSetup';

/** A model's setup, for the sources of one of its prepared meshes (the server reads them). */
async function request(modelId: string, preparedId: string, setup?: ImportedSetup, fetcher: typeof fetch = fetch) {
  const path = `/api/models/${encodeURIComponent(modelId)}/setup`;
  const response = await fetcher(setup ? path : `${path}?prepared_id=${encodeURIComponent(preparedId)}`, setup ? {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prepared_id: preparedId, setup: { schema_version: 1, ...setup } }),
  } : undefined);
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(formatApiDetail(error?.detail) ?? `Setup unavailable (${response.status}).`);
  }
  return response.json();
}

export const saveImportedSetup = (modelId: string, preparedId: string, setup: ImportedSetup, fetcher?: typeof fetch):
  Promise<{ revision_id: string; content_sha256: string }> => request(modelId, preparedId, setup, fetcher);
export const readImportedSetup = (modelId: string, preparedId: string, fetcher?: typeof fetch):
  Promise<{ setup: ImportedSetup | null; revision_id?: string | null }> => request(modelId, preparedId, undefined, fetcher);

/** An imported model as the server keeps it (`server/jobs/models_api.py`). */
export interface ImportedModelSummary {
  model_id: string;
  producer: 'mesh-file' | 'cad';
  display_name: string;
  archive_stem?: string | null;
  latest_prepared_id?: string | null;
}

/** A prepared mesh as a model read names it; a file's carries its solver mesh. */
export interface PreparedSummary {
  ingest_id: string;
  model_id: string | null;
  producer: string;
  manifest_sha256: string;
  artifact_sha256: string;
  msh_text?: string;
  [key: string]: unknown;
}

/** Everything a run is reopened with (`GET /api/runs/{id}/workspace`). */
export interface RunWorkspace {
  job_id: string;
  model?: ImportedModelSummary | null;
  prepared?: PreparedSummary | null;
  original_available: boolean;
  setup?: { revision_id: string; content: { geometry: ImportedSetup['geometry']; options: ImportedSetup['options'] };
    derived?: boolean; exact?: boolean; note?: string | null } | null;
  solve_options?: ImportedSetup['options'] | null;
  refusal?: { code: string; message: string } | null;
}

/** A refused read, with the status and code the server gave so a caller can tell a gone model from a failed one. */
export class ImportedModelApiError extends Error {
  constructor(message: string, readonly status: number, readonly code: string | null) {
    super(message);
    this.name = 'ImportedModelApiError';
  }
}

/** The server no longer has this model. */
export const isModelGone = (error: unknown): boolean =>
  error instanceof ImportedModelApiError && (error.status === 404 || error.code === 'model_not_found');

async function read<T>(path: string, fetcher: typeof fetch, what: string): Promise<T> {
  const response = await fetcher(path);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { message?: unknown; code?: unknown }; detail?: unknown } | null;
    const message = typeof body?.error?.message === 'string' ? body.error.message : formatApiDetail(body?.detail);
    throw new ImportedModelApiError(message ?? `${what} unavailable (${response.status}).`, response.status,
      typeof body?.error?.code === 'string' ? body.error.code : null);
  }
  return response.json() as Promise<T>;
}

/** The one read a run is reopened from: its model, prepared mesh, original and setup. */
export const readRunWorkspace = (jobId: string, fetcher: typeof fetch = fetch): Promise<RunWorkspace> =>
  read(`/api/runs/${encodeURIComponent(jobId)}/workspace`, fetcher, 'This run’s workspace');

/** A model, its latest preparation and, when named, one preparation with its mesh. */
export const readImportedModel = (modelId: string, preparedId: string | null, fetcher: typeof fetch = fetch):
  Promise<{ model: ImportedModelSummary; latest_prepared: PreparedSummary | null; prepared?: PreparedSummary; original_available: boolean }> =>
  read(`/api/models/${encodeURIComponent(modelId)}${preparedId ? `?prepared_id=${encodeURIComponent(preparedId)}` : ''}`,
    fetcher, 'This model');
