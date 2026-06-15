import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import {
  createScene,
  createPerspectiveCamera,
  createOrthoCamera,
  ZebraShader,
  getSceneThemeColors,
  attachCameraLights,
} from '../viewer/index.js';
import {
  prepareBackendViewportMesh,
  prepareViewportMesh,
  validateViewportMesh,
  isServerOnlyViewportFormula,
} from '../modules/geometry/useCases.js';
import { detachCreaseVertices } from './viewportMesh.js';
import { ImportedMeshState } from '../state.js';
import { AppEvents } from '../events.js';
import { PARAM_SCHEMA } from '../config/schema.js';
import { createPerfTimer, measurePerf } from '../logging/performance.js';

const GRID_DISPLAY_MODES = new Set(['wireframe', 'solidwire']);
// After a failed backend viewport fetch, render with the local JS engine and
// only retry the backend once this cooldown has elapsed.
const BACKEND_VIEWPORT_RETRY_MS = 15000;
const BACKEND_VIEWPORT_TIMEOUT_MS = 5000;
const VIEWPORT_CACHE_SCHEMA_GROUPS = ['GEOMETRY', 'MORPH', 'MESH', 'ENCLOSURE', 'SOURCE'];
const VIEWPORT_CACHE_PARAM_KEYS = new Set();
for (const group of VIEWPORT_CACHE_SCHEMA_GROUPS) {
  for (const key of Object.keys(PARAM_SCHEMA[group] || {})) {
    VIEWPORT_CACHE_PARAM_KEYS.add(key);
  }
}

function variantForDisplayMode(mode) {
  return GRID_DISPLAY_MODES.has(mode) ? 'grid' : 'smooth';
}

function resetMeshCache(app) {
  app._meshCache = { grid: null, smooth: null, stateKey: null };
  app._currentMeshVariant = null;
}

function getViewportStateCacheKey(state = {}) {
  const type = state.type || '';
  const params = state.params || {};
  const modelKeys = Object.keys(PARAM_SCHEMA[type] || {});
  const keyParts = [`type:${type}`];

  for (const key of [...modelKeys, ...VIEWPORT_CACHE_PARAM_KEYS].sort()) {
    keyParts.push(`${key}:${JSON.stringify(params[key])}`);
  }
  return keyParts.join('|');
}

function invalidateMeshCacheIfStale(app) {
  if (!app._meshCache) resetMeshCache(app);
  const versionKey = getViewportStateCacheKey(app.currentState || {});
  if (app._meshCache.stateKey !== versionKey) {
    app._meshCache.grid = null;
    app._meshCache.smooth = null;
    app._meshCache.stateKey = versionKey;
  }
}

function toRenderMesh(viewportMesh, variant, perf) {
  const renderMesh = detachCreaseVertices(viewportMesh);
  perf?.mark('detachCreaseVertices', {
    vertexCount: renderMesh.vertices.length / 3,
    triangleCount: renderMesh.indices.length / 3,
  });
  // The full integrity audit is O(triangles) on every rebuild; it exists to
  // catch detachCreaseVertices regressions, so it only runs when debugging is
  // explicitly forced (covered by viewport-render-mesh.test.js otherwise).
  if (globalThis.__WAVEGUIDE_DEBUG__ === true) {
    const integrity = validateViewportMesh(renderMesh);
    perf?.mark('validateViewportMesh', { ok: integrity.ok });
    if (!integrity.ok) {
      console.error(
        `[Viewport] Mesh integrity violation after detachCreaseVertices (${variant}):\n  - ${integrity.errors.join('\n  - ')}`,
        integrity.report
      );
    }
  }
  return {
    vertices: renderMesh.vertices,
    indices: renderMesh.indices,
    normals: renderMesh.normals,
    preparedParams: viewportMesh.preparedParams,
  };
}

