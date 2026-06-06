import { DEFAULT_BACKEND_URL } from '../config/backendUrl.js';
import { isDevRuntime } from '../config/runtimeMode.js';
import { ensureDatedSolveLabel } from '../modules/simulation/naming.js';
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

function createBrowserExportWriter(baseName) {
  const workspaceSubdir = ensureDatedSolveLabel(baseName, new Date());
  return async function writeBrowserExportFile(file) {
    await saveFile(file.content, file.fileName, {
      ...file.saveOptions,
      workspaceSubdir,
    });
    return file.fileName;
  };
}

function readExportBaseName() {
  return getExportBaseName();
}

export async function exportStlFromApp() {
  const { exportSTL } = await loadExportUseCases();
  const baseName = readExportBaseName();
  return exportSTL({
    state: readExportState(),
    baseName,
    writeFile: createBrowserExportWriter(baseName),
  });
}

export async function exportStepFromApp() {
  const { exportSTEP } = await loadExportUseCases();
  const baseName = readExportBaseName();
  return exportSTEP({
    state: readExportState(),
    baseName,
    backendUrl: DEFAULT_BACKEND_URL,
    writeFile: createBrowserExportWriter(baseName),
  });
}

export async function exportMwgConfigFromApp() {
  const { exportMWGConfig } = await loadExportUseCases();
  const baseName = readExportBaseName();
  return exportMWGConfig({
    state: readExportState(),
    baseName,
    writeFile: createBrowserExportWriter(baseName),
  });
}

export async function exportProfileCsvFromApp(vertices) {
  const { exportProfileCSV } = await loadExportUseCases();
  const baseName = readExportBaseName();
  return exportProfileCSV(vertices, {
    state: readExportState(),
    baseName,
    writeFile: createBrowserExportWriter(baseName),
    onMissingMesh: showError,
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
    console.log('Test 3: HornLab meshing endpoint');
    console.log('  URL:', `${backendUrl}/api/mesh/build`);
    try {
      const testPayload = {
        formula_type: 'OSSE',
        L: '120',
        s: '0.58',
        n: 4.158,
        h: 0,
        a: '25',
        a0: 15.5,
        r0: 12.7,
        k: 2,
        q: 3.4,
        gcurve_type: 0,
        gcurve_dist: 0.5,
        source_shape: 2,
        enc_depth: 0,
        wall_thickness: 6,
        n_angular: 24,
        n_length: 10,
        throat_res: 12,
        mouth_res: 20,
        rear_res: 40,
        msh_version: '2.2',
      };
      const start = performance.now();
      const res = await fetch(`${backendUrl}/api/mesh/build`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(testPayload),
      });
      const elapsed = (performance.now() - start).toFixed(0);
      console.log(`  Response: HTTP ${res.status} (${elapsed}ms)`);

      if (!res.ok) {
        console.error('  Error:', (await res.json()).detail || res.statusText);
        return;
      }

      const data = await res.json();
      console.log('  Success: HornLab mesher works.');
      console.log('  Stats:', data.stats);
    } catch (error) {
      console.error('  Failed:', error.name, '-', error.message);
    }

    console.log('');
    console.log('Diagnostic test complete.');
  };

  console.log('Backend diagnostic tool loaded. Run: window.testBackendConnection()');
}
