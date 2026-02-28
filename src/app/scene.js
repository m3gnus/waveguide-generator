import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import {
  createScene,
  createPerspectiveCamera,
  createOrthoCamera,
  ZebraShader
} from '../viewer/index.js';
import { buildGeometryArtifacts } from '../geometry/index.js';
import { getDisplayMode } from '../ui/settings/modal.js';
import {
  loadViewerSettings,
  applyViewerSettingsToControls,
  setInvertWheelZoom,
  getCurrentViewerSettings,
} from '../ui/settings/viewerSettings.js';

export function setupScene(app) {
  app.scene = createScene();
  const viewerSettings = loadViewerSettings();
  app.cameraMode = viewerSettings.startupCameraMode || 'perspective';

  const width = Math.max(1, app.container.clientWidth);
  const height = Math.max(1, app.container.clientHeight);
  const aspect = width / height;
  if (app.cameraMode === 'orthographic') {
    const size = getOrthoSize();
    app.camera = createOrthoCamera(aspect, size);
  } else {
    app.camera = createPerspectiveCamera(aspect);
  }

  try {
    app.renderer = new THREE.WebGLRenderer({ antialias: true });
    app.renderer.setSize(width, height);
    app.renderer.setPixelRatio(window.devicePixelRatio);
    app.container.appendChild(app.renderer.domElement);
  } catch (error) {
    app.renderer = null;
    app.controls = null;
    app.sceneInitError = error;
    console.error('Failed to initialize WebGL renderer:', error);
    app.stats.innerText = 'Viewport unavailable: WebGL failed to initialize';
    return false;
  }

  app.controls = new OrbitControls(app.camera, app.renderer.domElement);
  applyViewerSettingsToControls(app.controls, viewerSettings);
  setInvertWheelZoom(app.renderer.domElement, viewerSettings.invertWheelZoom);
  window.addEventListener('resize', () => onResize(app));
  animate(app);
  return true;
}

export function onResize(app) {
  if (!app.camera || !app.renderer) return;
  const width = app.container.clientWidth;
  const height = app.container.clientHeight;
  if (width <= 0 || height <= 0) return;
  const aspect = width / height;

  if (app.cameraMode === 'perspective') {
    app.camera.aspect = aspect;
  } else {
    const size = getOrthoSize();
    app.camera.left = -size * aspect;
    app.camera.right = size * aspect;
    app.camera.top = size;
    app.camera.bottom = -size;
  }

  app.camera.updateProjectionMatrix();
  app.renderer.setSize(width, height);
}

export function renderModel(app) {
  if (!app.scene || !app.renderer) return;
  if (app.hornMesh) {
    app.scene.remove(app.hornMesh);
    app.hornMesh.geometry.dispose();
    app.hornMesh.material.dispose();
  }

  const preparedParams = app.prepareParamsForMesh({
    applyVerticalOffset: true
  });

  // Viewport always uses the formula-based mesh — evaluates profile math
  // directly at every grid point. Export and simulation flows use a
  // canonical tagged payload derived from the same geometry equations.
  const artifacts = buildGeometryArtifacts(preparedParams, {
    adaptivePhi: false
  });
  const { vertices, indices } = artifacts.mesh;
  applyMeshToScene(app, vertices, indices, preparedParams);
}

/**
 * Apply vertex/index data to the Three.js scene.
 */
