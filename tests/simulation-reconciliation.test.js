import test from 'node:test';
import assert from 'node:assert/strict';

import {
  mergeJobs,
  hasActiveJobs,
  persistPanelJobs,
  allJobs,
  JOB_TRACKER_CONSTANTS
} from '../src/ui/simulation/jobTracker.js';
import {
  setSelectedFolderHandle,
  resetSelectedFolder
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

test('mergeJobs prefers backend status updates for same job id', () => {
  const merged = mergeJobs(
    [{ id: 'job-1', status: 'running', progress: 0.2 }],
    [{ id: 'job-1', status: 'complete', progress: 1 }]
  );

  assert.equal(merged.length, 1);
  assert.equal(merged[0].status, 'complete');
  assert.equal(merged[0].progress, 1);
});

test('hasActiveJobs checks queued/running states', () => {
  const panel = {
    jobs: new Map([
      ['a', { id: 'a', status: 'error' }],
      ['b', { id: 'b', status: 'queued' }]
    ])
  };

  assert.equal(hasActiveJobs(panel), true);
  panel.jobs.set('b', { id: 'b', status: 'complete' });
  assert.equal(hasActiveJobs(panel), false);
});

test('persistPanelJobs syncs workspace index without error', async () => {
  const root = createMemoryDirectory();
  setSelectedFolderHandle(root, { label: 'workspace' });

  try {
    const panel = {
      jobs: new Map()
    };

    for (let i = 0; i < 60; i += 1) {
      panel.jobs.set(`job-${i}`, {
        id: `job-${i}`,
        status: 'complete',
        progress: 1,
        createdAt: new Date(2026, 1, 1, 0, i, 0).toISOString()
      });
    }

    // persistPanelJobs delegates to syncSimulationWorkspaceIndex (fire-and-forget)
    persistPanelJobs(panel);

    // Allow the async workspace sync to complete
    await new Promise((resolve) => setTimeout(resolve, 50));

    // Verify the task index was written to the workspace
    assert.equal(root.files.has('.waveguide-tasks.index.v1.json'), true);
    const indexData = JSON.parse(root.files.get('.waveguide-tasks.index.v1.json'));
    assert.ok(Array.isArray(indexData.items));
    assert.equal(allJobs(panel).length, 60);
  } finally {
    resetSelectedFolder();
  }
});

test('mergeJobs preserves manifest metadata when backend omits fields', () => {
  const merged = mergeJobs(
    [
      {
        id: 'job-1',
        status: 'queued',
        rating: 4,
        exportedFiles: ['first.csv'],
        scriptSchemaVersion: 2,
        scriptSnapshot: { outputName: 'horn' }
      }
    ],
    [{ id: 'job-1', status: 'running', progress: 0.4 }]
  );

  assert.equal(merged.length, 1);
  assert.equal(merged[0].status, 'running');
  assert.equal(merged[0].rating, 4);
  assert.deepEqual(merged[0].exportedFiles, ['first.csv']);
  assert.equal(merged[0].scriptSchemaVersion, 2);
  assert.deepEqual(merged[0].scriptSnapshot, { outputName: 'horn' });
});
