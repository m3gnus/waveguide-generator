import { activityOperation } from '../shell/activityOperation';
import type { ImportedSolveSubmission } from '../jobs/actions';
import { refusalText } from './httpError';
import type { RegisteredMesh } from './meshImport';
import type { CadReturnIngestRecord } from './cadlink';

export const MAX_MESH_REPLAY_BYTES = 64 * 1024 * 1024;
export interface OpenedMeshReplay {
  record: RegisteredMesh | (CadReturnIngestRecord & { producer: 'cad'; msh_text: string });
  request: ImportedSolveSubmission;
}

export class MeshReplayRefusal extends Error {
  constructor(message: string, readonly status: number) { super(message); this.name = 'MeshReplayRefusal'; }
}

async function checked(response: Response): Promise<Response> {
  if (!response.ok) {
    const error = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new MeshReplayRefusal(refusalText(error?.detail, 'mesh replay', 'WG could not reopen this mesh. Solve the design again.'), response.status);
  }
  return response;
}

/** The server owns the versioned package and validates it before registering. */
export async function openMeshReplay(text: string, fetcher: typeof fetch = fetch): Promise<OpenedMeshReplay> {
  return activityOperation({ kind: 'mesh-import', label: 'Importing the mesh replay' }, fetcher, async (fetcher) => {
    if (new TextEncoder().encode(text).byteLength > MAX_MESH_REPLAY_BYTES) throw new Error('This file is larger than the 64 MiB WG can open. Choose a smaller file.');
    const response = await checked(await fetcher('/api/mesh-replay', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: text,
    }));
    return response.json() as Promise<OpenedMeshReplay>;
  });
}

export async function fetchMeshReplay(jobId: string, fetcher: typeof fetch = fetch): Promise<Blob> {
  return activityOperation({ kind: 'export', label: 'Exporting mesh replay' }, fetcher, async (fetcher) => {
    const response = await checked(await fetcher(`/api/jobs/${encodeURIComponent(jobId)}/mesh-replay`));
    return response.blob();
  });
}
