import { AppEvents } from '../../events.js';

let pendingMeshResolve = null;

export function setupMeshListener(panel) {
  AppEvents.on('simulation:mesh-ready', (meshData) => {
    if (pendingMeshResolve) {
      pendingMeshResolve(meshData);
      pendingMeshResolve = null;
      return;
    }
    if (panel.pendingMeshResolve) {
      panel.pendingMeshResolve(meshData);
      panel.pendingMeshResolve = null;
    }
  });
}

export function prepareMeshForSimulation(panel) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pendingMeshResolve = null;
      panel.pendingMeshResolve = null;
      reject(new Error('Timeout waiting for mesh data'));
    }, 10000);

    pendingMeshResolve = (meshData) => {
      clearTimeout(timeout);
      if (!meshData || !Array.isArray(meshData.vertices) || meshData.vertices.length === 0) {
        reject(new Error('No horn geometry available. Please generate a horn first.'));
        return;
      }
      if (!Array.isArray(meshData.surfaceTags) || meshData.surfaceTags.length !== meshData.indices.length / 3) {
        reject(new Error('Mesh payload is missing valid surface tags.'));
        return;
      }
      resolve(meshData);
    };

    AppEvents.emit('simulation:mesh-requested');
  });
}

export function prepareLegacyBemMesh(meshData) {
  return Promise.resolve(meshData);
}
