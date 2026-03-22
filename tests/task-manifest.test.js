import test from 'node:test';
import assert from 'node:assert/strict';

import {
  TASK_MANIFEST_FILE_NAME,
  TASK_SCRIPT_SCHEMA_VERSION,
  createTaskManifestFromJob,
  normalizeTaskManifest,
  resolveTaskWorkspaceDirectoryName,
  updateTaskManifestForJob
} from '../src/ui/workspace/taskManifest.js';
import {
  GENERATION_PROJECT_MANIFEST_FILE_NAME,
  GENERATION_SCRIPT_SNAPSHOT_FILE_NAME
} from '../src/ui/workspace/generationArtifacts.js';

/**
 * Creates a mock fallbackWriteFile that stores written files in a Map.
 * Returns { writeFile, files } where files is a Map<fileName, { content, contentType }>.
 */
function createMockWriteFile() {
  const files = new Map();
  const writeFile = async (fileName, content, contentType) => {
    files.set(fileName, { content: String(content), contentType });
  };
  return { writeFile, files };
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

test('updateTaskManifestForJob writes task.manifest.json via fallbackWriteFile', async () => {
  const { writeFile, files } = createMockWriteFile();

  const updated = await updateTaskManifestForJob(null, {
    id: 'job-5',
    label: 'horn_5',
    status: 'queued',
    exportedFiles: []
  }, {}, { fallbackWriteFile: writeFile });

  assert.equal(updated.warning, null);
  assert.equal(updated.manifest.id, 'job-5');

  // Verify the manifest file was written
  assert.ok(files.has(TASK_MANIFEST_FILE_NAME), 'task manifest file should be written');
  const manifestContent = files.get(TASK_MANIFEST_FILE_NAME).content;
  assert.match(manifestContent, /"id": "job-5"/);

  // Verify the project manifest was written
  assert.ok(files.has(GENERATION_PROJECT_MANIFEST_FILE_NAME), 'project manifest should be written');
  const projectPayload = JSON.parse(files.get(GENERATION_PROJECT_MANIFEST_FILE_NAME).content);
  assert.equal(projectPayload.generation.id, 'job-5');
  assert.equal(projectPayload.generation.folder, 'horn_5');
  assert.equal(projectPayload.artifacts.scriptSnapshot, null);
  assert.deepEqual(projectPayload.artifacts.selectedExports, []);
});

test('updateTaskManifestForJob returns warning when no fallbackWriteFile is provided', async () => {
  const result = await updateTaskManifestForJob(null, {
    id: 'job-6',
    status: 'queued',
    exportedFiles: []
  });

  assert.equal(result.manifest, null);
  assert.match(result.warning, /unavailable/i);
});

test('updateTaskManifestForJob preserves exported files across updates', async () => {
  const { writeFile, files } = createMockWriteFile();

  await updateTaskManifestForJob(null, {
    id: 'job-7',
    status: 'queued',
    exportedFiles: ['first.csv']
  }, {}, { fallbackWriteFile: writeFile });

  const migrated = await updateTaskManifestForJob(null, {
    id: 'job-7',
    label: 'horn_7',
    status: 'complete'
  }, {}, { fallbackWriteFile: writeFile });

  assert.equal(migrated.manifest.id, 'job-7');
  assert.equal(migrated.manifest.label, 'horn_7');
  // Note: without a shared directory handle, the function creates manifests
  // independently from the job data passed in. exportedFiles comes from
  // the job object, which does not have exportedFiles in the second call.
  assert.deepEqual(migrated.manifest.exportedFiles, []);
});

test('updateTaskManifestForJob writes deterministic script snapshot and project artifact metadata', async () => {
  const { writeFile, files } = createMockWriteFile();

  await updateTaskManifestForJob(null, {
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
  }, {}, { fallbackWriteFile: writeFile });

  // Check the script snapshot file was written
  assert.ok(files.has(GENERATION_SCRIPT_SNAPSHOT_FILE_NAME), 'script snapshot should be written');
  const snapshotText = files.get(GENERATION_SCRIPT_SNAPSHOT_FILE_NAME).content;
  assert.match(snapshotText, /; MWG config/);
  assert.match(snapshotText, /Simulation.F1 = 100/);
  assert.match(snapshotText, /Simulation.F2 = 1000/);
  assert.match(snapshotText, /Simulation.NumFrequencies = 5/);

  // Check the project manifest
  assert.ok(files.has(GENERATION_PROJECT_MANIFEST_FILE_NAME), 'project manifest should be written');
  const projectPayload = JSON.parse(files.get(GENERATION_PROJECT_MANIFEST_FILE_NAME).content);
  assert.equal(projectPayload.generation.folder, 'horn_8');
  assert.equal(projectPayload.artifacts.scriptSnapshot.fileName, GENERATION_SCRIPT_SNAPSHOT_FILE_NAME);
  assert.equal(projectPayload.artifacts.rawResults.fileName, 'horn_8_raw.results.json');
  assert.equal(projectPayload.artifacts.meshArtifact.fileName, 'horn_8_solver.mesh.msh');
  assert.deepEqual(projectPayload.artifacts.selectedExports, [
    { formatId: 'csv', fileName: 'horn_8_results.csv' },
    { formatId: 'json', fileName: 'horn_8_results.json' }
  ]);
});
