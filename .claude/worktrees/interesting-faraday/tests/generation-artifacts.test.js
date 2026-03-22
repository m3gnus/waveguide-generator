import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildGenerationProjectManifest,
  parseExportedFileRecord,
  resolveGenerationExportFileName,
  resolveGenerationRuntimeArtifactFileName,
  resolveGenerationScriptSnapshotFileName
} from '../src/ui/workspace/generationArtifacts.js';

test('resolveGenerationExportFileName returns deterministic names for known formats', () => {
  assert.equal(resolveGenerationExportFileName('csv', { baseName: 'horn_12' }), 'horn_12_results.csv');
  assert.equal(resolveGenerationExportFileName('json', { baseName: 'horn_12' }), 'horn_12_results.json');
  assert.equal(resolveGenerationExportFileName('txt', { baseName: 'horn_12' }), 'horn_12_report.txt');
  assert.equal(resolveGenerationExportFileName('polar_csv', { baseName: 'horn_12' }), 'horn_12_polar.csv');
  assert.equal(resolveGenerationExportFileName('impedance_csv', { baseName: 'horn_12' }), 'horn_12_impedance.csv');
  assert.equal(resolveGenerationExportFileName('vacs', { baseName: 'horn_12' }), 'horn_12_spectrum.txt');
  assert.equal(resolveGenerationExportFileName('stl', { baseName: 'horn_12' }), 'horn_12.stl');
  assert.equal(
    resolveGenerationExportFileName('png', { baseName: 'horn_12', chartKey: 'directivity_index' }),
    'horn_12_directivity_index.png'
  );
});

test('resolveGenerationExportFileName maps fusion exports to profiles/slices deterministically', () => {
  assert.equal(
    resolveGenerationExportFileName('fusion_csv', {
      baseName: 'horn_12',
      originalFileName: 'horn_12_profiles.csv'
    }),
    'horn_12_profiles.csv'
  );
  assert.equal(
    resolveGenerationExportFileName('fusion_csv', {
      baseName: 'horn_12',
      originalFileName: 'horn_12_slices.csv'
    }),
    'horn_12_slices.csv'
  );
});

test('resolveGenerationRuntimeArtifactFileName returns deterministic names for raw results and mesh artifact', () => {
  assert.equal(
    resolveGenerationRuntimeArtifactFileName('raw_results', { baseName: 'horn_12' }),
    'horn_12_raw.results.json'
  );
  assert.equal(
    resolveGenerationRuntimeArtifactFileName('mesh_artifact', { baseName: 'horn_12' }),
    'horn_12_solver.mesh.msh'
  );
});

test('parseExportedFileRecord enforces formatId:fileName shape', () => {
  assert.deepEqual(parseExportedFileRecord('csv:horn_12_results.csv'), {
    formatId: 'csv',
    fileName: 'horn_12_results.csv'
  });
  assert.equal(parseExportedFileRecord('invalid'), null);
  assert.equal(parseExportedFileRecord('csv:'), null);
});

test('buildGenerationProjectManifest includes script and selected export artifacts', () => {
  const payload = buildGenerationProjectManifest({
    directoryName: 'horn_12',
    job: {
      id: 'job-12',
      label: 'horn_12',
      status: 'complete'
    },
    exportedFiles: [
      'csv:horn_12_results.csv',
      'json:horn_12_results.json',
      'csv:horn_12_results.csv'
    ],
    scriptSnapshotFileName: resolveGenerationScriptSnapshotFileName(),
    rawResultsFileName: 'horn_12_raw.results.json',
    meshArtifactFileName: 'horn_12_solver.mesh.msh',
    updatedAt: '2026-03-19T10:00:00.000Z'
  });

  assert.equal(payload.generation.folder, 'horn_12');
  assert.equal(payload.generation.id, 'job-12');
  assert.equal(payload.updatedAt, '2026-03-19T10:00:00.000Z');
  assert.equal(payload.artifacts.scriptSnapshot.fileName, resolveGenerationScriptSnapshotFileName());
  assert.equal(payload.artifacts.rawResults.fileName, 'horn_12_raw.results.json');
  assert.equal(payload.artifacts.meshArtifact.fileName, 'horn_12_solver.mesh.msh');
  assert.deepEqual(payload.artifacts.selectedExports, [
    { formatId: 'csv', fileName: 'horn_12_results.csv' },
    { formatId: 'json', fileName: 'horn_12_results.json' }
  ]);
});
