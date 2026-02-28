import test from 'node:test';
import assert from 'node:assert/strict';

import {
  TASK_MANIFEST_FILE_NAME,
  TASK_SCRIPT_SCHEMA_VERSION,
  createTaskManifestFromJob,
  normalizeTaskManifest,
  readTaskManifest,
  updateTaskManifestForJob
} from '../src/ui/workspace/taskManifest.js';

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

test('normalizeTaskManifest enforces required defaults', () => {
  const normalized = normalizeTaskManifest({ id: 'job-1', status: 'complete' });
  assert.equal(normalized.id, 'job-1');
  assert.equal(normalized.version, 1);
  assert.equal(normalized.rating, null);
  assert.deepEqual(normalized.exportedFiles, []);
  assert.equal(normalized.scriptSchemaVersion, TASK_SCRIPT_SCHEMA_VERSION);
});

test('createTaskManifestFromJob maps script and metadata fields', () => {
  const manifest = createTaskManifestFromJob({
    id: 'job-42',
    label: 'horn_42',
    status: 'running',
    rating: 4,
    exportedFiles: ['file-a.csv'],
    script: { outputName: 'horn' }
  });

  assert.equal(manifest.id, 'job-42');
  assert.equal(manifest.label, 'horn_42');
  assert.equal(manifest.rating, 4);
  assert.deepEqual(manifest.exportedFiles, ['file-a.csv']);
  assert.deepEqual(manifest.scriptSnapshot, { outputName: 'horn' });
});

test('updateTaskManifestForJob writes and reads task.manifest.json with defaults', async () => {
  const root = createMemoryDirectory();

  const updated = await updateTaskManifestForJob(root, {
    id: 'job-5',
    label: 'job_5',
    status: 'queued',
    exportedFiles: []
  });

  assert.equal(updated.warning, null);
  assert.equal(updated.manifest.id, 'job-5');

  const taskDir = await root.getDirectoryHandle('job-5');
  const fileHandle = await taskDir.getFileHandle(TASK_MANIFEST_FILE_NAME);
  const rawFile = await fileHandle.getFile();
  const text = await rawFile.text();
  assert.match(text, /"id": "job-5"/);

  const reread = await readTaskManifest(taskDir);
  assert.equal(reread.warning, null);
  assert.equal(reread.manifest.id, 'job-5');
  assert.deepEqual(reread.manifest.exportedFiles, []);
});
