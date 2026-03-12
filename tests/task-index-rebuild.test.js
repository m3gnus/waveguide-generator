import test from 'node:test';
import assert from 'node:assert/strict';

import {
  TASK_INDEX_FILE_NAME,
  buildTaskIndexEntriesFromJobs,
  loadTaskIndex,
  rebuildIndexFromManifests,
  writeTaskIndex
} from '../src/ui/workspace/taskIndex.js';
import { updateTaskManifestForJob } from '../src/ui/workspace/taskManifest.js';

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

test('writeTaskIndex and loadTaskIndex round-trip normalized items', async () => {
  const root = createMemoryDirectory();
  await writeTaskIndex(root, [{
    id: 'job-1',
    status: 'complete',
    exportedFiles: ['a.csv'],
    symmetrySummary: {
      badge: 'Reduced',
      headline: 'Applied half-domain reduction',
      details: 'The solver applied half-domain reduction.',
      tone: 'success',
      items: [
        { label: 'Requested', value: 'Enabled' },
        { label: 'Decision', value: 'Half-domain (X symmetry)' }
      ]
    }
  }]);

  const loaded = await loadTaskIndex(root);
  assert.equal(loaded.warning, null);
  assert.equal(loaded.exists, true);
  assert.equal(loaded.items.length, 1);
  assert.equal(loaded.items[0].id, 'job-1');
  assert.deepEqual(loaded.items[0].exportedFiles, ['a.csv']);
  assert.equal(loaded.items[0].symmetrySummary?.badge, 'Reduced');
});

test('loadTaskIndex reports warning for missing and corrupt index', async () => {
  const root = createMemoryDirectory();
  const missing = await loadTaskIndex(root);
  assert.equal(missing.exists, false);
  assert.match(missing.warning, /missing/i);

  root.files.set(TASK_INDEX_FILE_NAME, '{bad-json');
  const corrupt = await loadTaskIndex(root);
  assert.equal(corrupt.exists, true);
  assert.match(corrupt.warning, /invalid/i);
});

test('rebuildIndexFromManifests scans task folders and returns repair payload', async () => {
  const root = createMemoryDirectory();
  await updateTaskManifestForJob(root, {
    id: 'job-1',
    label: 'job_1',
    status: 'complete',
    createdAt: '2026-02-28T12:00:00.000Z',
    exportedFiles: ['results.csv'],
    symmetrySummary: {
      badge: 'Full model',
      headline: 'Symmetry reduction disabled',
      details: 'The solve request disabled symmetry reduction.',
      tone: 'neutral',
      items: [
        { label: 'Requested', value: 'Disabled' },
        { label: 'Decision', value: 'Full model' }
      ]
    }
  });

  const rebuilt = await rebuildIndexFromManifests(root);
  assert.equal(rebuilt.repaired, true);
  assert.equal(rebuilt.items.length, 1);
  assert.equal(rebuilt.items[0].id, 'job-1');
  assert.deepEqual(rebuilt.items[0].exportedFiles, ['results.csv']);
  assert.equal(rebuilt.items[0].symmetrySummary?.items?.[0]?.value, 'Disabled');
});

test('buildTaskIndexEntriesFromJobs preserves manifest metadata fields', () => {
  const entries = buildTaskIndexEntriesFromJobs([
    {
      id: 'job-2',
      status: 'running',
      rating: 5,
      autoExportCompletedAt: '2026-03-11T10:10:00.000Z',
      exportedFiles: ['export.csv'],
      symmetrySummary: {
        badge: 'Requested',
        headline: 'Symmetry reduction requested',
        details: 'The solve request allows symmetry reduction.',
        tone: 'neutral',
        items: [
          { label: 'Requested', value: 'Enabled' },
          { label: 'Decision', value: 'Pending results' }
        ]
      },
      scriptSchemaVersion: 2,
      scriptSnapshot: { outputName: 'horn' }
    }
  ]);

  assert.equal(entries.length, 1);
  assert.equal(entries[0].id, 'job-2');
  assert.equal(entries[0].rating, 5);
  assert.equal(entries[0].autoExportCompletedAt, '2026-03-11T10:10:00.000Z');
  assert.deepEqual(entries[0].exportedFiles, ['export.csv']);
  assert.equal(entries[0].symmetrySummary?.badge, 'Requested');
});
