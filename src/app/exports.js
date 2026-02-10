import * as THREE from '../../node_modules/three/build/three.module.js';
import { STLExporter } from '../../node_modules/three/examples/jsm/exporters/STLExporter.js';
import {
  buildGmshGeo,
  exportProfilesCSV,
  exportFullGeo,
  generateMWGConfigContent,
  generateAbecProjectFile,
  generateAbecSolvingFile,
  generateAbecObservationFile,
  generateAbecCoordsFile,
  generateAbecStaticFile
} from '../export/index.js';
import { buildGeometryArtifacts } from '../geometry/index.js';
import { generateMeshFromGeo } from '../solver/client.js';
import { saveFile, getExportBaseName } from '../ui/fileOps.js';
import { showError } from '../ui/feedback.js';
import { GlobalState } from '../state.js';

function getJSZipCtor() {
  const JSZipCtor = globalThis.JSZip;
  if (!JSZipCtor) {
    throw new Error('JSZip failed to load. Reload the page and try again.');
  }
  return JSZipCtor;
}

function getPolarSettings() {
  const aStart = parseFloat(document.getElementById('polar-angle-start')?.value) || 0;
  const aEnd = parseFloat(document.getElementById('polar-angle-end')?.value) || 180;
  const aStep = parseFloat(document.getElementById('polar-angle-step')?.value) || 5;
  const aCount = Math.max(2, Math.floor((aEnd - aStart) / aStep) + 1);
  const polarRange = `${aStart},${aEnd},${aCount}`;
  const polarDistance = Number(document.getElementById('polar-distance')?.value || 2);
  const polarNormAngle = Number(document.getElementById('polar-norm-angle')?.value || 5);
  const polarInclination = Number(document.getElementById('polar-inclination')?.value || 0);
  return {
    polarRange,
    distance: Number.isFinite(polarDistance) ? polarDistance : 2,
    normAngle: Number.isFinite(polarNormAngle) ? polarNormAngle : 5,
    inclination: Number.isFinite(polarInclination) ? polarInclination : 0
  };
}

function getBackendUrl(app) {
  return app?.simulationPanel?.solver?.backendUrl || 'http://localhost:8000';
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
  const scale = GMSH_EXPORT_DEFAULTS.resolutionScale;

  return {
    ...preparedParams,
    angularSegments: coarseAngular,
    lengthSegments: coarseLength,
    throatResolution: toPositiveNumber(preparedParams.throatResolution, 5) * scale,
    mouthResolution: toPositiveNumber(preparedParams.mouthResolution, 8) * scale,
    rearResolution: toPositiveNumber(preparedParams.rearResolution, 10) * scale,
    encFrontResolution: scaleResolutionValue(preparedParams.encFrontResolution, scale),
    encBackResolution: scaleResolutionValue(preparedParams.encBackResolution, scale),
    wallThickness: hasEnclosure
      ? preparedParams.wallThickness
      : toPositiveNumber(preparedParams.wallThickness, 5)
  };
}

/**
 * Extract an ATH expression string from a value that may be a compiled
 * parseExpression() function, a plain number, or a raw string.
 * Returns undefined if value is null/undefined.
 */
function toExprString(value) {
  if (value == null) return undefined;
  if (typeof value === 'function') {
    return value._rawExpr != null ? String(value._rawExpr) : undefined;
  }
  return String(value);
}

/**
 * Convert prepared JS params to the WaveguideParamsRequest shape expected
 * by the Python OCC builder endpoint (POST /api/mesh/build).
 * Valid for both R-OSSE and OSSE configs.
 */