function buildVariantMesh(state, variant) {
  const perf = createPerfTimer(`viewportMesh:${variant}`);
  const viewportMesh = prepareViewportMesh(state, { variant });
  perf.mark('prepareViewportMesh', {
    vertexCount: viewportMesh.vertices.length / 3,
    triangleCount: viewportMesh.indices.length / 3,
  });
  const result = { ...toRenderMesh(viewportMesh, variant, perf), source: 'local' };
  perf.end();
  return result;
}

function getOrBuildVariant(app, variant) {
  invalidateMeshCacheIfStale(app);
  const cached = app._meshCache[variant];
  if (cached) return cached;
  const built = buildVariantMesh(app.currentState, variant);
  app._meshCache[variant] = built;
  return built;
}

function isBackendViewportInCooldown(app) {
  return (
    Number.isFinite(app._viewportBackendDownAt) &&
    Date.now() - app._viewportBackendDownAt < BACKEND_VIEWPORT_RETRY_MS
  );
}

function applyVariantToScene(app, variant, mesh) {
  applyMeshToScene(app, mesh.vertices, mesh.indices, mesh.preparedParams, mesh.normals);
  app._currentMeshVariant = variant;
  app.needsRender = true;
}

/** Apply `mesh` only if the app still wants this state + variant. */
function applyVariantIfCurrent(app, stateKey, variant, mesh) {
  if (ImportedMeshState.active && ImportedMeshState.vertices && ImportedMeshState.indices) return;
  if (!app._meshCache || app._meshCache.stateKey !== stateKey) return;
  const mode = app.uiCoordinator.readDisplayModeSetting();
  if (variantForDisplayMode(mode) !== variant) return;
  applyVariantToScene(app, variant, mesh);
}

/**
 * Fetch viewport geometry from the backend mesher and apply it when it lands.
 * Stale responses (state changed while in flight) are discarded; failures put
 * the backend on a retry cooldown and fall back to the local JS engine.
 */
function startBackendViewportBuild(app, variant) {
  invalidateMeshCacheIfStale(app);
  const stateKey = app._meshCache.stateKey;
  const fetchKey = `${stateKey}::${variant}`;
  if (app._viewportFetch?.key === fetchKey) return;
  if (app._viewportFetch) {
    // A newer state/variant superseded the in-flight fetch; its abort must
    // not count as a backend failure.
    app._viewportFetch.superseded = true;
    app._viewportFetch.controller?.abort();
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), BACKEND_VIEWPORT_TIMEOUT_MS);
  const fetchRecord = { key: fetchKey, controller, superseded: false };
  app._viewportFetch = fetchRecord;

  prepareBackendViewportMesh(app.currentState, { variant, signal: controller.signal })
    .then((viewportMesh) => {
      if (fetchRecord.superseded) return;
      const built = { ...toRenderMesh(viewportMesh, variant), source: 'backend' };
      app._viewportBackendDownAt = null;
      invalidateMeshCacheIfStale(app);
      if (app._meshCache.stateKey !== stateKey) return;
      app._meshCache[variant] = built;
      applyVariantIfCurrent(app, stateKey, variant, built);
    })
    .catch((error) => {
      if (fetchRecord.superseded) return;
      const status = typeof error?.status === 'number' ? error.status : null;
      // A 4xx means the backend is up but rejected these params (e.g. an
      // infeasible ICW rollback). Do NOT enter the backend-down cooldown — that
      // would block the rebuild once the user corrects the offending input.
      const isValidationError = status !== null && status >= 400 && status < 500;
      const isAbort = error?.name === 'AbortError';
      if (!isValidationError) {
        app._viewportBackendDownAt = Date.now();
      }
      console.warn('[Viewport] Backend viewport geometry unavailable:', error?.message || error);
      invalidateMeshCacheIfStale(app);
      if (app._meshCache.stateKey !== stateKey) return;
      // Server-only formulas have no JS engine fallback. Surface the failure
      // instead of silently leaving the prior frame on screen, so an infeasible
      // or invalid solve reads as an error rather than a frozen viewport.
      if (isServerOnlyViewportFormula(app.currentState)) {
        reportMeshBuildFailure(
          app,
          isAbort ? new Error('Backend geometry request timed out.') : error
        );
        return;
      }
      try {
        const mesh = getOrBuildVariant(app, variant);
        applyVariantIfCurrent(app, stateKey, variant, mesh);
      } catch (buildError) {
        reportMeshBuildFailure(app, buildError);
      }
    })
    .finally(() => {
      clearTimeout(timeout);
      if (app._viewportFetch === fetchRecord) {
        app._viewportFetch = null;
      }
    });
}

