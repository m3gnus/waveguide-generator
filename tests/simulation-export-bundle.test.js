import test from 'node:test';
import assert from 'node:assert/strict';

import { exportResults } from '../src/ui/simulation/exports.js';
import {
  persistSimulationGenerationArtifacts,
  writeSimulationTaskBundleFile
} from '../src/ui/simulation/workspaceTasks.js';
import {
  getSelectedFolderHandle,
  resetSelectedFolder,
  setSelectedFolderHandle
} from '../src/ui/workspace/folderWorkspace.js';

test('exportResults writes selected bundle files into the task folder workspace', async () => {
  const originalFetch = global.fetch;

  const fetchCalls = [];
  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      }
    };
  };

  const panel = {
    currentSmoothing: 'none',
    lastResults: {
      spl_on_axis: { frequencies: [100], spl: [90] },
      di: { di: [8] },
      impedance: { frequencies: [100], real: [6], imaginary: [1] },
      directivity: {}
    }
  };

  try {
    const bundle = await exportResults(panel, {
      job: {
        id: 'job-1',
        label: 'horn_12'
      },
      selectedFormats: ['csv', 'json']
    });

    assert.deepEqual(bundle.exportedFiles, [
      'csv:horn_12_results.csv',
      'json:horn_12_results.json'
    ]);
    assert.deepEqual(bundle.failures, []);

    // Both files should be written via fetch to the backend export endpoint
    assert.equal(fetchCalls.length, 2);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[1].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[0].options.body.get('workspace_subdir'), 'horn_12');
    assert.equal(fetchCalls[1].options.body.get('workspace_subdir'), 'horn_12');
  } finally {
    global.fetch = originalFetch;
    resetSelectedFolder();
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
      }
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
        imaginary: [0, -1]
      },
      directivity: {}
    }
  };

  try {
    await exportResults(panel, {
      job: {
        id: 'job-zero',
        label: 'horn_zero'
      },
      selectedFormats: ['csv']
    });

    assert.equal(fetchCalls.length, 1);
    const csvFile = fetchCalls[0].options.body.get('file');
    const csv = await csvFile.text();
    assert.match(
      csv,
      /100,0,0,0,0\n200,91,8,6,-1\n$/,
    );
  } finally {
    global.fetch = originalFetch;
    resetSelectedFolder();
  }
});

test('writeSimulationTaskBundleFile clears the selected workspace and falls back when task-folder writes fail', async () => {
  setSelectedFolderHandle({
    name: 'workspace',
    async queryPermission() {
      return 'granted';
    },
    async getDirectoryHandle() {
      throw new Error('write failed');
    }
  }, { label: 'workspace' });

  const fallbackCalls = [];

  try {
    const result = await writeSimulationTaskBundleFile(
      { id: 'job-2' },
      {
        fileName: 'job-2_results.json',
        content: '{"ok":true}',
        saveOptions: { contentType: 'application/json' }
      },
      {
        fallbackWrite: async (file) => {
          fallbackCalls.push(file.fileName);
        }
      }
    );

    assert.equal(result.wroteToTaskFolder, false);
    assert.deepEqual(fallbackCalls, ['job-2_results.json']);
    assert.equal(getSelectedFolderHandle(), null);
  } finally {
    resetSelectedFolder();
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
      }
    };
  };

  const panel = {
    currentSmoothing: 'none',
    lastResults: {
      spl_on_axis: { frequencies: [100], spl: [90] },
      di: { di: [8] },
      impedance: { frequencies: [100], real: [6], imaginary: [1] },
      directivity: {}
    }
  };

  try {
    const bundle = await exportResults(panel, {
      job: {
        id: 'job-3',
        label: 'horn_34'
      },
      selectedFormats: ['csv']
    });

    assert.deepEqual(bundle.exportedFiles, ['csv:horn_34_results.csv']);
    assert.equal(fetchCalls.length, 1);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');
    const body = fetchCalls[0].options.body;
    assert.equal(body.get('workspace_subdir'), 'horn_34');
  } finally {
    global.fetch = originalFetch;
    resetSelectedFolder();
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
      }
    };
  };

  try {
    const persisted = await persistSimulationGenerationArtifacts(
      {
        id: 'job-4',
        label: 'horn_56'
      },
      {
        results: { spl_on_axis: { frequencies: [100], spl: [90] }, metadata: { solveMs: 123 } },
        meshArtifactText: '$MeshFormat\n2.2 0 8\n$EndMeshFormat'
      }
    );

    assert.equal(persisted.warnings.length, 0);
    assert.equal(persisted.rawResultsFile, 'horn_56_raw.results.json');
    assert.equal(persisted.meshArtifactFile, 'horn_56_solver.mesh.msh');

    // Both artifacts should be written via fetch to the backend export endpoint
    assert.equal(fetchCalls.length, 2);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[1].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[0].options.body.get('workspace_subdir'), 'horn_56');
    assert.equal(fetchCalls[1].options.body.get('workspace_subdir'), 'horn_56');
  } finally {
    global.fetch = originalFetch;
    resetSelectedFolder();
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
        }
      };
    }
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      }
    };
  };

  try {
    const persisted = await persistSimulationGenerationArtifacts(
      {
        id: 'job-partial',
        label: 'horn_partial'
      },
      {
        results: { ok: true },
        meshArtifactText: '$MeshFormat\n2.2 0 8\n$EndMeshFormat'
      }
    );

    assert.equal(persisted.rawResultsFile, null);
    assert.equal(persisted.meshArtifactFile, 'horn_partial_solver.mesh.msh');
    assert.deepEqual(persisted.warnings, [
      'Raw results artifact write failed: raw write failed'
    ]);
    assert.equal(fetchCalls.length, 2);
    assert.equal(fetchCalls[1].options.body.get('workspace_subdir'), 'horn_partial');
  } finally {
    global.fetch = originalFetch;
    resetSelectedFolder();
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
      }
    };
  };

  try {
    const persisted = await persistSimulationGenerationArtifacts(
      {
        id: 'job-5',
        label: 'horn_57'
      },
      {
        results: { ok: true },
        meshArtifactText: '$MeshFormat'
      }
    );

    assert.equal(persisted.warnings.length, 0);
    assert.equal(fetchCalls.length, 2);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[1].url, 'http://localhost:8000/api/export-file');
    assert.equal(fetchCalls[0].options.body.get('workspace_subdir'), 'horn_57');
    assert.equal(fetchCalls[1].options.body.get('workspace_subdir'), 'horn_57');
  } finally {
    global.fetch = originalFetch;
    resetSelectedFolder();
  }
});
