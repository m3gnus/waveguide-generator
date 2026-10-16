import { formatApiDetail, type ImportedSolveSubmission } from '../jobs/actions';
import type { RegisteredMesh } from './meshImport';

export const MAX_MESH_REPLAY_BYTES = 64 * 1024 * 1024;
export interface OpenedMeshReplay {
  record: RegisteredMesh;
  request: ImportedSolveSubmission;
}

export class MeshReplayRefusal extends Error {
  constructor(message: string, readonly status: number) { super(message); this.name = 'MeshReplayRefusal'; }
}

async function checked(response: Response): Promise<Response> {
  if (!response.ok) {
    const error = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new MeshReplayRefusal(formatApiDetail(error?.detail) ?? `Mesh replay failed (${response.status}).`, response.status);
  }
  return response;
}

/** The server owns the versioned package and validates it before registering. */
export async function openMeshReplay(text: string, fetcher: typeof fetch = fetch): Promise<OpenedMeshReplay> {
  if (new TextEncoder().encode(text).byteLength > MAX_MESH_REPLAY_BYTES) throw new Error('Mesh replay exceeds the 64 MiB import limit.');
  const response = await checked(await fetcher('/api/mesh-replay', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: text,
  }));
  return response.json() as Promise<OpenedMeshReplay>;
}

export async function fetchMeshReplay(jobId: string, fetcher: typeof fetch = fetch): Promise<Blob> {
  const response = await checked(await fetcher(`/api/jobs/${encodeURIComponent(jobId)}/mesh-replay`));
  return response.blob();
}
