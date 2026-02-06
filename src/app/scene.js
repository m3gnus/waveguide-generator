import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import {
  createScene,
  createPerspectiveCamera,
  createOrthoCamera,
  ZebraShader
} from '../viewer/index.js';
import { buildHornMesh } from '../geometry/index.js';

export function setupScene(app) {
  app.scene = createScene();

  app.cameraMode = 'perspective';
  const aspect = app.container.clientWidth / app.container.clientHeight;
  app.camera = createPerspectiveCamera(aspect);

  app.renderer = new THREE.WebGLRenderer({ antialias: true });
  app.renderer.setSize(app.container.clientWidth, app.container.clientHeight);
  app.renderer.setPixelRatio(window.devicePixelRatio);
  app.container.appendChild(app.renderer.domElement);

  app.controls = new OrbitControls(app.camera, app.renderer.domElement);
  app.controls.enableDamping = true;

  window.addEventListener('resize', () => onResize(app));

  animate(app);
}

export function onResize(app) {
  const width = app.container.clientWidth;
  const height = app.container.clientHeight;
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
  if (app.hornMesh) {
    app.scene.remove(app.hornMesh);
    app.hornMesh.geometry.dispose();
    app.hornMesh.material.dispose();
  }

  const preparedParams = app.prepareParamsForMesh({
    forceFullQuadrants: true,
    applyVerticalOffset: true
  });

  // Viewport always uses the formula-based mesh — evaluates profile math
  // directly at every grid point. CAD pipeline is used only for export
  // (STEP, MSH, ABEC) where exact parametric geometry matters.
  const { vertices, indices } = buildHornMesh(preparedParams);
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

  const displayMode = document.getElementById('display-mode')?.value || 'standard';
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
  if (!app.hornMesh) return;
  app.hornMesh.geometry.computeBoundingBox();
  const box = app.hornMesh.geometry.boundingBox;
  const center = new THREE.Vector3();
  box.getCenter(center);
  app.controls.target.copy(center);
  app.controls.update();
}

export function zoom(app, factor) {
  if (app.cameraMode === 'perspective') {
    app.camera.position.multiplyScalar(factor);
  } else {
    app.camera.zoom /= factor;
    app.camera.updateProjectionMatrix();
  }
  app.controls.update();
}

export function toggleCamera(app) {
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
  app.controls.enableDamping = true;
  app.controls.update();
  oldControls.dispose();
}

export function getOrthoSize() {
  return 300;
}

function animate(app) {
  requestAnimationFrame(() => animate(app));
  app.controls.update();
  app.renderer.render(app.scene, app.camera);
}
