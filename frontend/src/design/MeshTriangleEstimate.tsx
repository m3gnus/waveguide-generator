import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { solverMeshDesignKey } from '../api/solverMesh';
import type { DesignDocument } from '../stores/design';

/** Observe the build's own density pass, after edits settle. */
export function MeshTriangleEstimate({ design }: { design: DesignDocument }) {
  const key = solverMeshDesignKey(design);
  const [settled, setSettled] = useState(key);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(key), 500);
    return () => clearTimeout(timer);
  }, [key]);
  const query = useQuery({
    queryKey: ['mesh-estimate', settled],
    queryFn: async ({ signal }) => {
      const response = await fetch('/api/mesh-estimate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design: JSON.parse(settled) }), signal,
      });
      if (!response.ok) throw new Error('Estimate unavailable');
      const result = await response.json() as { triangle_count?: number };
      if (typeof result.triangle_count !== 'number' || !Number.isFinite(result.triangle_count)) throw new Error('Estimate unavailable');
      return result.triangle_count;
    },
    staleTime: 60_000, gcTime: 60_000, retry: false,
    enabled: settled === key,
  });
  return <p className="section-note" role="status" aria-label="Triangle estimate">
    Estimated triangles (full model): {settled !== key || query.isPending ? 'calculating…'
      : query.isError ? 'unavailable' : query.data?.toLocaleString() ?? '—'}
  </p>;
}
