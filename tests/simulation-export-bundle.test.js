import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

import { exportResults } from '../src/ui/simulation/exports.js';
import { persistSimulationGenerationArtifacts } from '../src/ui/simulation/workspaceTasks.js';
import { GlobalState } from '../src/state.js';
import { getDefaults } from '../src/config/defaults.js';
import {
  buildMwgConfigExportFiles,
  buildStlExportFiles,
} from '../src/modules/export/useCases.js';

test('exportResults writes selected bundle files into the task folder workspace', async () => {
  const originalFetch = global.fetch;

  const fetchCalls = [];
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  const panel = {
    currentSmoothing: 'none',
    lastResults: {
      spl_on_axis: { frequencies: [100], spl: [90] },
      di: { di: [8] },
      impedance: { frequencies: [100], real: [6], imaginary: [1] },
      directivity: {},
    },
  };

  try {
    const bundle = await exportResults(panel, {
      job: {
        id: 'job-1',
        label: 'horn_12',
        createdAt: '2026-03-11T10:00:00.000Z',
      },
      selectedFormats: ['csv', 'json'],
    });

    assert.deepEqual(bundle.exportedFiles, [
      'csv:horn_12_results.csv',
      'json:horn_12_results.json',
    ]);
    assert.deepEqual(bundle.failures, []);

    // Both files should be written via fetch to the backend export endpoint
    assert.equal(fetchCalls.length, 2);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[1].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[0].options.body.get('workspace_subdir'), '260311_horn_12');
    assert.equal(fetchCalls[1].options.body.get('workspace_subdir'), '260311_horn_12');
  } finally {
    global.fetch = originalFetch;
  }
});

test('exportResults preserves zero-valued result cells in CSV bundle files', async () => {
  const originalFetch = global.fetch;

  const fetchCalls = [];
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  const panel = {
    currentSmoothing: 'none',
    lastResults: {
      spl_on_axis: { frequencies: [100, 200], spl: [0, 91] },
      di: { frequencies: [100, 200], di: [0, 8] },
      impedance: {
        frequencies: [100, 200],
        real: [0, 6],
        imaginary: [0, -1],
      },
      directivity: {},
    },
  };

  try {
    await exportResults(panel, {
      job: {
        id: 'job-zero',
        label: 'horn_zero',
        createdAt: '2026-03-11T10:00:00.000Z',
      },
      selectedFormats: ['csv'],
    });

    assert.equal(fetchCalls.length, 1);
    const csvFile = fetchCalls[0].options.body.get('file');
    const csv = await csvFile.text();
    assert.match(csv, /100,0,0,0,0\n200,91,8,6,-1\n$/);
  } finally {
    global.fetch = originalFetch;
  }
});