function reportMeshBuildFailure(app, error) {
  // Backend errors carry the mesher's own reason on `backendDetail` (e.g. an
  // ICW infeasibility hint); prefer it over the wrapped HTTP message.
  const detail = error?.backendDetail || error?.message || error;
  console.error('[Viewport] Mesh build failed:', detail);
  if (typeof app.uiCoordinator?.showError === 'function') {
    try {
      app.uiCoordinator.showError(`Mesh build failed: ${detail}`);
    } catch {
      // Optional toast surface.
    }
  }
}

/**
 * Resolve the mesh for a display variant. Cached meshes return immediately.
 * Otherwise the backend mesher builds asynchronously (the previous mesh stays
 * on screen until it lands); the local JS engine covers backend-down cooldown
 * windows and the very first paint, where a blank viewport would be worse
 * than one synchronous local build.
 */
function resolveVariantMesh(app, variant) {
  invalidateMeshCacheIfStale(app);
  // Server-only formulas (e.g. ICW) have no local JS engine implementation, so
  // they must never fall back to getOrBuildVariant — that would render an
  // incorrect OSSE-shaped profile. They render exclusively via the backend; a
  // null return keeps the prior frame on screen until the backend mesh lands.
  const serverOnly = isServerOnlyViewportFormula(app.currentState);
  const cached = app._meshCache[variant];
  if (cached) {
    // A locally-built mesh can be upgraded to backend geometry once the
    // cooldown lapses; the upgrade applies asynchronously.
    if (cached.source === 'local' && !isBackendViewportInCooldown(app)) {
      startBackendViewportBuild(app, variant);
    }
    return cached;
  }
  if (serverOnly) {
    if (!isBackendViewportInCooldown(app)) {
      startBackendViewportBuild(app, variant);
    }
    return null;
  }
  if (isBackendViewportInCooldown(app)) {
    return getOrBuildVariant(app, variant);
  }
  startBackendViewportBuild(app, variant);
  if (!app.hornMesh) {
    return getOrBuildVariant(app, variant);
  }
  return null;
}

