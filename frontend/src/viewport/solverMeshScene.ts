import { solverMeshArtifactToken, type SolverMeshResult } from '../api/solverMesh';
import { createImportedMeshScene, type ImportedMeshScene } from './importedMesh';
import { parseMSH } from './mshParser';

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
