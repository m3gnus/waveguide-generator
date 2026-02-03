import * as THREE from 'three';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';
import { generateMWGConfigContent, exportProfilesCSV, exportGmshGeo } from '../export/index.js';
import { saveFile } from '../ui/fileOps.js';
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
  const geo = exportGmshGeo(vertices, state.params);

  saveFile(geo, 'mesh.geo', {
    extension: '.geo',
    contentType: 'text/plain',
    typeInfo: { description: 'Gmsh Geometry', accept: { 'text/plain': ['.geo'] } }
  });
}
