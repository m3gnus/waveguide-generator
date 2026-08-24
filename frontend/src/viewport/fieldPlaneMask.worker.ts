import { parseMSH } from './mshParser';
import { classifyFieldPlaneMask, createFieldPlaneMaskMesh, type FieldPlaneMaskMesh } from './fieldPlaneMaskLogic';
import type { FieldPlaneMaskRequest, FieldPlaneMaskResponse } from './fieldPlaneMaskProtocol';

interface WorkerScope {
  onmessage: ((event: MessageEvent<FieldPlaneMaskRequest>) => void) | null;
  postMessage: (message: FieldPlaneMaskResponse, transfer?: Transferable[]) => void;
}

const scope = globalThis as unknown as WorkerScope;
const meshes = new Map<string, Promise<FieldPlaneMaskMesh>>();

function meshKey(jobId: string, geometrySha256: string, symmetryPlane: string | null): string {
  return `${jobId}|${geometrySha256}|${symmetryPlane ?? 'none'}`;
}

function loadMesh(
  jobId: string,
  geometrySha256: string,
  symmetryPlane: string | null,
): Promise<FieldPlaneMaskMesh> {
  const key = meshKey(jobId, geometrySha256, symmetryPlane);
  const existing = meshes.get(key);
  if (existing) return existing;
  const pending = fetch(`/api/mesh-artifact/${encodeURIComponent(jobId)}`, {
    headers: { Accept: 'text/plain' },
  }).then(async (response) => {
    if (!response.ok) throw new Error(`mesh artifact request failed (${response.status})`);
    const parsed = parseMSH(await response.text());
    const mesh = createFieldPlaneMaskMesh(parsed.vertices, parsed.indices, symmetryPlane);
    console.info(`field-plane mask: snapped ${mesh.snappedVertexCount} vertices onto the symmetry plane (tol ${mesh.snapToleranceM})`);
    return mesh;
  }).catch((reason: unknown) => {
    meshes.delete(key);
    throw reason;
  });
  meshes.set(key, pending);
  return pending;
}

scope.onmessage = (event) => {
  const request = event.data;
  if (request.type !== 'classify') return;
  void loadMesh(request.jobId, request.geometrySha256, request.symmetryPlane)
    .then((mesh) => {
      const mask = classifyFieldPlaneMask(mesh, request.plane);
      const result: FieldPlaneMaskResponse = {
        type: 'result',
        generation: request.generation,
        jobId: request.jobId,
        geometrySha256: request.geometrySha256,
        symmetryPlane: request.symmetryPlane,
        nx: mask.nx,
        ny: mask.ny,
        watertight: mesh.watertight,
        snappedVertexCount: mesh.snappedVertexCount,
        mask: mask.data.buffer as ArrayBuffer,
      };
      scope.postMessage(result, [result.mask]);
    })
    .catch((reason: unknown) => {
      scope.postMessage({
        type: 'error',
        generation: request.generation,
        jobId: request.jobId,
        geometrySha256: request.geometrySha256,
        symmetryPlane: request.symmetryPlane,
        message: reason instanceof Error ? reason.message : 'field-plane mask failed',
      });
    });
};

export {};
