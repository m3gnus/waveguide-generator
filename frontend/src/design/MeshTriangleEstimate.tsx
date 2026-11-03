import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { failureWords, httpErrorMessage, ProgramFault } from '../api/httpError';
import { solverMeshDesignKey } from '../api/solverMesh';
import type { DesignDocument } from '../stores/design';

const ESTIMATE_FAILED = 'WG could not count this mesh. Open the Mesh view to build it and see its size.';

const roughRatio = (ratio: number) => ratio < 2 ? ratio.toFixed(1) : Math.round(ratio).toString();

function withoutTarget(key: string): string {
  const wire = JSON.parse(key) as { mesh: Record<string, unknown> };
  const mesh = { ...wire.mesh };
  delete mesh.surface_deviation_mm;
  return JSON.stringify({ ...wire, mesh });
}

/** One estimate of a serialised design, shared by every reader of that key. */
function useEstimate(key: string, enabled: boolean) {
  return useQuery({
    queryKey: ['mesh-estimate', key],
    queryFn: async ({ signal }) => {
      const response = await fetch('/api/mesh-estimate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design: JSON.parse(key) }), signal,
      });
      if (!response.ok) throw new Error(await httpErrorMessage(response, ESTIMATE_FAILED));
      const result = await response.json() as { triangle_count?: number };
      if (typeof result.triangle_count !== 'number' || !Number.isFinite(result.triangle_count)) throw new ProgramFault('Mesh estimate response is invalid');
      return result.triangle_count;
    },
    staleTime: 60_000, gcTime: 60_000, retry: false,
    enabled,
  });
}

/** The cost of the mesh settings, counted on the mesh itself once edits settle.
 * The server keeps that mesh, so a following Build or solve reuses it. A
 * deviation target is compared with the same design without one. */
export function MeshTriangleEstimate({ design }: { design: DesignDocument }) {
  const key = solverMeshDesignKey(design);
  const [settled, setSettled] = useState(key);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(key), 500);
    return () => clearTimeout(timer);
  }, [key]);
  const current = settled === key;
  const targeted = design.mesh.surface_deviation_mm != null;
  const estimate = useEstimate(settled, current);
  const baseline = useEstimate(withoutTarget(settled), current && targeted);
  const count = estimate.data;
  const off = baseline.data;
  const failure = estimate.isError ? failureWords(estimate.error, ESTIMATE_FAILED, 'mesh estimate') : null;
  return <div className="resolved-mode mesh-estimate" role="status" aria-label="Triangle estimate">
    <span>Triangles, full model</span>
    <b>{!current || estimate.isPending ? 'calculating…'
      : failure || count === undefined ? 'not available' : count.toLocaleString()}</b>
    {failure ? <small>{failure}</small>
      : targeted ? <small>
        {current && count !== undefined && off !== undefined && off > 0 && <>With this Off, {off.toLocaleString()}: {roughRatio(count / off)}× the triangles. </>}
        Measured designs took about 2.8× the memory.
      </small>
        : <small>Setting a target took 7 to 8× the triangles and about 2.8× the memory on measured designs.</small>}
  </div>;
}
