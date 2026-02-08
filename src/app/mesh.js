import { AppEvents } from '../events.js';
import { buildGeometryArtifacts } from '../geometry/index.js';

export function provideMeshForSimulation(app) {
  const preparedParams = app.prepareParamsForMesh({ applyVerticalOffset: true });
  const artifacts = buildGeometryArtifacts(preparedParams, {
    includeEnclosure: Number(preparedParams.encDepth || 0) > 0
  });
  const payload = artifacts.simulation;

  const vertexCount = payload.vertices.length / 3;
  const triangleCount = payload.indices.length / 3;

  const maxIndex = Math.max(...payload.indices);
  if (maxIndex >= vertexCount) {
    console.error(
      `[Simulation] Invalid mesh: max index ${maxIndex} >= vertex count ${vertexCount}`
    );
    console.error(
      '[Simulation] This indicates the mesh was corrupted during simulation mesh generation'
    );
    AppEvents.emit('simulation:mesh-ready', null);
    return null;
  }

  console.log(
    `[Simulation] Mesh validated: ${vertexCount} vertices, ${triangleCount} triangles`
  );

  AppEvents.emit('simulation:mesh-ready', payload);
}
