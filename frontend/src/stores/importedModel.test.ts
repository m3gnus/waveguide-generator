import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import type { RegisteredMesh } from '../api/meshImport';
import { record as cadRecord } from '../shell/cadlink/selection.fixtures';
import { importedMeshStore } from '../viewport/importedMeshStore';
import {
  applyCadSolveSelection, cadSolveSelection, discardParkedCadOptions, importedModelBlocker, openImported,
  resetImportedModelStore, unchangedSetup, useImportedModelStore,
} from './importedModel';
import { useMeshImportStore } from './meshImport';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { workspaceModeStore } from './workspaceMode';

const MSH = new TextDecoder().decode(readFileSync('../server/tests/fixtures/mesh_import/tetra.msh'));
const mesh: RegisteredMesh = { ingest_id: 'wgi_file', model_id: 'mesh-file:sha256:artifact', producer: 'mesh-file',
  manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact',
  summary: { node_count: 4, triangle_count: 4, dimensions: [1, 1, 1], groups: [{ tag: 2, name: 'HF', triangle_count: 1 }] },
  source_file: { filename: 'speaker.msh', transform: { units: 'cm', forward: '+x', up: '+z' }, source_tags: [2] },
  source_tags: { group_2: 2 }, msh_text: MSH };
const setup = { schema_version: 1, geometry: { skipped_source_ids: [], drive_channels: [{ id: 'group_2', source_ids: ['group_2'], motion: 'axial' }] },
  options: { engine: 'metal', frequencies_hz: [100, 200, 400], frequency_spacing: 'linear' } };
const run = (id = 'file-run') => ({ id, run_number: 7, config_summary: { geometry_type: 'imported', import_origin: 'mesh-file' },
  cad_source: { ingest_id: mesh.ingest_id } }) as unknown as JobItem;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const workspace = (overrides: Record<string, unknown> = {}) => ({
  job_id: 'file-run', model: { model_id: mesh.model_id, producer: 'mesh-file', display_name: 'speaker.msh' },
  prepared: mesh, original_available: true, setup: { revision_id: 'wgs_run', content: setup }, solve_options: setup.options,
  ...overrides,
});
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }

beforeEach(() => {
  localStorage.clear();
  importedMeshStore.clear();
  useMeshImportStore.setState({ filename: '', original: null, summary: null, motions: {}, busy: false, error: null, notice: null });
  workspaceModeStore.setMode('parametric');
  workspaceModeStore.setImportedInput('cad');
  resetSolveOptionsStore();
  resetImportedModelStore();
});
afterEach(() => vi.restoreAllMocks());

