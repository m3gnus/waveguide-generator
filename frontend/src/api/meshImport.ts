import { formatApiDetail, type ImportedSolveSubmission } from '../jobs/actions';

export interface MeshSummary {
  node_count: number;
  triangle_count: number;
  dimensions: number[];
  dimensions_mm?: number[];
  groups: Array<{ tag: number; name: string; triangle_count: number }>;
}
export interface RegisteredMesh {
  ingest_id: string;
  producer: 'mesh-file';
  manifest_sha256: string;
  artifact_sha256: string;
  source_file: { filename: string; transform: { units: string; forward: string; up: string }; source_tags: number[] };
  source_tags: Record<string, number>;
  summary: MeshSummary;
  msh_text: string;
}

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
  request<MeshSummary>('/api/mesh-import/inspect', { filename, msh_text }, fetcher);
export const registerMesh = (body: unknown, fetcher?: typeof fetch) =>
  request<RegisteredMesh>('/api/mesh-import', body, fetcher);
export const readMeshImport = (id: string, fetcher?: typeof fetch) =>
  request<RegisteredMesh>(`/api/mesh-import/${encodeURIComponent(id)}`, undefined, fetcher);

/** Restore the admitted request, not the execution request's transformed inputs. */
export const readMeshJobRequest = (jobId: string, fetcher?: typeof fetch) =>
  request<{ effective_request: ImportedSolveSubmission }>(`/api/jobs/${encodeURIComponent(jobId)}/requests`, undefined, fetcher);
