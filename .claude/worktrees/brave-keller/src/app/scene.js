import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  createScene,
  createPerspectiveCamera,
  createOrthoCamera,
  ZebraShader,
  getSceneThemeColors,
} from "../viewer/index.js";
import { prepareViewportMesh } from "../modules/geometry/useCases.js";
import { detachThroatDiscVertices } from "./viewportMesh.js";
import { ImportedMeshState } from "../state.js";
import { AppEvents } from "../events.js";

export function setupScene(app) {
  app.scene = createScene();
  const viewerSettings = app.uiCoordinator.loadViewerSettings();
  app.cameraMode = viewerSettings.startupCameraMode || "perspective";
  app.needsRender = true;
  app.currentDisplayMode = null;

  const width = Math.max(1, app.container.clientWidth);
  const height = Math.max(1, app.container.clientHeight);
  const aspect = width / height;
  if (app.cameraMode === "orthographic") {
    const size = getOrthoSize();
    app.camera = createOrthoCamera(aspect, size);
  } else {
    app.camera = createPerspectiveCamera(aspect);
  }

  try {
    app.renderer = new THREE.WebGLRenderer({ antialias: true });
    app.renderer.setSize(width, height);
    app.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    app.container.appendChild(app.renderer.domElement);
  } catch (error) {
    app.renderer = null;
    app.controls = null;
    app.sceneInitError = error;
    console.error("Failed to initialize WebGL renderer:", error);
    const fallback = document.getElementById("webgl-fallback");
    if (fallback) fallback.style.display = "flex";
    app.stats.innerText = "Viewport unavailable: WebGL failed to initialize";
    return false;
  }

  app.controls = new OrbitControls(app.camera, app.renderer.domElement);
  app.uiCoordinator.applyViewerSettingsToControls(app.controls, viewerSettings);
  app.uiCoordinator.configureWheelZoomInversion(
    app.renderer.domElement,
    viewerSettings.invertWheelZoom,
  );

  let resizeTimeout;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => onResize(app), 100);
  });

  // Update scene background when OS color scheme changes
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  darkQuery.addEventListener("change", () => {
    if (app.scene) {
      const colors = getSceneThemeColors();
      app.scene.background = colors.bg;
      app.needsRender = true;
    }
  });

  // Re-render when an external mesh is imported
  AppEvents.on("mesh:imported", () => {
    renderModel(app);
    app.needsRender = true;
  });

  app.controls.addEventListener("change", () => {
    app.needsRender = true;
  });

  animate(app);
  return true;
}

