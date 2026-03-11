import { saveFile, getExportBaseName } from '../ui/fileOps.js';
import { showError } from '../ui/feedback.js';
import { GlobalState } from '../state.js';
import { DEFAULT_BACKEND_URL } from '../config/backendUrl.js';
import { isDevRuntime } from '../config/runtimeMode.js';
import { ExportModule } from '../modules/export/index.js';

function getBackendUrl(app) {
  return app?.simulationPanel?.solver?.backendUrl || DEFAULT_BACKEND_URL;
}

/**
 * Build an OCC-based .msh using the Python builder (POST /api/mesh/build).
 * Supports R-OSSE and OSSE configs when the backend Gmsh Python API is installed.
 */
export async function buildExportMeshFromParams(app, preparedParams, options = {}) {
  const exportTask = await ExportModule.task(
    ExportModule.importOccMeshBuild(preparedParams, {
      backendUrl: getBackendUrl(app),
      onStatus(message) {
        if (app?.stats) {
          app.stats.innerText = message.replace('...', '\u2026');
        }
      }
    }),
    options
  );

  return ExportModule.output.occMesh(exportTask);
}

export function exportSTL(app) {
  const baseName = getExportBaseName();
  const preparedParams = app.prepareParamsForMesh({
    applyVerticalOffset: false
  });
  const exportTask = ExportModule.task(ExportModule.importStl(preparedParams, { baseName }));
  for (const file of ExportModule.output.files(exportTask)) {
    saveFile(file.content, file.fileName, file.saveOptions);
  }
}

export function exportMWGConfig() {
  const state = GlobalState.get();
  const baseName = getExportBaseName();
  const exportTask = ExportModule.task(
    ExportModule.importConfig({
      params: { type: state.type, ...state.params },
      baseName
    })
  );
  for (const file of ExportModule.output.files(exportTask)) {
    saveFile(file.content, file.fileName, file.saveOptions);
  }
}

export function exportProfileCSV(app) {
  if (!app.hornMesh) {
    showError('Please generate a horn model first.');
    return;
  }

  const vertices = app.hornMesh.geometry.attributes.position.array;
  const state = GlobalState.get();
  const baseName = getExportBaseName();
  const exportTask = ExportModule.task(
    ExportModule.importProfileCsv({
      vertices,
      angularSegments: state.params.angularSegments,
      lengthSegments: state.params.lengthSegments,
      baseName
    })
  );
  for (const file of ExportModule.output.files(exportTask)) {
    saveFile(file.content, file.fileName, file.saveOptions);
  }
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