test('exportResults prepares smoothed result series once for a multi-format bundle', async () => {
  const originalFetch = global.fetch;
  let splReads = 0;

  const results = {
    get spl_on_axis() {
      splReads += 1;
      return {
        frequencies: [100, 200, 400],
        spl: [90, 92, 91],
        phase_degrees: [10, 20, 30],
      };
    },
    di: { frequencies: [100, 200, 400], di: [6, 8, 7] },
    impedance: {
      frequencies: [100, 200, 400],
      real: [1, 1.2, 1.1],
      imaginary: [0.1, 0.2, 0.15],
    },
    directivity: {},
  };

  global.fetch = async (url) => {
    if (String(url).endsWith('/api/render-charts')) {
      return {
        ok: true,
        async json() {
          return {
            charts: {
              frequency_response: 'data:image/png;base64,AA==',
            },
          };
        },
      };
    }

    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  try {
    const bundle = await exportResults(
      {
        currentSmoothing: '1/3',
        lastResults: results,
      },
      {
        job: {
          id: 'job-smoothed-bundle',
          label: 'horn_smoothed',
          createdAt: '2026-03-11T10:00:00.000Z',
        },
        selectedFormats: ['png', 'csv', 'txt'],
      }
    );

    assert.deepEqual(bundle.failures, []);
    assert.equal(splReads, 1);
  } finally {
    global.fetch = originalFetch;
  }
});

test('exportResults labels and normalizes legacy impedance values in text exports', async () => {
  const originalFetch = global.fetch;

  const fetchCalls = [];
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  const panel = {
    currentSmoothing: 'none',
    lastResults: {
      spl_on_axis: { frequencies: [100], spl: [90] },
      di: { frequencies: [100], di: [8] },
      impedance: {
        frequencies: [100],
        real: [415.03],
        imaginary: [0],
      },
      directivity: {},
    },
  };

  try {
    await exportResults(panel, {
      job: {
        id: 'job-legacy-impedance',
        label: 'horn_legacy',
        createdAt: '2026-03-11T10:00:00.000Z',
      },
      selectedFormats: ['csv', 'txt', 'impedance_csv', 'vacs'],
    });

    assert.equal(fetchCalls.length, 4);
    const filesByName = new Map();
    for (const call of fetchCalls) {
      const file = call.options.body.get('file');
      filesByName.set(file.name, await file.text());
    }

    const csv = filesByName.get('horn_legacy_results.csv');
    assert.ok(csv);
    const [csvHeader, csvRow] = csv.trim().split('\n');
    assert.equal(
      csvHeader,
      'Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))'
    );
    const csvCells = csvRow.split(',');
    assert.equal(csvCells[0], '100');
    assert.ok(Math.abs(Number(csvCells[3]) - 1.0) < 1e-9);
    assert.ok(Math.abs(Number(csvCells[4])) < 1e-9);
    assert.equal(csv.toLowerCase().includes('ohm'), false);
    assert.equal(csv.includes('\u03a9'), false);

    const txt = filesByName.get('horn_legacy_report.txt');
    assert.ok(txt);
    assert.match(txt, /Average Real Part Z\/\(rho\*c\): 1\.00/);
    assert.match(txt, /Z_Re\/\(rho\*c\)\s+Z_Im\/\(rho\*c\)/);
    assert.match(txt, /100\s+90\.00\s+8\.00\s+1\.00\s+0\.00/);
    assert.equal(txt.toLowerCase().includes('ohm'), false);
    assert.equal(txt.includes('\u03a9'), false);

    const impedanceCsv = filesByName.get('horn_legacy_impedance.csv');
    assert.ok(impedanceCsv);
    assert.equal(impedanceCsv.trim(), 'Freq_Hz,Z_Real_Z_over_rho_c,Z_Imag_Z_over_rho_c\n100,1,0');

    const vacs = filesByName.get('horn_legacy_spectrum.txt');
    assert.ok(vacs);
    assert.match(vacs, /Data_Legend="Radiation Impedance Z\/\(rho\*c\)"/);
    assert.match(vacs, /^100\s+1\s+0$/m);
  } finally {
    global.fetch = originalFetch;
  }
});

test('exportResults does not renormalize backend Z over rho c impedance metadata', async () => {
  const originalFetch = global.fetch;

  const fetchCalls = [];
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  const panel = {
    currentSmoothing: 'none',
    lastResults: {
      spl_on_axis: { frequencies: [100], spl: [90] },
      di: { frequencies: [100], di: [8] },
      impedance: {
        frequencies: [100],
        real: [32],
        imaginary: [-4],
      },
      directivity: {},
      metadata: {
        impedance_units: 'Z/(rho*c)',
        impedance_quantity: 'specific_acoustic_impedance',
      },
    },
  };

  try {
    await exportResults(panel, {
      job: {
        id: 'job-normalized-impedance',
        label: 'horn_normalized',
        createdAt: '2026-03-11T10:00:00.000Z',
      },
      selectedFormats: ['impedance_csv'],
    });

    assert.equal(fetchCalls.length, 1);
    const file = fetchCalls[0].options.body.get('file');
    const impedanceCsv = await file.text();
    assert.equal(impedanceCsv.trim(), 'Freq_Hz,Z_Real_Z_over_rho_c,Z_Imag_Z_over_rho_c\n100,32,-4');
  } finally {
    global.fetch = originalFetch;
  }
});

test('exportResults forwards Metal phase convention for rendered PNG charts', async () => {
  const originalFetch = global.fetch;

  const chartPayloads = [];
  global.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/api/render-charts')) {
      chartPayloads.push(JSON.parse(options.body));
      return {
        ok: true,
        async json() {
          return {
            charts: {
              frequency_response: 'data:image/png;base64,AA==',
            },
          };
        },
      };
    }

    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  const panel = {
    currentSmoothing: 'none',
    lastResults: {
      spl_on_axis: {
        frequencies: [100],
        spl: [90],
        phase_degrees: [15],
      },
      di: { frequencies: [100], di: [8] },
      impedance: { frequencies: [100], real: [1], imaginary: [0] },
      directivity: {},
      metadata: {
        solver_backend: 'metal',
        observation: {
          effective_distance_m: 2,
        },
      },
    },
  };

  try {
    const bundle = await exportResults(panel, {
      job: {
        id: 'job-metal-phase',
        label: 'horn_metal',
        createdAt: '2026-03-11T10:00:00.000Z',
      },
      selectedFormats: ['png'],
    });

    assert.deepEqual(bundle.failures, []);
    assert.equal(chartPayloads.length, 1);
    assert.equal(chartPayloads[0].phase_time_convention, 'metal');
    assert.equal(chartPayloads[0].phase_reference_distance_m, 2);
    assert.equal(chartPayloads[0].impedance_units, 'Z/(rho*c)');
    assert.equal(chartPayloads[0].impedance_normalization, 'rho_c');
  } finally {
    global.fetch = originalFetch;
  }
});

