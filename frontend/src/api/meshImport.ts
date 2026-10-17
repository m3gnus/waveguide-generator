import { formatApiDetail } from '../jobs/actions';

export interface MeshSummary {
  node_count: number;
  triangle_count: number;
  dimensions: number[];
  dimensions_mm?: number[];
  groups: Array<{ tag: number; name: string; triangle_count: number }>;
}
/** One prepared mesh of an imported file model, with its solver mesh. */
export interface RegisteredMesh {
  ingest_id: string;
  /** The file's model: `mesh-file:` and its original's digest. */
  model_id: string;
  producer: 'mesh-file';
  manifest_sha256: string;
  artifact_sha256: string;
  source_file: { filename: string; transform: { units: string; forward: string; up: string }; source_tags: number[] };
  source_tags: Record<string, number>;
  summary: MeshSummary;
  msh_text: string;
}
export interface FilePreparation { units: string; forward: string; up: string; source_tags: number[] }

const modelPath = (modelId: string) => `/api/models/${encodeURIComponent(modelId)}`;

async function request<T>(url: string, body?: unknown, fetcher: typeof fetch = fetch): Promise<T> {
  const response = await fetcher(url, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(formatApiDetail(error?.detail) ?? `Mesh import failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export const inspectMesh = (filename: string, msh_text: string, fetcher?: typeof fetch) =>
  request<MeshSummary>('/api/models/files/inspect', { filename, msh_text }, fetcher);
/** Import a file as its model and prepare it; the same file and preparation is the same mesh. */
export const registerMesh = async (body: FilePreparation & { filename: string; msh_text: string }, fetcher?: typeof fetch) =>
  (await request<{ prepared: RegisteredMesh }>('/api/models/files', body, fetcher)).prepared;
/** Prepare a file model again from the original the server retained. */
export const prepareMeshModel = async (modelId: string, body: FilePreparation, fetcher?: typeof fetch) =>
  (await request<{ prepared: RegisteredMesh }>(`${modelPath(modelId)}/prepare`, body, fetcher)).prepared;
/** The model a prepared mesh is filed under, or null when the server has none for it. */
export async function findPreparedModel(preparedId: string, fetcher: typeof fetch = fetch):
  Promise<{ model_id: string; prepared_id: string } | null> {
  const response = await fetcher(`/api/models?prepared_id=${encodeURIComponent(preparedId)}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Saved mesh setup unavailable (${response.status}).`);
  return response.json() as Promise<{ model_id: string; prepared_id: string }>;
}
