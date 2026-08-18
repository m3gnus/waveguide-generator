import { parseMSH } from './mshParser';
import { classifyFieldPlaneMask, createFieldPlaneMaskMesh, type FieldPlaneMaskMesh } from './fieldPlaneMaskLogic';
import type { FieldPlaneMaskRequest, FieldPlaneMaskResponse } from './fieldPlaneMaskProtocol';

interface WorkerScope {
  onmessage: ((event: MessageEvent<FieldPlaneMaskRequest>) => void) | null;
  postMessage: (message: FieldPlaneMaskResponse, transfer?: Transferable[]) => void;
}

const scope = globalThis as unknown as WorkerScope;
const meshes = new Map<string, Promise<FieldPlaneMaskMesh>>();

function meshKey(jobId: string, geometrySha256: string): string {
  return `${jobId}|${geometrySha256}`;
}

function loadMesh(jobId: string, geometrySha256: string): Promise<FieldPlaneMaskMesh> {
  const key = meshKey(jobId, geometrySha256);
  const existing = meshes.get(key);
  if (existing) return existing;
  const pending = fetch(`/api/mesh-artifact/${encodeURIComponent(jobId)}`, {
    headers: { Accept: 'text/plain' },
  }).then(async (response) => {
    if (!response.ok) throw new Error(`mesh artifact request failed (${response.status})`);
    const parsed = parseMSH(await response.text());
    return createFieldPlaneMaskMesh(parsed.vertices, parsed.indices);
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
  void loadMesh(request.jobId, request.geometrySha256)
    .then((mesh) => {
      const mask = classifyFieldPlaneMask(mesh, request.plane);
      const result: FieldPlaneMaskResponse = {
        type: 'result',
        generation: request.generation,
        jobId: request.jobId,
        geometrySha256: request.geometrySha256,
        nx: request.plane.nx,
        ny: request.plane.ny,
        watertight: mesh.watertight,
        mask: mask.buffer as ArrayBuffer,
      };
      scope.postMessage(result, [result.mask]);
    })
    .catch((reason: unknown) => {
      scope.postMessage({
        type: 'error',
        generation: request.generation,
        jobId: request.jobId,
        geometrySha256: request.geometrySha256,
        message: reason instanceof Error ? reason.message : 'field-plane mask failed',
      });
    });
};

export {};