describe('openImported', () => {
  it('opens a run from its workspace alone: model, prepared mesh, original, draft and setup revision', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => json(workspace()));
    const opening = openImported({ kind: 'run', job: run() }, fetcher);
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'file', phase: 'loading', model: null });
    expect(importedModelBlocker('file')).toContain('finish loading');
    expect(await opening).toBe(true);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][0]).toBe('/api/runs/file-run/workspace');
    expect(useImportedModelStore.getState()).toMatchObject({
      input: 'file', phase: 'ready', modelId: mesh.model_id, model: { producer: 'mesh-file' },
      prepared: { ingest_id: mesh.ingest_id }, originalAvailable: true, restoredRevisionId: 'wgs_run',
      fileDraft: { units: 'cm', forward: '+x', up: '+z', selected: [2] },
    });
    expect(importedModelBlocker('file')).toBeNull();
    // The rail and the options are the model's own.
    expect(useMeshImportStore.getState()).toMatchObject({ filename: 'speaker.msh', motions: { group_2: 'axial' }, busy: false });
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ engine: 'metal', frequencyMode: 'list', frequencySpacing: 'linear' });
    expect(importedMeshStore.getSnapshot().file?.ingestId).toBe(mesh.ingest_id);
  });

  it('solves the opened revision until a person edits an imported setting, never for a parametric edit', async () => {
    await openImported({ kind: 'run', job: run() }, vi.fn<typeof fetch>(async () => json(workspace())));
    expect(unchangedSetup('file', mesh.ingest_id)).toEqual({ setup_revision_id: 'wgs_run' });
    expect(unchangedSetup('file', 'wgi_other')).toBeNull();
    expect(unchangedSetup('cad', mesh.ingest_id)).toBeNull();
    useSolveOptionsStore.getState().setFrequencySpacing('log');
    useImportedSolveOptionsStore.setState({ engine: 'bempp' }); // hydration, not an edit
    expect(unchangedSetup('file', mesh.ingest_id)).toEqual({ setup_revision_id: 'wgs_run' });
    useImportedSolveOptionsStore.getState().setFrequencySpacing('log');
    expect(unchangedSetup('file', mesh.ingest_id)).toBeNull();
    expect(useImportedModelStore.getState()).toMatchObject({ setup: null, restoredRevisionId: null, phase: 'ready' });
  });

  it('fails an open that a person edited while it loaded, rather than replacing the edit', async () => {
    const wait = deferred<Response>();
    const opening = openImported({ kind: 'run', job: run() }, vi.fn(() => wait.promise));
    useImportedSolveOptionsStore.getState().setFrequencySpacing('log');
    wait.resolve(json(workspace()));
    expect(await opening).toBe(false);
    expect(useImportedModelStore.getState().phase).toBe('failed');
    expect(importedModelBlocker('file')).toContain('Settings changed');
    // The rail stops waiting and says why.
    expect(useMeshImportStore.getState()).toMatchObject({ busy: false });
    expect(useMeshImportStore.getState().error).toContain('Settings changed');
  });

  it('turns the server\'s refusal into the blocker, and opens nothing for a parametric or unnumbered run', async () => {
    const refused = vi.fn<typeof fetch>(async () => json(workspace({ prepared: undefined,
      refusal: { code: 'prepared_unavailable', message: 'This run\'s prepared mesh is no longer retained.' } })));
    await expect(openImported({ kind: 'run', job: run() }, refused)).rejects.toThrow('no longer retained');
    expect(useImportedModelStore.getState().phase).toBe('failed');
    expect(importedModelBlocker('file')).toContain('no longer retained');
    expect(importedModelBlocker('cad')).toBeNull();
    const none = vi.fn();
    expect(await openImported({ kind: 'run', job: { ...run(), config_summary: { geometry_type: 'osse' } } as JobItem }, none)).toBe(false);
    expect(await openImported({ kind: 'run', job: { ...run(), run_number: null } as JobItem }, none)).toBe(false);
    expect(none).not.toHaveBeenCalled();
  });

  it('lets the newest open win', async () => {
    const first = deferred<Response>();
    const older = openImported({ kind: 'run', job: run('older') }, vi.fn(() => first.promise));
    const newer = { ...mesh, ingest_id: 'wgi_newer' };
    expect(await openImported({ kind: 'run', job: run('newer') },
      vi.fn<typeof fetch>(async () => json(workspace({ prepared: newer, setup: { revision_id: 'wgs_newer', content: setup } }))))).toBe(true);
    first.resolve(json(workspace()));
    expect(await older).toBe(false);
    expect(useImportedModelStore.getState()).toMatchObject({ prepared: { ingest_id: 'wgi_newer' }, restoredRevisionId: 'wgs_newer' });
  });

  it('opens a model by its id: its preparation and its saved setup', async () => {
    const fetcher = vi.fn<typeof fetch>(async (url) => String(url).includes('/setup?')
      ? json({ setup, revision_id: 'wgs_saved' })
      : json({ model: { model_id: mesh.model_id, producer: 'mesh-file', display_name: 'speaker.msh' },
        latest_prepared: mesh, prepared: mesh, original_available: false }));
    expect(await openImported({ kind: 'model', modelId: mesh.model_id, preparedId: mesh.ingest_id }, fetcher)).toBe(true);
    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      `/api/models/${encodeURIComponent(mesh.model_id)}?prepared_id=${mesh.ingest_id}`,
      `/api/models/${encodeURIComponent(mesh.model_id)}/setup?prepared_id=${mesh.ingest_id}`,
    ]);
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'file', originalAvailable: false, restoredRevisionId: 'wgs_saved' });
  });

  it('fills the store from a registration and from a CAD arrival, which restore no setup', async () => {
    workspaceModeStore.setImportedInput('file');
    await openImported({ kind: 'file', prepared: mesh });
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'file', phase: 'ready', modelId: mesh.model_id,
      originalAvailable: true, setup: null, restoredRevisionId: null, fileDraft: { units: 'cm' } });
    await openImported({ kind: 'file', prepared: null });
    expect(useImportedModelStore.getState()).toMatchObject({ phase: 'empty', prepared: null, modelId: null });
    workspaceModeStore.setImportedInput('cad');
    await openImported({ kind: 'cad', record: { ...cadRecord, model_id: '' } });
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'cad', phase: 'ready', modelId: 'unfiled', model: null,
      prepared: { ingest_id: cadRecord.ingest_id }, setup: null });
  });
});

describe('each input keeps its open model', () => {
  it('parks the leaving model with its options and reopens it on return', async () => {
    workspaceModeStore.setImportedInput('cad');
    await openImported({ kind: 'cad', record: { ...cadRecord, model_id: 'wgl_speaker' } });
    useImportedSolveOptionsStore.setState({ engine: 'beat-cpu', accuracy: 'fast', sweepCount: 33 });
    await openImported({ kind: 'run', job: run() }, vi.fn<typeof fetch>(async () => json(workspace())));
    expect(useImportedModelStore.getState().input).toBe('file');
    // CAD's selection, while the file is on screen, is its parked one.
    expect(cadSolveSelection()).toEqual({ engine: 'beat-cpu', accuracy: 'fast' });
    applyCadSolveSelection({ engine: 'beat-metal' });
    expect(useImportedSolveOptionsStore.getState().engine).toBe('metal'); // the file's own
    workspaceModeStore.setImportedInput('cad');
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'cad', modelId: 'wgl_speaker', phase: 'ready' });
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ engine: 'beat-metal', sweepCount: 33 });
    workspaceModeStore.setImportedInput('file');
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'file', modelId: mesh.model_id, restoredRevisionId: 'wgs_run' });
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ frequencyMode: 'list', frequencySpacing: 'linear' });
  });

  it('keeps a CAD return that arrives while a file is on screen for the CAD input', async () => {
    workspaceModeStore.setImportedInput('file');
    await openImported({ kind: 'file', prepared: mesh });
    await openImported({ kind: 'cad', record: { ...cadRecord, model_id: 'wgl_arrived' } });
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'file', modelId: mesh.model_id });
    workspaceModeStore.setImportedInput('cad');
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'cad', modelId: 'wgl_arrived' });
  });

  it('drops CAD\'s parked options when another project is selected while a file is on screen', async () => {
    useImportedSolveOptionsStore.setState({ engine: 'beat-cpu' });
    workspaceModeStore.setImportedInput('file');
    expect(cadSolveSelection()).toMatchObject({ engine: 'beat-cpu' });
    discardParkedCadOptions();
    expect(cadSolveSelection()).toBeNull();
    useSolveOptionsStore.setState({ engine: 'bempp' });
    workspaceModeStore.setImportedInput('cad');
    // Seeded afresh from the parametric options.
    expect(useImportedSolveOptionsStore.getState().engine).toBe('bempp');
  });
});
