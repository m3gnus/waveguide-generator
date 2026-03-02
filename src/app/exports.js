import * as THREE from 'three';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';
import {
  exportProfilesCSV,
  exportSlicesCSV,
  generateMWGConfigContent
} from '../export/index.js';
import { buildGeometryArtifacts } from '../geometry/index.js';
import { buildWaveguidePayload } from '../solver/waveguidePayload.js';
import { saveFile, getExportBaseName } from '../ui/fileOps.js';
import { showError } from '../ui/feedback.js';
import { GlobalState } from '../state.js';
import { DEFAULT_BACKEND_URL } from '../config/backendUrl.js';
import { isDevRuntime } from '../config/runtimeMode.js';

// Mirrors normalizeAngularSegments() in geometry/engine/mesh/angles.js —
// the mesh builder snaps angularSegments to a multiple of 8 when it isn't
// already a multiple of 4, so CSV export must use the same effective count.
function getMeshRingCount(rawAngularSegments) {
  const count = Math.max(4, Math.round(Number(rawAngularSegments) || 0));
  if (count % 4 === 0) return count;
  return Math.max(8, Math.ceil(count / 8) * 8);
}

function getBackendUrl(app) {
  return app?.simulationPanel?.solver?.backendUrl || DEFAULT_BACKEND_URL;
}

const GMSH_EXPORT_DEFAULTS = Object.freeze({
  segmentDivisor: 1,
  resolutionScale: 1,
  minAngularSegments: 20,
  minLengthSegments: 10
});

function toPositiveNumber(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function scaleResolutionValue(value, scale) {
  if (value === undefined || value === null || value === '') return value;

  if (typeof value === 'number') {
    return value > 0 ? value * scale : value;
  }

  const text = String(value).trim();
  if (!text) return value;
  const parts = text.split(',').map((part) => part.trim()).filter((part) => part.length > 0);
  if (parts.length === 0) return value;

  const nums = parts.map((part) => Number(part));
  if (nums.some((n) => !Number.isFinite(n))) return value;

  return nums.map((n) => (n > 0 ? n * scale : n)).join(',');
}

function normalizeAngularSegments(value, minSegments) {
  const rounded = Math.max(minSegments, Math.round(value));
  const snapped = Math.round(rounded / 4) * 4;
  return Math.max(4, snapped);
}

function buildGmshExportParams(preparedParams) {
  const hasEnclosure = Number(preparedParams.encDepth || 0) > 0;
  const baseAngular = toPositiveNumber(preparedParams.angularSegments, 120);
  const baseLength = toPositiveNumber(preparedParams.lengthSegments, 40);
  const coarseAngular = normalizeAngularSegments(
    baseAngular / GMSH_EXPORT_DEFAULTS.segmentDivisor,
    GMSH_EXPORT_DEFAULTS.minAngularSegments
  );
  const coarseLength = Math.max(
    GMSH_EXPORT_DEFAULTS.minLengthSegments,
    Math.round(baseLength / GMSH_EXPORT_DEFAULTS.segmentDivisor)
  );
  const scale = preparedParams.scale ?? GMSH_EXPORT_DEFAULTS.resolutionScale;

  return {
    ...preparedParams,
    angularSegments: coarseAngular,
    lengthSegments: coarseLength,
    throatResolution: toPositiveNumber(preparedParams.throatResolution, 6) * scale,
    mouthResolution: toPositiveNumber(preparedParams.mouthResolution, 15) * scale,
    rearResolution: toPositiveNumber(preparedParams.rearResolution, 40) * scale,
    encFrontResolution: scaleResolutionValue(preparedParams.encFrontResolution ?? '25,25,25,25', scale),
    encBackResolution: scaleResolutionValue(preparedParams.encBackResolution ?? '40,40,40,40', scale),
    wallThickness: hasEnclosure
      ? preparedParams.wallThickness
      : toPositiveNumber(preparedParams.wallThickness, 5)
  };
}

/**
 * Build an OCC-based .msh using the Python builder (POST /api/mesh/build).
 * Supports R-OSSE and OSSE configs when the backend Gmsh Python API is installed.
 */
export async function buildExportMeshFromParams(app, preparedParams, options = {}) {
  const backendUrl = getBackendUrl(app);

  if (app?.stats) app.stats.innerText = 'Connecting to backend\u2026';

  let reachable = await checkBackendReachable(backendUrl);
  if (!reachable) {
    await new Promise(resolve => setTimeout(resolve, 500));
    reachable = await checkBackendReachable(backendUrl);
  }
  if (!reachable) {
    throw new Error(
      `Backend health check failed at ${backendUrl}.\n` +
      `Start with: npm start`
    );
  }

  if (app?.stats) app.stats.innerText = 'Building mesh (Python OCC)\u2026';

  const mshVersion = options.mshVersion || '2.2';
  const requestPayload = buildWaveguidePayload(preparedParams, mshVersion);

  let response;
  try {
    const res = await fetch(`${backendUrl}/api/mesh/build`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload)
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(`/api/mesh/build failed: ${err.detail || res.statusText}`);
    }

    response = await res.json();
  } catch (err) {
    if (err.message?.includes('/api/mesh/build failed')) throw err;
    throw new Error(`/api/mesh/build request failed: ${err.message}`);
  }

  if (!response || response.generatedBy !== 'gmsh-occ' || typeof response.msh !== 'string') {
    throw new Error('Invalid response from /api/mesh/build: expected gmsh-occ mesh data.');
  }

  // Build geometry artifacts (coords, static, solving params)
  const gmshParams = buildGmshExportParams(preparedParams);
  const artifacts = buildGeometryArtifacts(gmshParams, {
    includeEnclosure: Number(gmshParams.encDepth || 0) > 0
  });
  const payload = artifacts.simulation;
  return {
    artifacts,
    payload,
    msh: response.msh,
    meshStats: response.stats || null
  };
}

