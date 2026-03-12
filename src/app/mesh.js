import { prepareCanonicalSimulationMesh } from '../modules/simulation/domain.js';
import { readSimulationState } from '../modules/simulation/useCases.js';

export function provideMeshForSimulation(app) {
  try {
    const payload = prepareCanonicalSimulationMesh(readSimulationState());

    const vertexCount = payload.vertices.length / 3;
    const triangleCount = payload.indices.length / 3;

    console.log(
      `[Simulation] Mesh validated: ${vertexCount} vertices, ${triangleCount} triangles`
    );

    return app.publishSimulationMesh(payload);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[Simulation] Mesh generation failed:', message);
    return app.publishSimulationMeshError(message);
  }
}
