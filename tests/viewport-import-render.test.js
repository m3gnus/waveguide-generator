import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';

import { ImportedMeshState } from '../src/state.js';
import { applyDisplayMode, renderModel } from '../src/app/scene.js';

function makeApp(mode = 'curvature') {
  return {
    scene: new THREE.Scene(),
    renderer: {},
    uiCoordinator: {
      readDisplayModeSetting() {
        return mode;
      },
    },
    stats: { innerText: '' },
    setViewportMeshStats(stats) {
      this.viewportMeshStats = stats;
    },
  };
}

function setImportedMesh({ physicalTags = new Uint32Array([1]) } = {}) {
  ImportedMeshState.active = true;
  ImportedMeshState.filename = 'test.msh';
  ImportedMeshState.vertices = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  ImportedMeshState.indices = new Uint32Array([0, 1, 2]);
  ImportedMeshState.physicalTags = physicalTags;
  ImportedMeshState.physicalNames = new Map([[1, 'SD1G0']]);
}

function clearImportedMesh() {
  ImportedMeshState.active = false;
  ImportedMeshState.filename = null;
  ImportedMeshState.vertices = null;
  ImportedMeshState.indices = null;
  ImportedMeshState.physicalTags = null;
  ImportedMeshState.physicalNames = null;
}

test('imported mesh render reuses Three.js mesh when inputs and display mode are unchanged', () => {
  setImportedMesh();
  const app = makeApp('curvature');

  try {
    renderModel(app);
    const firstMesh = app.hornMesh;
    const firstGeometry = firstMesh.geometry;
    const firstMaterial = firstMesh.material;

    renderModel(app);

    assert.equal(app.hornMesh, firstMesh);
    assert.equal(app.hornMesh.geometry, firstGeometry);
    assert.equal(app.hornMesh.material, firstMaterial);
    assert.equal(app.viewportMeshStats.vertexCount, 3);
    assert.equal(app.viewportMeshStats.triangleCount, 1);
  } finally {
    clearImportedMesh();
  }
});

test('imported physical tag colors are built directly and preserved across display mode changes', () => {
  setImportedMesh();
  const app = makeApp('curvature');

  try {
    renderModel(app);

    assert.ok(app.hornMesh.geometry.attributes.color);
    assert.equal(app.hornMesh.material.vertexColors, true);
    assert.equal(app.hornMesh.material.type, 'MeshPhongMaterial');

    applyDisplayMode(app, 'wireframe');

    assert.ok(app.hornMesh.geometry.attributes.color);
    assert.equal(app.hornMesh.material.vertexColors, true);
    assert.equal(app.hornMesh.material.type, 'MeshPhongMaterial');
  } finally {
    clearImportedMesh();
  }
});

test('imported mesh index buffer uses Uint32Array when vertex indices exceed Uint16 range', () => {
  const vertexCount = 65537;
  ImportedMeshState.active = true;
  ImportedMeshState.filename = 'large-index.msh';
  ImportedMeshState.vertices = new Float32Array(vertexCount * 3);
  ImportedMeshState.vertices[65536 * 3] = 1;
  ImportedMeshState.indices = new Uint32Array([0, 65536, 1]);
  ImportedMeshState.physicalTags = null;
  ImportedMeshState.physicalNames = null;
  const app = makeApp('clay');

  try {
    renderModel(app);

    assert.ok(app.hornMesh.geometry.index.array instanceof Uint32Array);
  } finally {
    clearImportedMesh();
  }
});
