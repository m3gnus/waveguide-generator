import { DEFAULT_BACKEND_URL } from '../config/backendUrl.js';
import { isDevRuntime } from '../config/runtimeMode.js';
import { GlobalState } from '../state.js';
import { showError } from '../ui/feedback.js';
import { getExportBaseName, saveFile } from '../ui/fileOps.js';

let exportUseCasesPromise = null;

function loadExportUseCases() {
  if (!exportUseCasesPromise) {
    exportUseCasesPromise = import('../modules/export/useCases.js');
  }
  return exportUseCasesPromise;
}

function readExportState() {
  return GlobalState.get();
}

async function writeBrowserExportFile(file) {
  await saveFile(file.content, file.fileName, file.saveOptions);
  return file.fileName;
}

export async function exportStlFromApp() {
  const { exportSTL } = await loadExportUseCases();
  return exportSTL({
    state: readExportState(),
    baseName: getExportBaseName(),
    writeFile: writeBrowserExportFile
  });
}

export async function exportMwgConfigFromApp() {
  const { exportMWGConfig } = await loadExportUseCases();
  return exportMWGConfig({
    state: readExportState(),
    baseName: getExportBaseName(),
    writeFile: writeBrowserExportFile
  });
}

export async function exportProfileCsvFromApp(vertices) {
  const { exportProfileCSV } = await loadExportUseCases();
  return exportProfileCSV(vertices, {
    state: readExportState(),
    baseName: getExportBaseName(),
    writeFile: writeBrowserExportFile,
    onMissingMesh: showError
  });
}

export function registerBackendDiagnosticTool(targetWindow = window) {
  if (typeof targetWindow === 'undefined' || !isDevRuntime()) {
    return;
  }

  targetWindow.testBackendConnection = async function testBackendConnection() {
    const backendUrl = DEFAULT_BACKEND_URL;
    console.log('Backend connection diagnostic test');
    console.log('');

    console.log('Test 1: Health endpoint check');
    console.log('  URL:', `${backendUrl}/health`);
    try {
      const start = performance.now();
      const res = await fetch(`${backendUrl}/health`);
      const elapsed = (performance.now() - start).toFixed(0);
      console.log(`  OK: HTTP ${res.status} (${elapsed}ms)`);
      console.log('  Data:', await res.json());
    } catch (error) {
      console.error('  Failed:', error.name, '-', error.message);
    }

    console.log('');
    console.log('Test 2: Root endpoint check');
    console.log('  URL:', `${backendUrl}/`);
    try {
      const start = performance.now();
      const res = await fetch(`${backendUrl}/`);
      const elapsed = (performance.now() - start).toFixed(0);
      console.log(`  OK: HTTP ${res.status} (${elapsed}ms)`);
      console.log('  Data:', await res.json());
    } catch (error) {
      console.error('  Failed:', error.name, '-', error.message);
    }

    console.log('');
    console.log('Test 3: Gmsh meshing endpoint (OCC builder)');
    console.log('  URL:', `${backendUrl}/api/mesh/build`);
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
      console.log(`  Response: HTTP ${res.status} (${elapsed}ms)`);

      if (!res.ok) {
        console.error('  Error:', (await res.json()).detail || res.statusText);
        return;
      }

      const data = await res.json();
      console.log('  Success: Python OCC builder works.');
      console.log('  Stats:', data.stats);
    } catch (error) {
      console.error('  Failed:', error.name, '-', error.message);
    }

    console.log('');
    console.log('Diagnostic test complete.');
  };

  console.log('Backend diagnostic tool loaded. Run: window.testBackendConnection()');
}