async function checkBackendReachable(backendUrl) {
  console.log(`[Export] Checking backend health at ${backendUrl}/health...`);
  const controller = new AbortController();
  const timeoutMs = 10000; // Increased from 3s to 10s for slow backends
  const timer = setTimeout(() => {
    console.warn(`[Export] Health check timeout after ${timeoutMs}ms`);
    controller.abort();
  }, timeoutMs);

  const startTime = performance.now();

  try {
    const res = await fetch(`${backendUrl}/health`, { signal: controller.signal });
    const elapsed = (performance.now() - startTime).toFixed(0);
    clearTimeout(timer);

    console.log(`[Export] Health check response: HTTP ${res.status} ${res.statusText} (${elapsed}ms)`);

    if (!res.ok) {
      console.error(`[Export] Backend returned non-OK status: ${res.status}`);
      try {
        const body = await res.text();
        console.error(`[Export] Response body:`, body.substring(0, 200));
      } catch (e) {
        console.error(`[Export] Could not read response body:`, e);
      }
    }

    return res.ok;
  } catch (error) {
    const elapsed = (performance.now() - startTime).toFixed(0);
    clearTimeout(timer);

    console.error(`[Export] Health check FAILED after ${elapsed}ms:`, {
      name: error.name,
      message: error.message,
      stack: error.stack?.split('\n').slice(0, 3).join('\n')
    });

    // Provide specific error guidance
    if (error.name === 'AbortError') {
      console.error(`[Export] ❌ Request timed out - backend may be slow or unresponsive`);
    } else if (error.message.includes('Failed to fetch') || error.message.includes('fetch')) {
      console.error(`[Export] ❌ Network error - check if backend is running: npm start`);
    } else if (error.message.includes('NetworkError') || error.message.includes('CORS')) {
      console.error(`[Export] ❌ CORS or network policy error`);
    }

    return false;
  }
}

export function exportSTL(app) {
  const baseName = getExportBaseName();
  const preparedParams = app.prepareParamsForMesh({
    applyVerticalOffset: false
  });
  const artifacts = buildGeometryArtifacts(preparedParams, {
    includeEnclosure: false,
    adaptivePhi: true
  });
  const { vertices, indices } = artifacts.mesh;

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();

  const exporter = new STLExporter();
  const exportMesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  exportMesh.geometry.applyMatrix4(new THREE.Matrix4().makeRotationX(Math.PI / 2));
  exportMesh.updateMatrixWorld(true);
  const result = exporter.parse(exportMesh, { binary: true });

  saveFile(result, `${baseName}.stl`, {
    contentType: 'application/sla',
    typeInfo: { description: 'STL Model', accept: { 'model/stl': ['.stl'] } }
  });
}

export function exportMWGConfig() {
  const state = GlobalState.get();
  const exportParams = { type: state.type, ...state.params };
  const content = generateMWGConfigContent(exportParams);
  const baseName = getExportBaseName();
  saveFile(content, `${baseName}.txt`, {
    contentType: 'text/plain',
    typeInfo: { description: 'MWG Config', accept: { 'text/plain': ['.txt'] } }
  });
}

