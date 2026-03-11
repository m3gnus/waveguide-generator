import { GlobalState } from '../state.js';
import { DesignModule } from '../modules/design/index.js';
import { SimulationModule } from '../modules/simulation/index.js';

export function provideMeshForSimulation(app) {
  try {
    const designTask = DesignModule.task(
      DesignModule.importState(GlobalState.get(), {
        applyVerticalOffset: true
      })
    );
    const preparedParams = DesignModule.output.simulationParams(designTask);
    const simulationTask = SimulationModule.task(SimulationModule.importDesign(designTask), {
      includeEnclosure: Number(preparedParams.encDepth || 0) > 0,
      adaptivePhi: false
    });
    const payload = SimulationModule.output.mesh(simulationTask);

    const vertexCount = payload.vertices.length / 3;
    const triangleCount = payload.indices.length / 3;

    const maxIndex = Math.max(...payload.indices);
    if (maxIndex >= vertexCount) {
      throw new Error(
        `Invalid mesh: max index ${maxIndex} >= vertex count ${vertexCount}. This indicates simulation mesh corruption.`
      );
    }

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