function buildPythonBuilderPayload(preparedParams, mshVersion = '2.2') {
  const type = preparedParams.type || 'R-OSSE';
  return {
    formula_type: type,

    // ── R-OSSE formula ────────────────────────────────────────────────────
    R: toExprString(preparedParams.R),
    r: Number(preparedParams.r ?? 0.4),
    b: Number(preparedParams.b ?? 0.2),
    m: Number(preparedParams.m ?? 0.85),
    tmax: Number(preparedParams.tmax ?? 1.0),

    // ── OSSE formula ──────────────────────────────────────────────────────
    L: toExprString(preparedParams.L),
    s: toExprString(preparedParams.s),
    n: Number(preparedParams.n ?? 4.158),
    h: Number(preparedParams.h ?? 0.0),

    // ── Shared formula ────────────────────────────────────────────────────
    a: toExprString(preparedParams.a),
    r0: Number(preparedParams.r0),
    a0: Number(preparedParams.a0),
    k: Number(preparedParams.k),
    q: Number(preparedParams.q ?? 3.4),

    // ── Throat geometry ───────────────────────────────────────────────────
    throat_profile: Number(preparedParams.throatProfile ?? 1),
    throat_ext_angle: Number(preparedParams.throatExtAngle ?? 0),
    throat_ext_length: Number(preparedParams.throatExtLength ?? 0),
    slot_length: Number(preparedParams.slotLength ?? 0),
    rot: Number(preparedParams.rot ?? 0),

    // ── Circular arc ──────────────────────────────────────────────────────
    circ_arc_term_angle: Number(preparedParams.circArcTermAngle ?? 1),
    circ_arc_radius: Number(preparedParams.circArcRadius ?? 0),

    // ── Guiding curve ─────────────────────────────────────────────────────
    gcurve_type: Number(preparedParams.gcurveType ?? 0),
    gcurve_dist: Number(preparedParams.gcurveDist ?? 0.5),
    gcurve_width: Number(preparedParams.gcurveWidth ?? 0),
    gcurve_aspect_ratio: Number(preparedParams.gcurveAspectRatio ?? 1),
    gcurve_se_n: Number(preparedParams.gcurveSeN ?? 3),
    gcurve_sf: preparedParams.gcurveSf != null ? String(preparedParams.gcurveSf) : undefined,
    gcurve_sf_a: preparedParams.gcurveSfA != null ? String(preparedParams.gcurveSfA) : undefined,
    gcurve_sf_b: preparedParams.gcurveSfB != null ? String(preparedParams.gcurveSfB) : undefined,
    gcurve_sf_m1: preparedParams.gcurveSfM1 != null ? String(preparedParams.gcurveSfM1) : undefined,
    gcurve_sf_m2: preparedParams.gcurveSfM2 != null ? String(preparedParams.gcurveSfM2) : undefined,
    gcurve_sf_n1: preparedParams.gcurveSfN1 != null ? String(preparedParams.gcurveSfN1) : undefined,
    gcurve_sf_n2: preparedParams.gcurveSfN2 != null ? String(preparedParams.gcurveSfN2) : undefined,
    gcurve_sf_n3: preparedParams.gcurveSfN3 != null ? String(preparedParams.gcurveSfN3) : undefined,
    gcurve_rot: Number(preparedParams.gcurveRot ?? 0),

    // ── Morph ─────────────────────────────────────────────────────────────
    morph_target: Number(preparedParams.morphTarget ?? 0),
    morph_width: Number(preparedParams.morphWidth ?? 0),
    morph_height: Number(preparedParams.morphHeight ?? 0),
    morph_corner: Number(preparedParams.morphCorner ?? 0),
    morph_rate: Number(preparedParams.morphRate ?? 3.0),
    morph_fixed: Number(preparedParams.morphFixed ?? 0),
    morph_allow_shrinkage: Number(preparedParams.morphAllowShrinkage ?? 0),

    // ── Geometry grid ─────────────────────────────────────────────────────
    n_angular: Math.max(20, Math.round(Number(preparedParams.angularSegments) || 100) / 4 * 4),
    n_length: Math.max(10, Math.round(Number(preparedParams.lengthSegments) || 20)),
    quadrants: Number(preparedParams.quadrants || 1234),

    // ── BEM mesh element sizes ────────────────────────────────────────────
    throat_res: Number(preparedParams.throatResolution) || 5.0,
    mouth_res: Number(preparedParams.mouthResolution) || 8.0,
    rear_res: Number(preparedParams.rearResolution) || 25.0,
    wall_thickness: Number(preparedParams.wallThickness) || 6.0,

    // ── Subdomain interfaces (passed through, no effect) ──────────────────
    subdomain_slices: preparedParams.subdomainSlices != null ? String(preparedParams.subdomainSlices) : undefined,
    interface_offset: preparedParams.interfaceOffset != null ? String(preparedParams.interfaceOffset) : undefined,
    interface_draw: preparedParams.interfaceDraw != null ? String(preparedParams.interfaceDraw) : undefined,
    interface_resolution: preparedParams.interfaceResolution != null ? String(preparedParams.interfaceResolution) : undefined,

    // ── Simulation / output ───────────────────────────────────────────────
    sim_type: Number(preparedParams.abecSimType || 2),
    msh_version: mshVersion
  };
}

