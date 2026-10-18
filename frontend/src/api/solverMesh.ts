import { formatApiDetail, toSolveDesign } from '../jobs/actions';
import type { DesignDocument } from '../stores/design';
import type { SolveOptionsStore } from '../stores/solveOptions';
import { createImportedMeshScene, type ImportedMeshScene } from '../viewport/importedMesh';
import { parseMSH } from '../viewport/mshParser';
import type { components } from './generated/openapi';

/** Trimmed build statistics the solver-mesh endpoint reports alongside the
 * artifact. `mesh_cache_key` identifies the artifact independently of the
 * design revision that requested it: an edit that cannot change the mesh
 * (a frequency sweep, a polar setting) comes back with the same key. */
export type SolverMeshStats = components['schemas']['SolverMeshStats'];
export type SolverMeshResult = components['schemas']['SolverMeshResponse'];

/** Preview request inputs, including validation applied to the cached artifact. */
export function solverMeshOptions(state: SolveOptionsStore) {
  return {
    engine: state.engine,
    accuracy: state.accuracy,
    solver_mode: state.solverMode,
    symmetry: state.symmetry,
    mesh_validation_mode: state.meshValidationMode,
    ...(state.groundPlane.enabled ? { ground_plane: state.groundPlane } : {}),
  };
}

/** Design sampling is consumed after meshing, just like option sampling. */
export function solverMeshDesignKey(design: DesignDocument): string {
  const wire = toSolveDesign(design);
  const simulation = { ...(wire.simulation as Record<string, unknown>) };
  delete simulation.f1;
  delete simulation.f2;
  delete simulation.num_frequencies;
  return JSON.stringify({
    ...wire,
    simulation,
    extra_blocks: Object.fromEntries(Object.entries(design.extra_blocks)
      .filter(([name]) => !name.startsWith('ABEC.Polars:'))),
  });
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
 * `body` is the JSON request `{design, options}`; callers serialise it
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
