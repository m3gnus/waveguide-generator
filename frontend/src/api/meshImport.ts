import type { ImportedSetup } from '../jobs/importedSetup';
import { requestImportedModel } from './importedModels';

export interface MeshSummary {
  known_model?: { model_id: string; latest_prepared_id: string };
  previous_roles?: Record<string, string>;
  previous_setup?: { filename: string; updated_at: string; preparation: FilePreparation; setup: ImportedSetup | null };
  node_count: number;
  triangle_count: number;
  dimensions: number[];
  dimensions_mm?: number[];
  suggested_units?: string | null;
  detected_planes?: string[];
  lids?: Record<string, { triangle_count: number; candidate: boolean; groups: Array<{ tag: number; triangle_count: number }> }>;
  kept_sides?: Record<string, 'positive' | 'negative' | 'both' | 'on_plane'>;
  groups: Array<{ tag: number; solve_tag?: number; name: string; triangle_count: number;
    suggested_role?: string | null; needs_role_choice?: boolean; role_from?: 'file' | 'name' | 'default'; display_name?: string }>;

}
/** One prepared mesh of an imported file model, with its solver mesh. */
export interface RegisteredMesh {
  ingest_id: string;
  /** The file's model: `mesh-file:` and its original's digest. */
  model_id: string;
  producer: 'mesh-file';
  manifest_sha256: string;
  artifact_sha256: string;
  source_file: { filename: string; transform: { units: string; forward: string; up: string; removed_lids?: Record<string, { triangle_count: number; groups: Array<{ tag: number; triangle_count: number }> }> }; source_tags: number[]; roles?: Record<string, string>; domain_planes?: string[]; reflect_planes?: string[] | null; remove_lid_planes?: string[] };
  source_tags: Record<string, number>;
  summary: MeshSummary;
  sources?: Array<{ id: string; role: string; name?: string }>;
  symmetry?: { domain_planes: string[]; cut_planes: string[]; reflected_planes?: string[] };
  mesh?: { integrity?: Record<string, unknown> };
  msh_text: string;
}
export interface FilePreparation {
  units: string; forward: string; up: string;
  source_tags?: number[];
  roles?: Record<string, string>;
  domain_planes?: string[];
  remove_lid_planes?: string[];
  reflect_planes?: string[] | null;
}

const modelPath = (modelId: string) => `/api/models/${encodeURIComponent(modelId)}`;

function request<T>(url: string, body?: unknown, fetcher: typeof fetch = fetch): Promise<T> {
  return requestImportedModel(url, fetcher, 'Mesh import', body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
}

export const inspectMesh = (filename: string, msh_text: string, fetcher?: typeof fetch, frame?: { units?: string; forward: string; up: string }) =>
  request<MeshSummary>('/api/models/files/inspect', { filename, msh_text, ...frame }, fetcher);
/** Inspect the retained original through the same endpoint and checks. */
export const inspectMeshModel = (model_id: string, frame: { units: string; forward: string; up: string }, fetcher?: typeof fetch) =>
  request<MeshSummary>('/api/models/files/inspect', { model_id, ...frame }, fetcher);
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
