import * as THREE from 'three';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';
import {
  exportHornToMSHWithBoundaries,
  exportProfilesCSV,
  exportGmshGeo as exportGmshGeoContent,
  generateMWGConfigContent,
  generateAbecProjectFile,
  generateAbecSolvingFile,
  generateAbecObservationFile
} from '../export/index.js';
import { saveFile, getExportBaseName } from '../ui/fileOps.js';
import { GlobalState } from '../state.js';

export function exportSTL(app) {
  if (!app.hornMesh) return;
  const exporter = new STLExporter();
  const exportMesh = app.hornMesh.clone();
  exportMesh.geometry = app.hornMesh.geometry.clone();
  exportMesh.geometry.applyMatrix4(new THREE.Matrix4().makeRotationX(Math.PI / 2));
  exportMesh.updateMatrixWorld(true);
  const result = exporter.parse(exportMesh, { binary: true });

  saveFile(result, 'horn.stl', {
    extension: '.stl',
    contentType: 'application/sla',
    typeInfo: { description: 'STL Model', accept: { 'model/stl': ['.stl'] } }
  });
}

export function exportMWGConfig() {
  const state = GlobalState.get();
  const exportParams = { type: state.type, ...state.params };
  const content = generateMWGConfigContent(exportParams);
  saveFile(content, 'config.txt', {
    extension: '.txt',
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
    extension: '.csv',
    contentType: 'text/csv',
    typeInfo: { description: 'Profile Coordinates', accept: { 'text/csv': ['.csv'] } }
  });
}

export function exportGmshGeo(app) {
  if (!app.hornMesh) {
    alert('Please generate a horn model first');
    return;
  }

  const vertices = app.hornMesh.geometry.attributes.position.array;
  const state = GlobalState.get();
  const geo = exportGmshGeoContent(vertices, state.params);

  saveFile(geo, 'mesh.geo', {
    extension: '.geo',
    contentType: 'text/plain',
    typeInfo: { description: 'Gmsh Geometry', accept: { 'text/plain': ['.geo'] } }
  });
}

/**
 * Export the horn mesh to Gmsh .msh format.
 * @param {Object} app
 */
export function exportMSH(app) {
  if (!app.hornMesh) {
    alert('Please generate a horn model first');
    return;
  }

  const geometry = app.hornMesh.geometry;
  const vertices = geometry.attributes.position.array;
  const indices = geometry.index?.array;
  if (!indices) {
    alert('Mesh indices are missing. Please re-render the model and try again.');
    return;
  }

  const state = GlobalState.get();
  const msh = exportHornToMSHWithBoundaries(vertices, indices, state.params);

  saveFile(msh, 'mesh.msh', {
    extension: '.msh',
    contentType: 'text/plain',
    typeInfo: { description: 'Gmsh Mesh', accept: { 'text/plain': ['.msh'] } }
  });
}

/**
 * Export an ABEC project bundle (project + solving + observation + mesh files).
 * @param {Object} app
 * @returns {Promise<void>}
 */
export async function exportABECProject(app) {
  if (!app.hornMesh) {
    alert('Please generate a horn model first');
    return;
  }

  const geometry = app.hornMesh.geometry;
  const vertices = geometry.attributes.position.array;
  const indices = geometry.index?.array;
  if (!indices) {
    alert('Mesh indices are missing. Please re-render the model and try again.');
    return;
  }

  const state = GlobalState.get();
  const baseName = getExportBaseName();
  const projectBase = `${baseName}_project`;
  const solvingBase = `${baseName}_solving`;
  const observationBase = `${baseName}_observation`;

  const meshFileName = `${baseName}.msh`;
  const solvingFileName = `${solvingBase}.txt`;
  const observationFileName = `${observationBase}.txt`;
  const projectFileName = `${projectBase}.abec`;

  const polarRange = document.getElementById('polar-angle-range')?.value || '0,180,37';
  const polarDistance = Number(document.getElementById('polar-distance')?.value || 2);
  const polarNormAngle = Number(document.getElementById('polar-norm-angle')?.value || 5);
  const polarInclination = Number(document.getElementById('polar-inclination')?.value || 0);

  const projectContent = generateAbecProjectFile({
    solvingFileName,
    observationFileName,
    meshFileName
  });
  const solvingContent = generateAbecSolvingFile(state.params);
  const observationContent = generateAbecObservationFile({
    angleRange: polarRange,
    distance: Number.isFinite(polarDistance) ? polarDistance : 2,
    normAngle: Number.isFinite(polarNormAngle) ? polarNormAngle : 5,
    inclination: Number.isFinite(polarInclination) ? polarInclination : 0
  });
  const meshContent = exportHornToMSHWithBoundaries(vertices, indices, state.params);

  await saveFile(projectContent, projectFileName, {
    baseName: projectBase,
    extension: '.abec',
    contentType: 'text/plain',
    typeInfo: { description: 'ABEC Project', accept: { 'text/plain': ['.abec'] } },
    incrementCounter: false
  });
  await saveFile(solvingContent, solvingFileName, {
    baseName: solvingBase,
    extension: '.txt',
    contentType: 'text/plain',
    typeInfo: { description: 'ABEC Solving', accept: { 'text/plain': ['.txt'] } },
    incrementCounter: false
  });
  await saveFile(observationContent, observationFileName, {
    baseName: observationBase,
    extension: '.txt',
    contentType: 'text/plain',
    typeInfo: { description: 'ABEC Observation', accept: { 'text/plain': ['.txt'] } },
    incrementCounter: false
  });
  await saveFile(meshContent, meshFileName, {
    baseName,
    extension: '.msh',
    contentType: 'text/plain',
    typeInfo: { description: 'Gmsh Mesh', accept: { 'text/plain': ['.msh'] } }
  });
}
