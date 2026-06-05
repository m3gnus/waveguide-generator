import { prepareHornlabSolveContractMesh } from '../modules/simulation/domain.js';
import { debugLog } from '../logging/debug.js';

export function provideMeshForSimulation(app) {
  try {
    const payload = prepareHornlabSolveContractMesh();

    debugLog('[Simulation] Using HornLab mesher solve contract payload');

    return app.publishSimulationMesh(payload);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error('[Simulation] Mesh contract preparation failed:', message);
    return app.publishSimulationMeshError(message);
  }
}
