import type { CompiledAssembly } from '../api/assembly';
import { solverMeshArtifactToken, type SolverMeshResult } from '../api/solverMesh';
import { createImportedMeshScene, type ImportedMeshScene } from './importedMesh';
import { parseMSH } from './mshParser';

/** Parse the artifact and reconstruct the full physical model for display.
 * The reduced solve domain keeps its `solvedDomain` mark; mirrored copies are
 * display-only, exactly as CAD-return artifacts are treated. */
export function solverMeshScene(name: string, result: SolverMeshResult): ImportedMeshScene {
  const scene = createImportedMeshScene(
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
  scene.tessellation = {
    surface_deviation_mm: result.stats.surface_deviation_mm ?? null,
    surface_deviation: result.stats.surface_deviation,
    element_shape: result.stats.element_shape,
  };
  return scene;
}

/** A compiled design assembly's solve mesh, the full domain with its sources
 * coloured by role. A reopened assembly run shows it as its retained `file`
 * model; the parametric design it was built from shows it as its `solver`
 * artifact. */
export function assemblyMeshScene(name: string, record: CompiledAssembly, source: 'file' | 'solver'): ImportedMeshScene {
  return createImportedMeshScene(name, parseMSH(record.msh_text), source, record.ingest_id, [], {
    rolesByTag: Object.fromEntries(record.sources.map(item => [record.source_tags[item.id], item.role])),
  });
}
