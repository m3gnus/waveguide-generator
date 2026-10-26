import { readFileSync } from 'node:fs';
import { beforeEach, expect, it } from 'vitest';
import type { RegisteredMesh } from '../api/meshImport';
import { archivedRequest, bundle, job, json, mesh, record, workspaceFor } from '../shell/cadlink/selection.fixtures';
import '../shell/cadlink/restores';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { openImported, resetImportedModelStore, restoreCadImportedSetup, unchangedSetup } from '../stores/importedModel';
import { restoreCadProjectSettings } from '../stores/cadProjectSettings';
import { fileMeshSubmission } from '../stores/meshImport';
import { resetSolveOptionsStore, useImportedSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { bindImportedSetup, type ImportedSetup } from './importedSetup';
import { buildImportedSubmission } from './importedSubmission';

beforeEach(() => {
  workspaceModeStore.setImportedInput('cad');
  resetCadReturnStore(); resetSolveOptionsStore(); resetImportedModelStore(); importedMeshStore.clear();
});

it('restores a CAD run and binds the retained setup to identical request bytes', async () => {
  const saved = workspaceFor();
  const fetcher = (async (url: RequestInfo | URL) => String(url).endsWith('/workspace')
    ? json(saved) : String(url).endsWith('/viewport-mesh') ? new Response(mesh) : json(record)) as typeof fetch;
  expect(await openImported({ kind: 'run', job: job() }, fetcher)).toBe(true);
  expect(unchangedSetup('cad', record.ingest_id)).toEqual({ setup_revision_id: 'wgs_archived' });
  const expected = bindImportedSetup(record, saved.setup.content as unknown as ImportedSetup);
  expect(JSON.stringify(buildImportedSubmission())).toBe(JSON.stringify(expected));
});

it('restores a saved CAD project and binds the same setup bytes', () => {
  const setup = workspaceFor().setup.content as unknown as ImportedSetup;
  useCadReturnStore.setState({ selectedBundle: bundle, ingestRecord: record, projectLineageId: 'wgl_project', needsIngest: false });
  restoreCadProjectSettings(setup, record, false, 'wgs_saved');
  restoreCadImportedSetup('wgl_project', record, setup, 'wgs_saved');
  expect(unchangedSetup('cad', record.ingest_id)).toEqual({ setup_revision_id: 'wgs_saved' });
  expect(JSON.stringify(buildImportedSubmission())).toBe(JSON.stringify(bindImportedSetup(record, setup)));
});

it('restores the CAD sweep instead of the stale solve-options range', async () => {
  const original = archivedRequest();
  const { frequencies_hz: _frequencies, ...otherOptions } = original.options;
  const request = { ...original, options: { ...otherOptions, frequency_range: [125, 8000], num_frequencies: 29 } };
  const saved = workspaceFor(record.ingest_id, request as never);
  const fetcher = (async (url: RequestInfo | URL) => String(url).endsWith('/workspace')
    ? json(saved) : String(url).endsWith('/viewport-mesh') ? new Response(mesh) : json(record)) as typeof fetch;
  useImportedSolveOptionsStore.setState({ sweepStartHz: 999, sweepEndHz: 1000, sweepCount: 2 });
  expect(await openImported({ kind: 'run', job: job() }, fetcher)).toBe(true);
  expect(useCadReturnStore.getState()).toMatchObject({ frequencyStartHz: 125, frequencyEndHz: 8000, frequencyCount: 29 });
  expect(JSON.stringify(buildImportedSubmission()))
    .toBe(JSON.stringify(bindImportedSetup(record, saved.setup.content as unknown as ImportedSetup)));
});

it('restores a legacy file setup and binds identical request bytes', async () => {
  const msh = new TextDecoder().decode(readFileSync('../server/tests/fixtures/mesh_import/tetra.msh'));
  const file: RegisteredMesh = { ingest_id: 'wgi_file', model_id: 'mesh-file:old', producer: 'mesh-file',
    manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact', msh_text: msh,
    source_file: { filename: 'old.msh', transform: { units: 'mm', forward: '+z', up: '+y' }, source_tags: [2] },
    source_tags: { group_2: 2 }, summary: { node_count: 4, triangle_count: 4, dimensions: [1, 1, 1], groups: [] } };
  const setup: ImportedSetup = { geometry: { skipped_source_ids: [],
    drive_channels: [{ id: 'group_2', source_ids: ['group_2'], motion: 'axial' }] },
    options: archivedRequest().options as unknown as ImportedSetup['options'] };
  workspaceModeStore.setImportedInput('file');
  await openImported({ kind: 'file', prepared: file, setup });
  importedMeshStore.setFile(createImportedMeshScene('old.msh', parseMSH(msh), 'file', file.ingest_id));
  expect(JSON.stringify(fileMeshSubmission())).toBe(JSON.stringify(bindImportedSetup(file, setup)));
});
