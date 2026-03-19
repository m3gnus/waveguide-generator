import test from 'node:test';
import assert from 'node:assert/strict';

import { exportResults } from '../src/ui/simulation/exports.js';
import { writeSimulationTaskBundleFile } from '../src/ui/simulation/workspaceTasks.js';
import {
  getSelectedFolderHandle,
  resetSelectedFolder,
  setSelectedFolderHandle
} from '../src/ui/workspace/folderWorkspace.js';

function createMemoryDirectory(name = 'root') {
  const files = new Map();
  const directories = new Map();

  return {
    kind: 'directory',
    name,
    async getDirectoryHandle(dirName, options = {}) {
      if (!directories.has(dirName)) {
        if (!options.create) {
          const error = new Error('not found');
          error.name = 'NotFoundError';
          throw error;
        }
        directories.set(dirName, createMemoryDirectory(dirName));
      }
      return directories.get(dirName);
    },
    async getFileHandle(fileName, options = {}) {
      if (!files.has(fileName)) {
        if (!options.create) {
          const error = new Error('not found');
          error.name = 'NotFoundError';
          throw error;
        }
        files.set(fileName, '');
      }
      return {
        async getFile() {
          const textValue = files.get(fileName) ?? '';
          return { async text() { return textValue; } };
        },
        async createWritable() {
          return {
            async write(content) {
              if (content && typeof content.text === 'function') {
                files.set(fileName, await content.text());
                return;
              }
              files.set(fileName, String(content));
            },
            async close() {}
          };
        }
      };
    },
    files,
    directories,
    async *entries() {
      for (const [dirName, dirHandle] of directories.entries()) {
        yield [dirName, dirHandle];
      }
      for (const [fileName] of files.entries()) {
        yield [fileName, { kind: 'file', name: fileName }];
      }
    }
  };
}

test('exportResults writes selected bundle files into the task folder workspace', async () => {
  const root = createMemoryDirectory();
  setSelectedFolderHandle(root, { label: 'workspace' });

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

    // exportResults uses baseName (job.label) as the subdirectory name, not job.id
    const taskDir = await root.getDirectoryHandle('horn_12');
    assert.equal(taskDir.files.has('horn_12_results.csv'), true);
    assert.equal(taskDir.files.has('horn_12_results.json'), true);
    assert.match(taskDir.files.get('horn_12_results.csv'), /Frequency \(Hz\),SPL \(dB\)/);
    assert.match(taskDir.files.get('horn_12_results.json'), /"smoothing": "none"/);
  } finally {
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
