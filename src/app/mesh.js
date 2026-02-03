import { buildHornMesh } from '../geometry/index.js';
import { AppEvents } from '../events.js';

export function provideMeshForSimulation(app) {
  const preparedParams = app.prepareParamsForMesh({ applyVerticalOffset: true });
  const { vertices, indices } = buildHornMesh(preparedParams);
  const vertexCount = vertices.length / 3;

  const maxIndex = Math.max(...indices);
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
    `[Simulation] Mesh validated: ${vertexCount} vertices, ${indices.length / 3} triangles`
  );

  AppEvents.emit('simulation:mesh-ready', {
    vertices: Array.from(vertices),
    indices: Array.from(indices),
    vertexCount: vertexCount,
    triangleCount: indices.length / 3,
    params: preparedParams,
    type: preparedParams.type
  });
}
