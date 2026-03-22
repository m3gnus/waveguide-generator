export function setupMeshListener(panel) {
  return panel.uiCoordinator.bind();
}

export function prepareMeshForSimulation(panel) {
  return panel.uiCoordinator.prepareMesh();
}
