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
  generateAbecStaticFile,
  generateBemppStarterScript
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
  segmentDivisor: 2,
  resolutionScale: 2,
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

export async function buildExportMeshWithGmsh(app, preparedParams, options = {}) {
  const gmshParams = buildGmshExportParams(preparedParams);
  const artifacts = buildGeometryArtifacts(gmshParams, {
    includeEnclosure: Number(gmshParams.encDepth || 0) > 0
  });
  const payload = artifacts.simulation;
  const { geoText, geoStats } = buildGmshGeo(gmshParams, artifacts.mesh, payload, {
    mshVersion: options.mshVersion || '2.2'
  });

  const meshResponse = await generateMeshFromGeo(
    {
      geoText,
      mshVersion: options.mshVersion || '2.2',
      binary: Boolean(options.binary)
    },
    getBackendUrl(app)
  );

  if (!meshResponse || meshResponse.generatedBy !== 'gmsh' || typeof meshResponse.msh !== 'string') {
    throw new Error('Invalid mesh service response: gmsh-authored mesh data is missing.');
  }

  return {
    artifacts,
    payload,
    msh: meshResponse.msh,
    bemGeo: geoText,
    geoStats,
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

  saveFile(result, 'horn.stl', {
    contentType: 'application/sla',
    typeInfo: { description: 'STL Model', accept: { 'model/stl': ['.stl'] } }
  });
}

export function exportMWGConfig() {
  const state = GlobalState.get();
  const exportParams = { type: state.type, ...state.params };
  const content = generateMWGConfigContent(exportParams);
  saveFile(content, 'config.txt', {
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

  saveFile(csv, 'profiles.csv', {
    contentType: 'text/csv',
    typeInfo: { description: 'Profile Coordinates', accept: { 'text/csv': ['.csv'] } }
  });
}

export async function exportGmshGeo(app) {
  const preparedParams = app.prepareParamsForMesh({
    forceFullQuadrants: true,
    applyVerticalOffset: true
  });
  const baseName = getExportBaseName();
  const meshBase = `${baseName}`;
  const artifacts = buildGeometryArtifacts(preparedParams, {
    includeEnclosure: false
  });
  const geo = exportFullGeo(artifacts.mesh.vertices, preparedParams, {
    outputName: meshBase,
    useSplines: true,
    ringCount: artifacts.mesh.ringCount,
    fullCircle: artifacts.mesh.fullCircle
  });
  const starterScript = generateBemppStarterScript({
    meshFileName: `${meshBase}.msh`,
    sourceTag: 2
  });

  await saveFile(geo, `${meshBase}.geo`, {
    incrementCounter: false,
    contentType: 'text/plain',
    typeInfo: { description: 'Gmsh Geometry', accept: { 'text/plain': ['.geo'] } }
  });
  await saveFile(starterScript, `${meshBase}_bempp.py`, {
    contentType: 'text/x-python',
    typeInfo: { description: 'BEMPP Python', accept: { 'text/x-python': ['.py'] } }
  });
}

export async function exportMSH(app) {
  const preparedParams = app.prepareParamsForMesh({
    forceFullQuadrants: false,
    applyVerticalOffset: true
  });
  app.stats.innerText = 'Building mesh for MSH export...';
  try {
    const { msh } = await buildExportMeshWithGmsh(app, preparedParams);
    await saveFile(msh, 'mesh.msh', {
      contentType: 'text/plain',
      typeInfo: { description: 'Gmsh Mesh', accept: { 'text/plain': ['.msh'] } }
    });
    app.stats.innerText = 'MSH export complete';
  } catch (err) {
    console.error('[exports] MSH export failed:', err);
    app.stats.innerText = `MSH error: ${err.message}`;
    showError(`MSH export failed: ${err.message}. Gmsh backend meshing is required for .msh export.`);
  }
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
    const { artifacts, payload, msh, bemGeo } = await buildExportMeshWithGmsh(app, preparedParams);
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
    root.file('bem_mesh.geo', bemGeo);
    root.file(`${baseName}_bempp.py`, generateBemppStarterScript({ meshFileName, sourceTag: 2 }));
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