export function onResize(app) {
  if (!app.camera || !app.renderer) return;
  const width = app.container.clientWidth;
  const height = app.container.clientHeight;
  if (width <= 0 || height <= 0) return;
  const aspect = width / height;

  if (app.cameraMode === "perspective") {
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
  app.needsRender = true;
}

export function renderModel(app) {
  if (!app.scene || !app.renderer) return;

  // Imported mesh mode — render imported data instead of parametric model
  if (
    ImportedMeshState.active &&
    ImportedMeshState.vertices &&
    ImportedMeshState.indices
  ) {
    if (app.hornMesh) {
      app.scene.remove(app.hornMesh);
      app.hornMesh.geometry.dispose();
      app.hornMesh.material.dispose();
    }
    applyMeshToScene(
      app,
      ImportedMeshState.vertices,
      ImportedMeshState.indices,
      {},
    );

    // Color-code by physical group tags if available
    if (ImportedMeshState.physicalTags && app.hornMesh) {
      const colors = buildPhysicalGroupColors(
        ImportedMeshState.vertices,
        ImportedMeshState.indices,
        ImportedMeshState.physicalTags,
      );
      app.hornMesh.geometry.setAttribute(
        "color",
        new THREE.Float32BufferAttribute(colors, 3),
      );
      app.hornMesh.material.dispose();
      app.hornMesh.material = new THREE.MeshPhongMaterial({
        vertexColors: true,
        side: THREE.DoubleSide,
      });
    }
    app.needsRender = true;
    return;
  }

  if (!app.currentState) return;
  if (app.hornMesh) {
    app.scene.remove(app.hornMesh);
    app.hornMesh.geometry.dispose();
    app.hornMesh.material.dispose();
  }

  const viewportMesh = prepareViewportMesh(app.currentState);
  const renderMesh = detachThroatDiscVertices(viewportMesh);
  applyMeshToScene(
    app,
    renderMesh.vertices,
    renderMesh.indices,
    viewportMesh.preparedParams,
  );
  app.needsRender = true;
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
  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(vertices, 3),
  );
  geometry.setIndex(Array.from(indices));

  if (normals && normals.length === vertices.length) {
    geometry.setAttribute(
      "normal",
      new THREE.Float32BufferAttribute(normals, 3),
    );
  } else {
    geometry.computeVertexNormals();
  }

  const displayMode = app.uiCoordinator.readDisplayModeSetting();
  let material;

  if (displayMode === "zebra") {
    material = new THREE.ShaderMaterial({
      ...ZebraShader,
      side: THREE.DoubleSide,
    });
  } else if (displayMode === "curvature") {
    const ang = preparedParams.angularSegments || 80;
    const len = preparedParams.lengthSegments || 20;
    const colors = calculateCurvatureColors(geometry, ang, len);
    geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    material = new THREE.MeshPhongMaterial({
      vertexColors: true,
      side: THREE.DoubleSide,
    });
  } else {
    material = new THREE.MeshPhysicalMaterial({
      color: 0xcccccc,
      metalness: 0.5,
      roughness: 0.3,
      transmission: 0,
      transparent: true,
      opacity: 0.9,
      side: THREE.DoubleSide,
      wireframe: displayMode === "grid",
    });
  }

  app.hornMesh = new THREE.Mesh(geometry, material);
  app.scene.add(app.hornMesh);

  const viewportStats = {
    vertexCount: vertices.length / 3,
    triangleCount: indices.length / 3,
  };
  if (typeof app.setViewportMeshStats === "function") {
    app.setViewportMeshStats(viewportStats);
  } else if (app.stats) {
    app.stats.innerText = `Viewport: ${viewportStats.vertexCount} vertices | ${viewportStats.triangleCount} triangles`;
  }
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
        [j, i + 1],
      ];
      const nx = normals[idx];
      const ny = normals[idx + 1];
      const nz = normals[idx + 2];
      let sampleCount = 0;
      neighbors.forEach(([nj, ni]) => {
        if (nj >= 0 && nj <= lengthSteps && ni >= 0 && ni <= radialSteps) {
          const nIdx = (nj * (radialSteps + 1) + ni) * 3;
          const d =
            1.0 -
            (nx * normals[nIdx] +
              ny * normals[nIdx + 1] +
              nz * normals[nIdx + 2]);
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

/**
 * Build per-vertex colors from per-triangle physical group tags.
 * Tag 1 (wall) = grey, Tag 2 (source) = green, Tag 3 (enclosure) = blue, other = orange.
 */
export function buildPhysicalGroupColors(vertices, indices, physicalTags) {
  const TAG_COLORS = {
    1: [0.8, 0.8, 0.8], // wall (SD1G0) — grey
    2: [0.3, 0.8, 0.3], // source/throat (SD1D1001) — green
    3: [0.4, 0.6, 0.9], // enclosure (SD2G0) — blue
  };
  const DEFAULT_COLOR = [0.9, 0.6, 0.3]; // orange

  const vertexCount = vertices.length / 3;
  const colors = new Float32Array(vertexCount * 3);
  const assigned = new Uint8Array(vertexCount); // 0 = not yet assigned

  const triCount = indices.length / 3;
  for (let t = 0; t < triCount; t++) {
    const tag = physicalTags[t];
    const rgb = TAG_COLORS[tag] || DEFAULT_COLOR;
    for (let k = 0; k < 3; k++) {
      const vi = indices[t * 3 + k];
      if (!assigned[vi]) {
        colors[vi * 3] = rgb[0];
        colors[vi * 3 + 1] = rgb[1];
        colors[vi * 3 + 2] = rgb[2];
        assigned[vi] = 1;
      }
    }
  }
  return colors;
}

export function focusOnModel(app) {
  if (!app.controls) return;
  if (app.focusedOnModel) {
    app.controls.target.set(0, 0, 0);
    app.focusedOnModel = false;
  } else {
    if (!app.hornMesh) return;
    app.hornMesh.geometry.computeBoundingBox();
    const box = app.hornMesh.geometry.boundingBox;
    const center = new THREE.Vector3();
    box.getCenter(center);
    app.controls.target.copy(center);
    app.focusedOnModel = true;
  }
  app.controls.update();
  app.needsRender = true;
}

export function zoom(app, factor) {
  if (!app.camera || !app.controls) return;
  if (app.cameraMode === "perspective") {
    app.camera.position.multiplyScalar(factor);
  } else {
    app.camera.zoom /= factor;
    app.camera.updateProjectionMatrix();
  }
  app.controls.update();
  app.needsRender = true;
}

export function toggleCamera(app) {
  if (!app.camera || !app.controls || !app.renderer || !app.scene) return;
  const width = app.container.clientWidth;
  const height = app.container.clientHeight;
  const aspect = width / height;
  const pos = app.camera.position.clone();
  const target = app.controls.target.clone();

  if (app.cameraMode === "perspective") {
    const size = getOrthoSize();
    app.camera = createOrthoCamera(aspect, size);
    app.cameraMode = "orthographic";
    document.getElementById("camera-toggle").innerText = "▲";
  } else {
    app.camera = createPerspectiveCamera(aspect);
    app.cameraMode = "perspective";
    document.getElementById("camera-toggle").innerText = "⬚";
  }

  app.camera.position.copy(pos);
  app.scene.add(app.camera);

  const oldControls = app.controls;
  app.controls = new OrbitControls(app.camera, app.renderer.domElement);
  app.controls.target.copy(target);
  const vs = app.uiCoordinator.getViewerSettings();
  app.uiCoordinator.applyViewerSettingsToControls(app.controls, vs);
  app.uiCoordinator.configureWheelZoomInversion(
    app.renderer.domElement,
    vs.invertWheelZoom,
  );
  app.controls.addEventListener("change", () => {
    app.needsRender = true;
  });
  app.controls.update();
  oldControls.dispose();
  app.needsRender = true;
}

export function getOrthoSize() {
  return 300;
}

function animate(app) {
  if (!app.renderer || !app.camera || !app.scene || !app.controls) return;
  requestAnimationFrame(() => animate(app));
  app.controls.update();
  if (app.needsRender) {
    app.renderer.render(app.scene, app.camera);
    app.needsRender = false;
  }
}