export function exportProfileCSV(app) {
  if (!app.hornMesh) {
    showError('Please generate a horn model first.');
    return;
  }

  const vertices = app.hornMesh.geometry.attributes.position.array;
  const state = GlobalState.get();
  const baseName = getExportBaseName();

  // The mesh builder normalizes angularSegments (snaps to nearest multiple of 8
  // when not already a multiple of 4), so we must use the same normalized value
  // as the stride when indexing into the vertex array.
  const ringCount = getMeshRingCount(state.params.angularSegments);
  const lengthSteps = Math.max(1, Math.round(Number(state.params.lengthSegments) || 40));
  const meshParams = { angularSegments: ringCount, lengthSegments: lengthSteps };

  const profilesCsv = exportProfilesCSV(vertices, meshParams);
  saveFile(profilesCsv, `${baseName}_profiles.csv`, {
    contentType: 'text/csv',
    typeInfo: { description: 'Angular Profiles', accept: { 'text/csv': ['.csv'] } }
  });

  const slicesCsv = exportSlicesCSV(vertices, meshParams);
  saveFile(slicesCsv, `${baseName}_slices.csv`, {
    contentType: 'text/csv',
    typeInfo: { description: 'Length Slices', accept: { 'text/csv': ['.csv'] } }
  });
}

// Manual backend diagnostics tool (available in browser console in local/dev runtime only)
if (typeof window !== 'undefined' && isDevRuntime()) {
  window.testBackendConnection = async function () {
    console.log('╔════════════════════════════════════════════════════════╗');
    console.log('║     Backend Connection Diagnostic Test                ║');
    console.log('╚════════════════════════════════════════════════════════╝\n');

    const backendUrl = DEFAULT_BACKEND_URL;

    // Test 1: Health endpoint
    console.log('📡 Test 1: Health endpoint check');
    console.log('   URL:', `${backendUrl}/health`);
    try {
      const start = performance.now();
      const res = await fetch(`${backendUrl}/health`);
      const elapsed = (performance.now() - start).toFixed(0);
      console.log(`   ✅ Response: HTTP ${res.status} (${elapsed}ms)`);
      const data = await res.json();
      console.log('   Data:', data);
    } catch (e) {
      console.error('   ❌ Failed:', e.name, '-', e.message);
    }

    console.log('\n📡 Test 2: Root endpoint check');
    console.log('   URL:', `${backendUrl}/`);
    try {
      const start = performance.now();
      const res = await fetch(`${backendUrl}/`);
      const elapsed = (performance.now() - start).toFixed(0);
      console.log(`   ✅ Response: HTTP ${res.status} (${elapsed}ms)`);
      const data = await res.json();
      console.log('   Data:', data);
    } catch (e) {
      console.error('   ❌ Failed:', e.name, '-', e.message);
    }

    // Test 3: Gmsh meshing endpoint (OCC builder)
    console.log('\n📡 Test 3: Gmsh meshing endpoint (OCC builder)');
    console.log('   URL:', `${backendUrl}/api/mesh/build`);
    try {
      const testPayload = {
        params: {
          type: 'R-OSSE',
          L: 50,
          throat: 25.4,
          mouth: 150,
          depth: 100,
          quadrants: '12',
          angularSegments: 40,
          lengthSegments: 20
        },
        mshVersion: '2.2'
      };
      const start = performance.now();
      const res = await fetch(`${backendUrl}/api/mesh/build`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(testPayload)
      });
      const elapsed = (performance.now() - start).toFixed(0);
      console.log(`   Response: HTTP ${res.status} (${elapsed}ms)`);

      if (!res.ok) {
        const err = await res.json();
        console.error('   ❌ Error:', err.detail || err);
      } else {
        const data = await res.json();
        console.log('   ✅ Success! Python OCC builder works.');
        console.log('   Stats:', data.stats);
      }
    } catch (e) {
      console.error('   ❌ Failed:', e.name, '-', e.message);
    }

    console.log('\n╔════════════════════════════════════════════════════════╗');
    console.log('║     Diagnostic Test Complete                           ║');
    console.log('╚════════════════════════════════════════════════════════╝');
  };

  console.log('💡 Backend diagnostic tool loaded.');
  console.log('   Run: window.testBackendConnection()');
}