test('exportResults distinguishes restored Bempp metadata from legacy Bempp phase convention', async () => {
  const originalFetch = global.fetch;

  const chartPayloads = [];
  global.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/api/render-charts')) {
      chartPayloads.push(JSON.parse(options.body));
      return {
        ok: true,
        async json() {
          return {
            charts: {
              frequency_response: 'data:image/png;base64,AA==',
            },
          };
        },
      };
    }

    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  const baseResults = {
    spl_on_axis: {
      frequencies: [100],
      spl: [90],
      phase_degrees: [15],
    },
    di: { frequencies: [100], di: [8] },
    impedance: { frequencies: [100], real: [1], imaginary: [0] },
    directivity: {},
  };

  try {
    await exportResults(
      {
        currentSmoothing: 'none',
        lastResults: {
          ...baseResults,
          metadata: {
            solver_backend: 'bempp',
            engine: 'hornlab-bempp-bem',
            phase_time_convention: 'exp(+ikr)',
            device_interface: { selected: 'bempp-cl-opencl' },
          },
        },
      },
      {
        job: {
          id: 'job-new-bempp-phase',
          label: 'horn_new_bempp',
          createdAt: '2026-03-11T10:00:00.000Z',
        },
        selectedFormats: ['png'],
      }
    );

    await exportResults(
      {
        currentSmoothing: 'none',
        lastResults: {
          ...baseResults,
          metadata: {
            solver_backend: 'bempp',
          },
        },
      },
      {
        job: {
          id: 'job-legacy-bempp-phase',
          label: 'horn_legacy_bempp',
          createdAt: '2026-03-11T10:00:00.000Z',
        },
        selectedFormats: ['png'],
      }
    );

    assert.equal(chartPayloads.length, 2);
    assert.equal(chartPayloads[0].phase_time_convention, 'metal');
    assert.equal(chartPayloads[1].phase_time_convention, 'bempp');
  } finally {
    global.fetch = originalFetch;
  }
});

test('exportResults routes fallback bundle writes through backend workspace subdirectory', async () => {
  const originalFetch = global.fetch;

  const fetchCalls = [];
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  const panel = {
    currentSmoothing: 'none',
    lastResults: {
      spl_on_axis: { frequencies: [100], spl: [90] },
      di: { di: [8] },
      impedance: { frequencies: [100], real: [6], imaginary: [1] },
      directivity: {},
    },
  };

  try {
    const bundle = await exportResults(panel, {
      job: {
        id: 'job-3',
        label: 'horn_34',
        createdAt: '2026-03-11T10:00:00.000Z',
      },
      selectedFormats: ['csv'],
    });

    assert.deepEqual(bundle.exportedFiles, ['csv:horn_34_results.csv']);
    assert.equal(fetchCalls.length, 1);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');
    const body = fetchCalls[0].options.body;
    assert.equal(body.get('workspace_subdir'), '260311_horn_34');
  } finally {
    global.fetch = originalFetch;
  }
});