/**
 * Build .geo and .msh using the Python OCC builder (POST /api/mesh/build).
 * Supports R-OSSE and OSSE configs when the backend Gmsh Python API is installed.
 *
 * Falls back to buildExportMeshWithGmsh if the endpoint returns 503.
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
  const payload = buildPythonBuilderPayload(preparedParams, mshVersion);

  let response;
  try {
    const res = await fetch(`${backendUrl}/api/mesh/build`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (res.status === 503) {
      // Python OCC builder not available — fall back to JS .geo path
      console.warn('[Export] Python OCC builder unavailable (503), falling back to JS .geo path');
      if (app?.stats) app.stats.innerText = 'Falling back to JS mesh path\u2026';
      return buildExportMeshWithGmsh(app, preparedParams, options);
    }

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

  // Build geometry artifacts for the ABEC bundle (coords, static, solving params)
  const gmshParams = buildGmshExportParams(preparedParams);
  const artifacts = buildGeometryArtifacts(gmshParams, {
    includeEnclosure: Number(gmshParams.encDepth || 0) > 0
  });

  return {
    artifacts,
    payload: artifacts.simulation,
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

export async function buildExportMeshWithGmsh(app, preparedParams, options = {}) {
  const backendUrl = getBackendUrl(app);

  if (app?.stats) app.stats.innerText = 'Connecting to Gmsh backend\u2026';
  
  console.log(`[Export] Attempting backend connection (try 1/2)...`);
  let reachable = await checkBackendReachable(backendUrl);
  
  if (!reachable) {
    // Retry once after brief delay
    console.log(`[Export] First attempt failed, retrying after 500ms...`);
    await new Promise(resolve => setTimeout(resolve, 500));
    reachable = await checkBackendReachable(backendUrl);
  }
  
  if (!reachable) {
    const errorMsg = 
      `Gmsh backend health check failed at ${backendUrl}.\n\n` +
      `🔍 Check browser console (F12 → Console tab) for detailed error.\n\n` +
      `Possible causes:\n` +
      `• Backend not running → Start with: npm start\n` +
      `• Port conflict → Check if port 8000 is available\n` +
      `• Network/CORS issue → See console for details`;
    
    console.error(`[Export] ❌ EXPORT FAILED - Backend unreachable after 2 attempts`);
    throw new Error(errorMsg);
  }
  
  console.log(`[Export] ✅ Backend connection verified`);

  if (app?.stats) app.stats.innerText = 'Preparing geometry\u2026';
  const gmshParams = buildGmshExportParams(preparedParams);
  const artifacts = buildGeometryArtifacts(gmshParams, {
    includeEnclosure: Number(gmshParams.encDepth || 0) > 0
  });
  const payload = artifacts.simulation;
  const { geoText } = buildGmshGeo(gmshParams, artifacts.mesh, payload, {
    mshVersion: options.mshVersion || '2.2'
  });

  if (app?.stats) app.stats.innerText = 'Meshing with Gmsh\u2026';
  const meshResponse = await generateMeshFromGeo(
    {
      geoText,
      mshVersion: options.mshVersion || '2.2',
      binary: Boolean(options.binary)
    },
    backendUrl
  );

  if (!meshResponse || meshResponse.generatedBy !== 'gmsh' || typeof meshResponse.msh !== 'string') {
    throw new Error('Invalid mesh service response: gmsh-authored mesh data is missing.');
  }

  return {
    artifacts,
    payload,
    msh: meshResponse.msh,
    meshStats: meshResponse.stats || null
  };
}

function getAxialMax(vertices) {
  let maxY = -Infinity;
  for (let i = 1; i < vertices.length; i += 3) {
    if (vertices[i] > maxY) maxY = vertices[i];
  }
  return Number.isFinite(maxY) ? maxY : 0;
}

export function exportSTL(app) {
  const preparedParams = app.prepareParamsForMesh({
    forceFullQuadrants: true,
    applyVerticalOffset: false
  });
  const artifacts = buildGeometryArtifacts(preparedParams, {
    includeEnclosure: false
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

  const baseName = getExportBaseName();
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
  const csv = exportProfilesCSV(vertices, state.params);

  const baseName = getExportBaseName();
  saveFile(csv, `${baseName}.csv`, {
    contentType: 'text/csv',
    typeInfo: { description: 'Profile Coordinates', accept: { 'text/csv': ['.csv'] } }
  });
}

export async function exportABECProject(app) {
  const preparedParams = app.prepareParamsForMesh({
    forceFullQuadrants: false,
    applyVerticalOffset: true
  });

  const baseName = getExportBaseName();
  const meshFileName = `${baseName}.msh`;
  const folderName = Number(preparedParams.abecSimType || 2) === 1
    ? 'ABEC_InfiniteBaffle'
    : 'ABEC_FreeStanding';

  const polar = getPolarSettings();
  const projectContent = generateAbecProjectFile({
    solvingFileName: 'solving.txt',
    observationFileName: 'observation.txt',
    meshFileName
  });
  app.stats.innerText = 'Building ABEC bundle...';

  try {
    // Use Python OCC builder for R-OSSE and OSSE configs (preferred — smooth OCC geometry).
    // Falls back automatically to JS .geo path if builder is unavailable (503).
    const buildFn = (preparedParams.type === 'R-OSSE' || preparedParams.type === 'OSSE')
      ? buildExportMeshFromParams
      : buildExportMeshWithGmsh;
    const { artifacts, payload, msh } = await buildFn(app, preparedParams);
    const hornGeometry = artifacts.mesh;
    const solvingContent = generateAbecSolvingFile(preparedParams, {
      interfaceEnabled: Boolean(payload.metadata?.interfaceEnabled),
      infiniteBaffleOffset: getAxialMax(hornGeometry.vertices)
    });
    const observationContent = generateAbecObservationFile({
      angleRange: polar.polarRange,
      distance: polar.distance,
      normAngle: polar.normAngle,
      inclination: polar.inclination,
      polarBlocks: preparedParams._blocks,
      allowDefaultPolars: !(preparedParams._blocks && Number(preparedParams.abecSimType || 2) === 1)
    });
    const coordsContent = generateAbecCoordsFile(hornGeometry.vertices, hornGeometry.ringCount);
    const staticContent = generateAbecStaticFile(payload.vertices);

    const JSZipCtor = getJSZipCtor();
    const zip = new JSZipCtor();
    const root = zip.folder(folderName);
    root.file('Project.abec', projectContent);
    root.file('solving.txt', solvingContent);
    root.file('observation.txt', observationContent);
    root.file(meshFileName, msh);
    const resultsFolder = root.folder('Results');
    resultsFolder.file('coords.txt', coordsContent);
    resultsFolder.file('static.txt', staticContent);

    const zipBlob = await zip.generateAsync({ type: 'blob' });
    const zipName = `${baseName}_${folderName}.zip`;
    await saveFile(zipBlob, zipName, {
      contentType: 'application/zip',
      typeInfo: { description: 'ABEC Project Zip', accept: { 'application/zip': ['.zip'] } }
    });
    app.stats.innerText = 'ABEC project exported';
  } catch (err) {
    console.error('[exports] ABEC export failed:', err);
    app.stats.innerText = `ABEC export failed: ${err.message}`;
    showError(`ABEC export failed: ${err.message}. Gmsh backend meshing is required for ABEC mesh export.`);
  }
}

// Manual backend diagnostics tool (available in browser console)
if (typeof window !== 'undefined') {
  window.testBackendConnection = async function() {
    console.log('╔════════════════════════════════════════════════════════╗');
    console.log('║     Backend Connection Diagnostic Test                ║');
    console.log('╚════════════════════════════════════════════════════════╝\n');
    
    const backendUrl = 'http://localhost:8000';
    
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
    
    // Test 3: Gmsh meshing endpoint with minimal geo
    console.log('\n📡 Test 3: Gmsh meshing endpoint (minimal geometry)');
    console.log('   URL:', `${backendUrl}/api/mesh/generate-msh`);
    try {
      const testGeo = 'Point(1) = {0, 0, 0, 1.0};\n';
      const start = performance.now();
      const res = await fetch(`${backendUrl}/api/mesh/generate-msh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          geoText: testGeo, 
          mshVersion: '2.2', 
          binary: false 
        })
      });
      const elapsed = (performance.now() - start).toFixed(0);
      console.log(`   Response: HTTP ${res.status} (${elapsed}ms)`);
      
      if (!res.ok) {
        const err = await res.json();
        console.error('   ❌ Error:', err.detail || err);
      } else {
        const data = await res.json();
        console.log('   ✅ Success! Gmsh meshing works.');
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
