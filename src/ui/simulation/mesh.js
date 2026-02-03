import { AppEvents } from '../../events.js';
import { generateBemMesh } from '../../solver/bemMeshGenerator.js';

export function setupMeshListener(panel) {
  // Listen for mesh data from main app
  AppEvents.on('simulation:mesh-ready', (meshData) => {
    if (panel.pendingMeshResolve) {
      panel.pendingMeshResolve(meshData);
      panel.pendingMeshResolve = null;
    }
  });
}

export function prepareMeshForSimulation(panel) {
  // Request mesh from main app and wait for response
  return new Promise((resolve, reject) => {
    // Set up timeout
    const timeout = setTimeout(() => {
      panel.pendingMeshResolve = null;
      reject(new Error('Timeout waiting for mesh data'));
    }, 5000);

    // Store resolve function to be called when mesh arrives
    panel.pendingMeshResolve = (meshData) => {
      clearTimeout(timeout);

      if (!meshData || !meshData.vertices || meshData.vertices.length === 0) {
        reject(new Error('No horn geometry available. Please generate a horn first.'));
        return;
      }

      // Generate BEM-ready mesh with throat surface and boundary tags
      try {
        const bemMesh = generateBemMesh(meshData);
        resolve(bemMesh);
      } catch (error) {
        console.error('[Simulation] BEM mesh generation failed:', error);
        reject(new Error(`Mesh preparation failed: ${error.message}`));
      }
    };

    // Request mesh from main app
    AppEvents.emit('simulation:mesh-requested');
  });
}
