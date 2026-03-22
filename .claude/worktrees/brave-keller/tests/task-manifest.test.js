import test from 'node:test';
import assert from 'node:assert/strict';

import {
  TASK_MANIFEST_FILE_NAME,
  TASK_SCRIPT_SCHEMA_VERSION,
  createTaskManifestFromJob,
  normalizeTaskManifest,
  readTaskManifest,
  resolveTaskWorkspaceDirectoryName,
  updateTaskManifestForJob
} from '../src/ui/workspace/taskManifest.js';
import {
  GENERATION_PROJECT_MANIFEST_FILE_NAME,
  GENERATION_SCRIPT_SNAPSHOT_FILE_NAME
} from '../src/ui/workspace/generationArtifacts.js';

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
    autoExportCompletedAt: '2026-03-11T10:00:00.000Z',
    exportedFiles: ['file-a.csv'],
    rawResultsFile: 'horn_42_raw.results.json',
    meshArtifactFile: 'horn_42_solver.mesh.msh',
    script: { outputName: 'horn' }
  });

  assert.equal(manifest.id, 'job-42');
  assert.equal(manifest.label, 'horn_42');
  assert.equal(manifest.rating, 4);
  assert.equal(manifest.autoExportCompletedAt, '2026-03-11T10:00:00.000Z');
  assert.deepEqual(manifest.exportedFiles, ['file-a.csv']);
  assert.equal(manifest.rawResultsFile, 'horn_42_raw.results.json');
  assert.equal(manifest.meshArtifactFile, 'horn_42_solver.mesh.msh');
  assert.deepEqual(manifest.scriptSnapshot, { outputName: 'horn' });
});

test('resolveTaskWorkspaceDirectoryName prefers label, then script, then id', () => {
  assert.equal(
    resolveTaskWorkspaceDirectoryName({ id: 'job-1', label: 'horn_1' }),
    'horn_1'
  );
  assert.equal(
    resolveTaskWorkspaceDirectoryName({
      id: 'job-2',
      scriptSnapshot: { outputName: 'waveguide', counter: 7 }
    }),
    'waveguide_7'
  );
  assert.equal(
    resolveTaskWorkspaceDirectoryName({ id: 'job-3' }),
    'job-3'
  );
});

test('updateTaskManifestForJob writes and reads task.manifest.json with defaults', async () => {
  const root = createMemoryDirectory();

  const updated = await updateTaskManifestForJob(root, {
    id: 'job-5',
    label: 'horn_5',
    status: 'queued',
    exportedFiles: []
  });

  assert.equal(updated.warning, null);
  assert.equal(updated.manifest.id, 'job-5');

  const taskDir = await root.getDirectoryHandle('horn_5');
  const fileHandle = await taskDir.getFileHandle(TASK_MANIFEST_FILE_NAME);
  const rawFile = await fileHandle.getFile();
  const text = await rawFile.text();
  assert.match(text, /"id": "job-5"/);

  const reread = await readTaskManifest(taskDir);
  assert.equal(reread.warning, null);
  assert.equal(reread.manifest.id, 'job-5');
  assert.deepEqual(reread.manifest.exportedFiles, []);

  const projectFileHandle = await taskDir.getFileHandle(GENERATION_PROJECT_MANIFEST_FILE_NAME);
  const projectFile = await projectFileHandle.getFile();
  const projectPayload = JSON.parse(await projectFile.text());
  assert.equal(projectPayload.generation.id, 'job-5');
  assert.equal(projectPayload.generation.folder, 'horn_5');
  assert.equal(projectPayload.artifacts.scriptSnapshot, null);
  assert.deepEqual(projectPayload.artifacts.selectedExports, []);
});

test('updateTaskManifestForJob falls back to job id directory when no generation name is available', async () => {
  const root = createMemoryDirectory();

  await updateTaskManifestForJob(root, {
    id: 'job-6',
    status: 'queued',
    exportedFiles: []
  });

  const taskDir = await root.getDirectoryHandle('job-6');
  const fileHandle = await taskDir.getFileHandle(TASK_MANIFEST_FILE_NAME);
  const rawFile = await fileHandle.getFile();
  const text = await rawFile.text();
  assert.match(text, /"id": "job-6"/);
});

test('updateTaskManifestForJob reuses legacy job-id manifest data when migrating to generation folder name', async () => {
  const root = createMemoryDirectory();

  await updateTaskManifestForJob(root, {
    id: 'job-7',
    status: 'queued',
    exportedFiles: ['first.csv']
  });

  const migrated = await updateTaskManifestForJob(root, {
    id: 'job-7',
    label: 'horn_7',
    status: 'complete'
  });

  assert.equal(migrated.manifest.id, 'job-7');
  assert.equal(migrated.manifest.label, 'horn_7');
  assert.deepEqual(migrated.manifest.exportedFiles, ['first.csv']);

  const migratedDir = await root.getDirectoryHandle('horn_7');
  const reread = await readTaskManifest(migratedDir);
  assert.equal(reread.manifest.id, 'job-7');
  assert.equal(root.directories.has('job-7'), true);
});

test('updateTaskManifestForJob writes deterministic script snapshot and project artifact metadata', async () => {
  const root = createMemoryDirectory();

  await updateTaskManifestForJob(root, {
    id: 'job-8',
    label: 'horn_8',
    status: 'complete',
    exportedFiles: ['csv:horn_8_results.csv', 'json:horn_8_results.json'],
    rawResultsFile: 'horn_8_raw.results.json',
    meshArtifactFile: 'horn_8_solver.mesh.msh',
    scriptSnapshot: {
      outputName: 'horn',
      counter: 8,
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 5,
      stateSnapshot: { type: 'R-OSSE' },
      params: {
        type: 'R-OSSE',
        R: 1.2,
        a: 90,
        a0: 45,
        b: 0.5,
        k: 1.1,
        m: 0.8,
        q: 0.7,
        r: 42,
        r0: 10,
        tmax: 1,
        angularSegments: 40,
        lengthSegments: 20
      }
    }
  });

  const taskDir = await root.getDirectoryHandle('horn_8');
  const snapshotHandle = await taskDir.getFileHandle(GENERATION_SCRIPT_SNAPSHOT_FILE_NAME);
  const snapshotText = await (await snapshotHandle.getFile()).text();
  assert.match(snapshotText, /; MWG config/);
  assert.match(snapshotText, /Simulation.F1 = 100/);
  assert.match(snapshotText, /Simulation.F2 = 1000/);
  assert.match(snapshotText, /Simulation.NumFrequencies = 5/);

  const projectHandle = await taskDir.getFileHandle(GENERATION_PROJECT_MANIFEST_FILE_NAME);
  const projectPayload = JSON.parse(await (await projectHandle.getFile()).text());
  assert.equal(projectPayload.generation.folder, 'horn_8');
  assert.equal(projectPayload.artifacts.scriptSnapshot.fileName, GENERATION_SCRIPT_SNAPSHOT_FILE_NAME);
  assert.equal(projectPayload.artifacts.rawResults.fileName, 'horn_8_raw.results.json');
  assert.equal(projectPayload.artifacts.meshArtifact.fileName, 'horn_8_solver.mesh.msh');
  assert.deepEqual(projectPayload.artifacts.selectedExports, [
    { formatId: 'csv', fileName: 'horn_8_results.csv' },
    { formatId: 'json', fileName: 'horn_8_results.json' }
  ]);
});