test('exportResults builds job geometry exports from the stored design snapshot, not the editor design', async () => {
  const originalFetch = global.fetch;
  const originalState = JSON.parse(JSON.stringify(GlobalState.get()));

  const baseDefaults = getDefaults('R-OSSE');
  const editorDesign = {
    type: 'R-OSSE',
    params: { ...baseDefaults, R: Number(baseDefaults.R) * 1.5 },
  };
  const jobDesign = {
    type: 'R-OSSE',
    params: { ...baseDefaults },
  };

  // Compare STL payloads by digest: byte-level assert.deepEqual on large
  // binary buffers takes minutes to render a diff when it fails.
  const stlDigest = (content) => createHash('sha256').update(Buffer.from(content)).digest('hex');

  // Fixture self-check: the two designs must actually produce different STL
  // bytes, otherwise the assertions below cannot detect a fallback to the
  // editor design.
  const [expectedJobStl] = buildStlExportFiles(jobDesign, { baseName: 'horn_snapshot' });
  const [editorStl] = buildStlExportFiles(editorDesign, { baseName: 'horn_snapshot' });
  assert.notEqual(stlDigest(expectedJobStl.content), stlDigest(editorStl.content));
  const [expectedJobConfig] = buildMwgConfigExportFiles(jobDesign, { baseName: 'horn_snapshot' });

  const fetchCalls = [];
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  try {
    GlobalState.loadState(editorDesign, 'test-editor-design');

    const bundle = await exportResults(
      { currentSmoothing: 'none', lastResults: null },
      {
        job: {
          id: 'job-snapshot-geometry',
          label: 'horn_snapshot',
          createdAt: '2026-03-11T10:00:00.000Z',
          script: {
            outputName: 'horn_snapshot',
            counter: 1,
            stateSnapshot: jobDesign,
          },
        },
        selectedFormats: ['stl', 'mwg_config'],
      }
    );

    assert.deepEqual(bundle.failures, []);
    assert.deepEqual(bundle.exportedFiles, [
      'stl:horn_snapshot.stl',
      'mwg_config:horn_snapshot.txt',
    ]);

    const filesByName = new Map();
    for (const call of fetchCalls) {
      const file = call.options.body.get('file');
      filesByName.set(file.name, file);
    }

    const exportedStl = await filesByName.get('horn_snapshot.stl').arrayBuffer();
    assert.equal(stlDigest(exportedStl), stlDigest(expectedJobStl.content));
    assert.notEqual(stlDigest(exportedStl), stlDigest(editorStl.content));

    const exportedConfig = await filesByName.get('horn_snapshot.txt').text();
    assert.equal(exportedConfig, expectedJobConfig.content);
  } finally {
    global.fetch = originalFetch;
    GlobalState.loadState(originalState, 'test-restore');
  }
});