export function setupScene(app) {
  app.scene = createScene();
  const viewerSettings = app.uiCoordinator.loadViewerSettings();
  app.cameraMode = viewerSettings.startupCameraMode || 'perspective';
  app.needsRender = true;
  app.currentDisplayMode = null;

  const width = Math.max(1, app.container.clientWidth);
  const height = Math.max(1, app.container.clientHeight);
  const aspect = width / height;
  if (app.cameraMode === 'orthographic') {
    const size = getOrthoSize();
    app.camera = createOrthoCamera(aspect, size);
  } else {
    app.camera = createPerspectiveCamera(aspect);
  }
  app.scene.add(app.camera);
  attachCameraLights(app.camera, getSceneThemeColors());

  try {
    app.renderer = new THREE.WebGLRenderer({ antialias: true });
    app.renderer.setSize(width, height);
    app.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    app.container.appendChild(app.renderer.domElement);
  } catch (error) {
    app.renderer = null;
    app.controls = null;
    app.sceneInitError = error;
    console.error('Failed to initialize WebGL renderer:', error);
    const fallback = document.getElementById('webgl-fallback');
    if (fallback) fallback.style.display = 'flex';
    app.stats.innerText = 'Viewport unavailable: WebGL failed to initialize';
    return false;
  }

  app.controls = createConfiguredControls(app, viewerSettings);

  let resizeTimeout;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => onResize(app), 100);
  });

  // Update scene background when OS color scheme changes
  const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
  darkQuery.addEventListener('change', () => {
    if (app.scene) {
      const colors = getSceneThemeColors();
      app.scene.background = colors.bg;
      app.needsRender = true;
    }
  });

  // Re-render when an external mesh is imported
  AppEvents.on('mesh:imported', () => {
    renderModel(app);
    app.needsRender = true;
  });

  app.controls.addEventListener('change', () => {
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
  app.needsRender = true;
}

export function renderModel(app) {
  if (!app.scene || !app.renderer) return;

  // Imported mesh mode — render imported data instead of parametric model
  if (ImportedMeshState.active && ImportedMeshState.vertices && ImportedMeshState.indices) {
    const mode = app.uiCoordinator.readDisplayModeSetting();
    const cache = app._importedMeshRenderCache;
    if (
      cache &&
      cache.mesh === app.hornMesh &&
      cache.vertices === ImportedMeshState.vertices &&
      cache.indices === ImportedMeshState.indices &&
      cache.physicalTags === ImportedMeshState.physicalTags &&
      cache.displayMode === mode
    ) {
      app.needsRender = true;
      return;
    }
    const perf = createPerfTimer('importedMesh:renderModel');
    applyMeshToScene(app, ImportedMeshState.vertices, ImportedMeshState.indices, {}, null, mode, {
      physicalTags: ImportedMeshState.physicalTags,
      imported: true,
    });
    perf.end({
      vertexCount: ImportedMeshState.vertices.length / 3,
      triangleCount: ImportedMeshState.indices.length / 3,
      displayMode: mode,
      physicalTags: Boolean(ImportedMeshState.physicalTags),
    });
    app._importedMeshRenderCache = {
      mesh: app.hornMesh,
      vertices: ImportedMeshState.vertices,
      indices: ImportedMeshState.indices,
      physicalTags: ImportedMeshState.physicalTags,
      displayMode: mode,
    };
    app.needsRender = true;
    return;
  }

  if (!app.currentState) return;
  const mode = app.uiCoordinator.readDisplayModeSetting();
  const variant = variantForDisplayMode(mode);
  let mesh;
  try {
    mesh = resolveVariantMesh(app, variant);
  } catch (error) {
    reportMeshBuildFailure(app, error);
    return;
  }
  // No mesh yet: a backend build is in flight and will apply when it lands;
  // the previous mesh stays on screen meanwhile.
  if (!mesh) return;
  applyVariantToScene(app, variant, mesh);
}

/**
 * Apply vertex/index data to the Three.js scene.
 */
function applyMeshToScene(app, vertices, indices, preparedParams, normals, mode, options = {}) {
  const perf = createPerfTimer(
    options.imported ? 'importedMesh:applyMeshToScene' : 'applyMeshToScene'
  );
  if (app.hornMesh) {
    app.scene.remove(app.hornMesh);
    app.hornMesh.geometry.dispose();
    app.hornMesh.material.dispose();
    app._importedMeshRenderCache = null;
  }
  removeOverlays(app);

  app.lastPreparedParams = preparedParams || {};

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', createFloatAttribute(vertices, 3));
  geometry.setIndex(createIndexAttribute(indices));
  perf.mark('attributes-and-index', {
    vertexCount: vertices.length / 3,
    triangleCount: indices.length / 3,
  });

  if (normals && normals.length === vertices.length) {
    geometry.setAttribute('normal', createFloatAttribute(normals, 3));
  } else {
    measurePerf('BufferGeometry.computeVertexNormals', () => geometry.computeVertexNormals(), {
      vertexCount: vertices.length / 3,
      triangleCount: indices.length / 3,
    });
  }
  perf.mark('normals');

  const displayMode = mode || app.uiCoordinator.readDisplayModeSetting();
  const material = options.physicalTags
    ? createPhysicalGroupMaterial(geometry, vertices, indices, options.physicalTags)
    : createMaterialForMode(displayMode, geometry);
  perf.mark(options.physicalTags ? 'physical-material' : 'display-material', { displayMode });

  app.hornMesh = new THREE.Mesh(geometry, material);
  app.scene.add(app.hornMesh);
  addOverlaysForMode(app, displayMode);
  perf.mark('scene-attach', { displayMode });

  const viewportStats = {
    vertexCount: vertices.length / 3,
    triangleCount: indices.length / 3,
  };
  if (typeof app.setViewportMeshStats === 'function') {
    app.setViewportMeshStats(viewportStats);
  } else if (app.stats) {
    app.stats.innerText = `Viewport: ${viewportStats.vertexCount} vertices | ${viewportStats.triangleCount} triangles`;
  }
  perf.end();
}

function curvatureJetColor(t) {
  // Multi-stop jet colormap: dark blue → blue → cyan → green → yellow → red → dark red
  const stops = [
    [0.0, [0.0, 0.0, 0.5]],
    [0.125, [0.0, 0.0, 1.0]],
    [0.375, [0.0, 1.0, 1.0]],
    [0.625, [1.0, 1.0, 0.0]],
    [0.875, [1.0, 0.0, 0.0]],
    [1.0, [0.5, 0.0, 0.0]],
  ];
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, c0] = stops[i];
    const [t1, c1] = stops[i + 1];
    if (t >= t0 && t <= t1) {
      const f = (t - t0) / (t1 - t0);
      return [
        c0[0] + f * (c1[0] - c0[0]),
        c0[1] + f * (c1[1] - c0[1]),
        c0[2] + f * (c1[2] - c0[2]),
      ];
    }
  }
  return stops[stops.length - 1][1];
}

