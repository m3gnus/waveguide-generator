import { AppEvents } from '../../events.js';

let pendingMeshResolve = null;
let pendingMeshReject = null;

export function setupMeshListener(panel) {
  AppEvents.on('simulation:mesh-ready', (meshData) => {
    if (pendingMeshResolve) {
      pendingMeshResolve(meshData);
      pendingMeshResolve = null;
      pendingMeshReject = null;
      return;
    }
    if (panel.pendingMeshResolve) {
      panel.pendingMeshResolve(meshData);
      panel.pendingMeshResolve = null;
      panel.pendingMeshReject = null;
    }
  });

  AppEvents.on('simulation:mesh-error', (errorData) => {
    const message = errorData?.message || 'Simulation mesh generation failed.';
    if (pendingMeshReject) {
      pendingMeshReject(new Error(message));
      pendingMeshResolve = null;
      pendingMeshReject = null;
      return;
    }
    if (panel.pendingMeshReject) {
      panel.pendingMeshReject(new Error(message));
      panel.pendingMeshResolve = null;
      panel.pendingMeshReject = null;
    }
  });
}

export function prepareMeshForSimulation(panel) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pendingMeshResolve = null;
      pendingMeshReject = null;
      panel.pendingMeshResolve = null;
      panel.pendingMeshReject = null;
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
      if (typeof meshData.format !== 'string' || !meshData.format.trim()) {
        reject(new Error('Mesh payload is missing a format value.'));
        return;
      }
      if (!meshData.boundaryConditions || typeof meshData.boundaryConditions !== 'object') {
        reject(new Error('Mesh payload is missing boundary conditions.'));
        return;
      }
      resolve(meshData);
    };
    pendingMeshReject = (error) => {
      clearTimeout(timeout);
      reject(error);
    };
    panel.pendingMeshResolve = pendingMeshResolve;
    panel.pendingMeshReject = pendingMeshReject;

    AppEvents.emit('simulation:mesh-requested');
  });
}

export function prepareLegacyBemMesh(meshData) {
  return Promise.resolve(meshData);
}