test('exportResults fails job geometry exports without a design snapshot instead of using the editor design', async () => {
  const originalFetch = global.fetch;

  const fetchCalls = [];
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  const panel = {
    currentSmoothing: 'none',
    lastResults: {
      spl_on_axis: { frequencies: [100], spl: [90] },
      di: { di: [8] },
      impedance: { frequencies: [100], real: [6], imaginary: [1] },
      directivity: {},
    },
  };

  try {
    const bundle = await exportResults(panel, {
      job: {
        id: 'job-legacy-no-snapshot',
        label: 'horn_legacy_geom',
        createdAt: '2026-03-11T10:00:00.000Z',
        // Legacy job shape: prepared mesher params were saved, but no full
        // design state snapshot. Geometry exports must refuse rather than
        // silently exporting whatever design the editor holds.
        script: { outputName: 'horn_legacy_geom', counter: 2, params: { r0: 12.7 } },
      },
      selectedFormats: ['stl', 'csv'],
    });

    assert.equal(bundle.failures.length, 1);
    assert.equal(bundle.failures[0].formatId, 'stl');
    assert.match(bundle.failures[0].message, /design snapshot/);
    assert.deepEqual(bundle.exportedFiles, ['csv:horn_legacy_geom_results.csv']);

    // Only the result CSV may be written — nothing derived from the editor
    // design is allowed to reach the job's workspace folder.
    assert.equal(fetchCalls.length, 1);
    assert.equal(fetchCalls[0].options.body.get('file').name, 'horn_legacy_geom_results.csv');
  } finally {
    global.fetch = originalFetch;
  }
});

test('persistSimulationGenerationArtifacts writes raw results and mesh artifacts via backend', async () => {
  const originalFetch = global.fetch;
  const fetchCalls = [];

  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  try {
    const persisted = await persistSimulationGenerationArtifacts(
      {
        id: 'job-4',
        label: 'horn_56',
        createdAt: '2026-03-11T10:00:00.000Z',
      },
      {
        results: { spl_on_axis: { frequencies: [100], spl: [90] }, metadata: { solveMs: 123 } },
        meshArtifactText: '$MeshFormat\n2.2 0 8\n$EndMeshFormat',
      }
    );

    assert.equal(persisted.warnings.length, 0);
    assert.equal(persisted.rawResultsFile, 'horn_56_raw.results.json');
    assert.equal(persisted.meshArtifactFile, 'horn_56_solver.mesh.msh');

    // Both artifacts should be written via fetch to the backend export endpoint
    assert.equal(fetchCalls.length, 2);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[1].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[0].options.body.get('workspace_subdir'), '260311_horn_56');
    assert.equal(fetchCalls[1].options.body.get('workspace_subdir'), '260311_horn_56');
  } finally {
    global.fetch = originalFetch;
  }
});

test('persistSimulationGenerationArtifacts keeps writing later artifacts after an earlier write fails', async () => {
  const originalFetch = global.fetch;
  const fetchCalls = [];

  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    if (fetchCalls.length === 1) {
      return {
        ok: false,
        status: 500,
        statusText: 'Write failed',
        async json() {
          return { detail: 'raw write failed' };
        },
      };
    }
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  try {
    const persisted = await persistSimulationGenerationArtifacts(
      {
        id: 'job-partial',
        label: 'horn_partial',
        createdAt: '2026-03-11T10:00:00.000Z',
      },
      {
        results: { ok: true },
        meshArtifactText: '$MeshFormat\n2.2 0 8\n$EndMeshFormat',
      }
    );

    assert.equal(persisted.rawResultsFile, null);
    assert.equal(persisted.meshArtifactFile, 'horn_partial_solver.mesh.msh');
    assert.deepEqual(persisted.warnings, ['Raw results artifact write failed: raw write failed']);
    assert.equal(fetchCalls.length, 2);
    assert.equal(fetchCalls[1].options.body.get('workspace_subdir'), '260311_horn_partial');
  } finally {
    global.fetch = originalFetch;
  }
});

test('persistSimulationGenerationArtifacts falls back to backend workspace subdirectory', async () => {
  const originalFetch = global.fetch;
  const fetchCalls = [];

  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      },
    };
  };

  try {
    const persisted = await persistSimulationGenerationArtifacts(
      {
        id: 'job-5',
        label: 'horn_57',
        createdAt: '2026-03-11T10:00:00.000Z',
      },
      {
        results: { ok: true },
        meshArtifactText: '$MeshFormat',
      }
    );

    assert.equal(persisted.warnings.length, 0);
    assert.equal(fetchCalls.length, 2);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[1].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[0].options.body.get('workspace_subdir'), '260311_horn_57');
    assert.equal(fetchCalls[1].options.body.get('workspace_subdir'), '260311_horn_57');
  } finally {
    global.fetch = originalFetch;
  }
});
