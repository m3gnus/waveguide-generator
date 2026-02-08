import * as THREE from 'three';
import JSZip from 'jszip';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';
import {
  exportMSH as exportMSHContent,
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
import { saveFile, getExportBaseName } from '../ui/fileOps.js';
import { GlobalState } from '../state.js';

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

function buildExportMesh(preparedParams) {
  const artifacts = buildGeometryArtifacts(preparedParams, {
    includeEnclosure: Number(preparedParams.encDepth || 0) > 0
  });
  const payload = artifacts.simulation;
  const msh = exportMSHContent(payload.vertices, payload.indices, payload.surfaceTags, {
    verticalOffset: payload.metadata?.verticalOffset || 0
  });
  return { artifacts, payload, msh };
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
    includeEnclosure: false,
    includeRearShape: false
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
    alert('Please generate a horn model first');
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
    includeEnclosure: false,
    includeRearShape: false
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
    const { msh } = buildExportMesh(preparedParams);
    await saveFile(msh, 'mesh.msh', {
      contentType: 'text/plain',
      typeInfo: { description: 'Gmsh Mesh', accept: { 'text/plain': ['.msh'] } }
    });
    app.stats.innerText = 'MSH export complete';
  } catch (err) {
    console.error('[exports] MSH export failed:', err);
    app.stats.innerText = `MSH error: ${err.message}`;
    alert(`MSH export failed: ${err.message}`);
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
    const { artifacts, payload, msh } = buildExportMesh(preparedParams);
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
    const bemGeo = exportFullGeo(hornGeometry.vertices, preparedParams, {
      outputName: baseName,
      useSplines: true,
      ringCount: hornGeometry.ringCount,
      fullCircle: hornGeometry.fullCircle
    });

    const zip = new JSZip();
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
    alert(`ABEC export failed: ${err.message}`);
  }
}