function applyMeshToScene(app, vertices, indices, preparedParams, normals) {
  if (app.hornMesh) {
    app.scene.remove(app.hornMesh);
    app.hornMesh.geometry.dispose();
    app.hornMesh.material.dispose();
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  geometry.setIndex(Array.from(indices));

  if (normals && normals.length === vertices.length) {
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  } else {
    geometry.computeVertexNormals();
  }

  const displayMode = getDisplayMode();
  let material;

  if (displayMode === 'zebra') {
    material = new THREE.ShaderMaterial({
      ...ZebraShader,
      side: THREE.DoubleSide
    });
  } else if (displayMode === 'curvature') {
    const ang = preparedParams.angularSegments || 80;
    const len = preparedParams.lengthSegments || 20;
    const colors = calculateCurvatureColors(geometry, ang, len);
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    material = new THREE.MeshPhongMaterial({ vertexColors: true, side: THREE.DoubleSide });
  } else {
    material = new THREE.MeshPhysicalMaterial({
      color: 0xcccccc,
      metalness: 0.5,
      roughness: 0.3,
      transmission: 0,
      transparent: true,
      opacity: 0.9,
      side: THREE.DoubleSide,
      wireframe: displayMode === 'grid'
    });
  }

  app.hornMesh = new THREE.Mesh(geometry, material);
  app.scene.add(app.hornMesh);

  app.stats.innerText = `Vertices: ${vertices.length / 3} | Triangles: ${indices.length / 3}`;
}

export function calculateCurvatureColors(geometry, radialSteps, lengthSteps) {
  const normals = geometry.attributes.normal.array;
  const count = normals.length / 3;
  const colors = new Float32Array(count * 3);

  for (let j = 0; j <= lengthSteps; j++) {
    for (let i = 0; i <= radialSteps; i++) {
      const idx = (j * (radialSteps + 1) + i) * 3;
      let curvature = 0;
      const neighbors = [
        [j - 1, i],
        [j + 1, i],
        [j, i - 1],
        [j, i + 1]
      ];
      const nx = normals[idx];
      const ny = normals[idx + 1];
      const nz = normals[idx + 2];
      let sampleCount = 0;
      neighbors.forEach(([nj, ni]) => {
        if (nj >= 0 && nj <= lengthSteps && ni >= 0 && ni <= radialSteps) {
          const nIdx = (nj * (radialSteps + 1) + ni) * 3;
          const d = 1.0 - (nx * normals[nIdx] + ny * normals[nIdx + 1] + nz * normals[nIdx + 2]);
          curvature += d;
          sampleCount++;
        }
      });
      const c = Math.min(1.0, (curvature / sampleCount) * 50.0);
      colors[idx] = c;
      colors[idx + 1] = 1 - c;
      colors[idx + 2] = 0.5;
    }
  }
  return colors;
}

export function focusOnModel(app) {
  if (!app.hornMesh || !app.controls) return;
  app.hornMesh.geometry.computeBoundingBox();
  const box = app.hornMesh.geometry.boundingBox;
  const center = new THREE.Vector3();
  box.getCenter(center);
  app.controls.target.copy(center);
  app.controls.update();
}

export function zoom(app, factor) {
  if (!app.camera || !app.controls) return;
  if (app.cameraMode === 'perspective') {
    app.camera.position.multiplyScalar(factor);
  } else {
    app.camera.zoom /= factor;
    app.camera.updateProjectionMatrix();
  }
  app.controls.update();
}

export function toggleCamera(app) {
  if (!app.camera || !app.controls || !app.renderer || !app.scene) return;
  const width = app.container.clientWidth;
  const height = app.container.clientHeight;
  const aspect = width / height;
  const pos = app.camera.position.clone();
  const target = app.controls.target.clone();

  if (app.cameraMode === 'perspective') {
    const size = getOrthoSize();
    app.camera = createOrthoCamera(aspect, size);
    app.cameraMode = 'orthographic';
    document.getElementById('camera-toggle').innerText = '▲';
  } else {
    app.camera = createPerspectiveCamera(aspect);
    app.cameraMode = 'perspective';
    document.getElementById('camera-toggle').innerText = '⬚';
  }

  app.camera.position.copy(pos);
  app.scene.add(app.camera);

  const oldControls = app.controls;
  app.controls = new OrbitControls(app.camera, app.renderer.domElement);
  app.controls.target.copy(target);
  const vs = getCurrentViewerSettings();
  applyViewerSettingsToControls(app.controls, vs);
  setInvertWheelZoom(app.renderer.domElement, vs.invertWheelZoom);
  app.controls.update();
  oldControls.dispose();
}

export function getOrthoSize() {
  return 300;
}

function animate(app) {
  if (!app.renderer || !app.camera || !app.scene || !app.controls) return;
  requestAnimationFrame(() => animate(app));
  app.controls.update();
  app.renderer.render(app.scene, app.camera);
}