export function calculateCurvatureColors(geometry) {
  const normals = geometry.attributes.normal.array;
  const count = normals.length / 3;
  const rawCurvature = new Float32Array(count);
  const neighborCount = new Int32Array(count);

  // Build adjacency from index buffer — covers horn grid AND enclosure vertices
  const indexAttr = geometry.index;
  if (indexAttr) {
    const idx = indexAttr.array;
    const triCount = idx.length / 3;
    for (let t = 0; t < triCount; t++) {
      const a = idx[t * 3];
      const b = idx[t * 3 + 1];
      const c = idx[t * 3 + 2];
      const pairs = [
        [a, b],
        [b, c],
        [a, c],
      ];
      for (const [vi, vj] of pairs) {
        const ni = vi * 3,
          nj = vj * 3;
        const dot = Math.max(
          -1,
          Math.min(
            1,
            normals[ni] * normals[nj] +
              normals[ni + 1] * normals[nj + 1] +
              normals[ni + 2] * normals[nj + 2]
          )
        );
        const d = 1.0 - dot;
        rawCurvature[vi] += d;
        neighborCount[vi]++;
        rawCurvature[vj] += d;
        neighborCount[vj]++;
      }
    }
  }

  // Average per-vertex curvature
  let maxC = 0;
  for (let v = 0; v < count; v++) {
    if (neighborCount[v] > 0) {
      rawCurvature[v] /= neighborCount[v];
      if (rawCurvature[v] > maxC) maxC = rawCurvature[v];
    }
  }

  // Auto-scale: normalize so the 95th-percentile maps to 1.0 (prevents outliers washing out the range)
  const sorted = rawCurvature.slice().sort();
  const p95 = sorted[Math.floor(count * 0.95)] || maxC || 1;
  const scale = p95 > 0 ? 1.0 / p95 : 1.0;

  const colors = new Float32Array(count * 3);
  for (let v = 0; v < count; v++) {
    // Mild power curve to spread mid-range values
    const c = Math.min(1.0, Math.pow(rawCurvature[v] * scale, 0.6));
    const rgb = curvatureJetColor(c);
    colors[v * 3] = rgb[0];
    colors[v * 3 + 1] = rgb[1];
    colors[v * 3 + 2] = rgb[2];
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

function createPhysicalGroupMaterial(geometry, vertices, indices, physicalTags) {
  const colors = measurePerf(
    'physicalTagColors',
    () => buildPhysicalGroupColors(vertices, indices, physicalTags),
    {
      vertexCount: vertices.length / 3,
      triangleCount: indices.length / 3,
    }
  );
  geometry.setAttribute('color', createFloatAttribute(colors, 3));
  return new THREE.MeshPhongMaterial({
    vertexColors: true,
    side: THREE.DoubleSide,
  });
}

function removeOverlays(app) {
  if (app.hornWireOverlay) {
    app.scene.remove(app.hornWireOverlay);
    app.hornWireOverlay.material.dispose();
    app.hornWireOverlay = null;
  }
  if (app.hornEdgeLines) {
    app.scene.remove(app.hornEdgeLines);
    app.hornEdgeLines.geometry.dispose();
    app.hornEdgeLines.material.dispose();
    app.hornEdgeLines = null;
  }
}

function createClayMaterial() {
  return new THREE.MeshStandardMaterial({
    color: 0xb8b0a8,
    roughness: 0.85,
    metalness: 0,
    side: THREE.DoubleSide,
  });
}

function createMaterialForMode(mode, geometry) {
  switch (mode) {
    case 'clay':
    case 'solidwire':
    case 'edges':
      return createClayMaterial();
    case 'wireframe':
      return new THREE.MeshBasicMaterial({
        color: 0xb8b0a8,
        wireframe: true,
        side: THREE.DoubleSide,
      });
    case 'xray':
      return new THREE.MeshPhysicalMaterial({
        color: 0xb8b0a8,
        roughness: 0.6,
        metalness: 0,
        transparent: true,
        opacity: 0.25,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
    case 'zebra':
      return new THREE.ShaderMaterial({
        ...ZebraShader,
        side: THREE.DoubleSide,
      });
    case 'curvature': {
      const colors = calculateCurvatureColors(geometry);
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
      return new THREE.MeshPhongMaterial({
        vertexColors: true,
        side: THREE.DoubleSide,
      });
    }
    default:
      return createClayMaterial();
  }
}

function addOverlaysForMode(app, mode) {
  if (!app.hornMesh) return;
  const geom = app.hornMesh.geometry;

  if (mode === 'solidwire') {
    app.hornWireOverlay = new THREE.Mesh(
      geom,
      new THREE.MeshBasicMaterial({
        color: 0x000000,
        wireframe: true,
        transparent: true,
        opacity: 0.3,
        depthTest: true,
        polygonOffset: true,
        polygonOffsetFactor: -1,
        polygonOffsetUnit: -1,
        side: THREE.DoubleSide,
      })
    );
    app.scene.add(app.hornWireOverlay);
  } else if (mode === 'edges') {
    const edgesGeom = new THREE.EdgesGeometry(geom, 15);
    app.hornEdgeLines = new THREE.LineSegments(
      edgesGeom,
      new THREE.LineBasicMaterial({
        color: 0x000000,
        transparent: true,
        opacity: 0.4,
      })
    );
    app.scene.add(app.hornEdgeLines);
  }
}

export function applyDisplayMode(app, mode) {
  if (!app.hornMesh) return;

  const requiredVariant = variantForDisplayMode(mode);
  const canSwapVariant =
    app.currentState &&
    !(ImportedMeshState.active && ImportedMeshState.vertices && ImportedMeshState.indices);
  if (canSwapVariant && requiredVariant !== app._currentMeshVariant) {
    let mesh;
    try {
      mesh = resolveVariantMesh(app, requiredVariant);
    } catch (error) {
      reportMeshBuildFailure(app, error);
      return;
    }
    if (mesh) {
      applyMeshToScene(app, mesh.vertices, mesh.indices, mesh.preparedParams, mesh.normals, mode);
      app._currentMeshVariant = requiredVariant;
      app.needsRender = true;
      return;
    }
    // Backend build in flight: restyle the current mesh with the new display
    // mode immediately; the correct variant applies when the fetch lands.
  }

  removeOverlays(app);
  app.hornMesh.material.dispose();
  if (
    ImportedMeshState.active &&
    ImportedMeshState.vertices &&
    ImportedMeshState.indices &&
    ImportedMeshState.physicalTags
  ) {
    app.hornMesh.material = createPhysicalGroupMaterial(
      app.hornMesh.geometry,
      ImportedMeshState.vertices,
      ImportedMeshState.indices,
      ImportedMeshState.physicalTags
    );
    if (app._importedMeshRenderCache?.mesh === app.hornMesh) {
      app._importedMeshRenderCache.displayMode = mode;
    }
  } else {
    app.hornMesh.material = createMaterialForMode(mode, app.hornMesh.geometry);
  }
  addOverlaysForMode(app, mode);
  app.needsRender = true;
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
  if (app.cameraMode === 'perspective') {
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
  const width = Math.max(1, app.container.clientWidth);
  const height = Math.max(1, app.container.clientHeight);
  const aspect = width / height;
  const pos = app.camera.position.clone();
  const target = app.controls.target.clone();
  const prevCamera = app.camera;

  if (app.cameraMode === 'perspective') {
    const size = getOrthoSize();
    app.camera = createOrthoCamera(aspect, size);
    app.cameraMode = 'orthographic';
    updateCameraToggleLabel('▲');
  } else {
    app.camera = createPerspectiveCamera(aspect);
    app.cameraMode = 'perspective';
    updateCameraToggleLabel('⬚');
  }

  app.scene.remove(prevCamera);
  app.camera.position.copy(pos);
  app.scene.add(app.camera);
  attachCameraLights(app.camera, getSceneThemeColors());

  const oldControls = app.controls;
  const vs = app.uiCoordinator.getViewerSettings();
  app.controls = createConfiguredControls(app, vs, target);
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

function createConfiguredControls(app, viewerSettings, target = null) {
  const controls = new OrbitControls(app.camera, app.renderer.domElement);
  if (target) {
    controls.target.copy(target);
  }
  app.uiCoordinator.applyViewerSettingsToControls(controls, viewerSettings);
  app.uiCoordinator.configureWheelZoomInversion(
    app.renderer.domElement,
    viewerSettings.invertWheelZoom
  );
  controls.addEventListener('change', () => {
    app.needsRender = true;
  });
  return controls;
}

function createIndexAttribute(indices) {
  let maxIndex = 0;
  for (let i = 0; i < indices.length; i += 1) {
    if (indices[i] > maxIndex) {
      maxIndex = indices[i];
    }
  }
  const ArrayType = maxIndex > 65535 ? Uint32Array : Uint16Array;
  const typedIndices = indices instanceof ArrayType ? indices : new ArrayType(indices);
  if (ArrayType === Uint32Array) {
    return new THREE.Uint32BufferAttribute(typedIndices, 1);
  }
  return new THREE.Uint16BufferAttribute(typedIndices, 1);
}

function createFloatAttribute(values, itemSize) {
  const typedValues = values instanceof Float32Array ? values : new Float32Array(values);
  return new THREE.BufferAttribute(typedValues, itemSize);
}

function updateCameraToggleLabel(label) {
  const toggle = document.getElementById('camera-toggle');
  if (toggle) {
    toggle.innerText = label;
  }
}
