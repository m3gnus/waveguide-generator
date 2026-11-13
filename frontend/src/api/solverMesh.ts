import { stagedActivity } from '../shell/stagedActivity';
import { httpErrorMessage, isAbortError, ProgramFault } from './httpError';
import { serializeSolveDesign, type DesignDocument } from '../stores/design';
import type { SolveOptionsStore } from '../stores/solveOptions';
import type { components } from './generated/openapi';

/** Trimmed build statistics the solver-mesh endpoint reports alongside the
 * artifact. `mesh_cache_key` identifies the artifact independently of the
 * design revision that requested it: an edit that cannot change the mesh
 * (a frequency sweep, a polar setting) comes back with the same key. */
export type SolverMeshStats = components['schemas']['SolverMeshStats'];
export type SolverMeshResult = components['schemas']['SolverMeshResponse'];
export type SolverMeshSummary = components['schemas']['SolverMeshSummary'];

/** The measured solve mesh needs the person's choice before continuing. */
export class SolverMeshTooLarge extends Error {
  constructor(message: string, readonly triangles: number, readonly limit: number, readonly meshCacheKey: string) {
    super(message);
    this.name = 'SolverMeshTooLarge';
  }
}

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
  const wire = serializeSolveDesign(design);
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

// The server counts resolve, mesh (built and validated), check (per-request
// checks done) and publish; each label names the step then underway.
const solverMeshSteps = {
  start: 'Resolving solve settings', resolve: 'Building and checking the mesh',
  mesh: 'Checking the mesh', check: 'Receiving the mesh', publish: 'Receiving the mesh',
};

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
export function postSolverMesh(body: string, fetcher?: typeof fetch, signal?: AbortSignal): Promise<SolverMeshResult>;
export function postSolverMesh(body: string, fetcher: typeof fetch, signal: AbortSignal | undefined, representation: 'summary'): Promise<SolverMeshSummary>;
export async function postSolverMesh(
  body: string,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
  representation: 'full' | 'summary' = 'full',
): Promise<SolverMeshResult | SolverMeshSummary> {
  return stagedActivity({ kind: 'solver-mesh', label: 'Building the solver mesh' }, fetcher, signal, async (fetcher, signal) => {
    const response = await fetcher(representation === 'summary' ? '/api/solver-mesh?representation=summary' : '/api/solver-mesh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal,
    });
    if (!response.ok) {
      if (response.status === 409) {
        const payload = await response.clone().json().catch((error: unknown) => {
          if (isAbortError(error)) throw error;
          return null;
        }) as { detail?: unknown } | null;
        const value = payload?.detail;
        if (value && typeof value === 'object' && 'code' in value && value.code === 'mesh_too_large'
          && 'triangles' in value && typeof value.triangles === 'number'
          && 'limit' in value && typeof value.limit === 'number'
          && 'mesh_cache_key' in value && typeof value.mesh_cache_key === 'string') {
          throw new SolverMeshTooLarge(`This mesh has ${value.triangles.toLocaleString()} triangles, above the limit of ${value.limit.toLocaleString()} for the part WG solves. Raise Max surface deviation or a mesh resolution in Simulation › Mesh, or press Solve to choose.`, value.triangles, value.limit, value.mesh_cache_key);
        }
      }
      throw new Error(await httpErrorMessage(response, 'WG could not build the solver mesh. Press Solve to try again.'));
    }
    const result = await response.json() as Partial<SolverMeshResult> & { stats?: Partial<SolverMeshStats> };
    if (
      (representation === 'full' && typeof result.msh_text !== 'string')
      || typeof result.stats?.mesh_cache_key !== 'string'
      || typeof result.stats.triangle_count !== 'number'
      || !Array.isArray(result.cut_planes)
    ) {
      throw new ProgramFault('Solver mesh response is invalid');
    }
    return result as SolverMeshResult | SolverMeshSummary;
  }, { steps: solverMeshSteps });
}

/** The store token a solver-mesh artifact would carry, derived from the cache
 * key so an unchanged artifact can be recognised without re-parsing it. */
export function solverMeshArtifactToken(meshCacheKey: string): string {
  return `solver:${meshCacheKey}`;
}
