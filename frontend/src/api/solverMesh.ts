import { formatApiDetail } from '../jobs/actions';
import { createImportedMeshScene, type ImportedMeshScene } from '../viewport/importedMesh';
import { parseMSH } from '../viewport/mshParser';

/** Trimmed build statistics the solver-mesh endpoint reports alongside the
 * artifact. `mesh_cache_key` identifies the artifact independently of the
 * design revision that requested it: an edit that cannot change the mesh
 * (a frequency sweep, a polar setting) comes back with the same key. */
export interface SolverMeshStats {
  triangle_count: number;
  vertex_count: number;
  warnings: string[];
  mesh_cache_key: string;
  mesh_cache_hit: boolean;
}

export interface SolverMeshResult {
  msh_text: string;
  stats: SolverMeshStats;
  cut_planes: string[];
  quadrants: number;
}

async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    const formatted = formatApiDetail(body.detail);
    if (formatted) return formatted;
  } catch { /* fall through */ }
  return `${response.status} ${response.statusText}`.trim();
}

/**
 * Build (or fetch from the server's shared artifact cache) the authoritative
 * solver mesh for an already-serialised solve design.
 *
 * `body` is the JSON request `{design, symmetry}`; callers serialise it
 * themselves for the same reason `postSymmetry` does — re-serialising the
 * live design here would race an edit that landed after keying.
 *
 * `signal` genuinely cancels: the endpoint watches for the disconnect and the
 * build abandons at its next cooperative checkpoint.
 */
export async function postSolverMesh(
  body: string,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<SolverMeshResult> {
  const response = await fetcher('/api/solver-mesh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    signal,
  });
  if (!response.ok) throw new Error(await detail(response));
  const result = await response.json() as Partial<SolverMeshResult> & { stats?: Partial<SolverMeshStats> };
  if (
    typeof result.msh_text !== 'string'
    || typeof result.stats?.mesh_cache_key !== 'string'
    || typeof result.stats.triangle_count !== 'number'
    || !Array.isArray(result.cut_planes)
  ) {
    throw new Error('Solver mesh response is invalid');
  }
  return result as SolverMeshResult;
}

/** The store token a solver-mesh artifact would carry, derived from the cache
 * key so an unchanged artifact can be recognised without re-parsing it. */
export function solverMeshArtifactToken(meshCacheKey: string): string {
  return `solver:${meshCacheKey}`;
}

/** Parse the artifact and reconstruct the full physical model for display.
 * The reduced solve domain keeps its `solvedDomain` mark; mirrored copies are
 * display-only, exactly as CAD-return artifacts are treated. */
export function solverMeshScene(name: string, result: SolverMeshResult): ImportedMeshScene {
  return createImportedMeshScene(
    name,
    parseMSH(result.msh_text),
    'solver',
    null,
    result.cut_planes,
    {
      solvedTriangleCount: result.stats.triangle_count,
      artifactToken: solverMeshArtifactToken(result.stats.mesh_cache_key),
    },
  );
}
