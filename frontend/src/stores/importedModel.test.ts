import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import type { RegisteredMesh } from '../api/meshImport';
import { record as cadRecord } from '../shell/cadlink/selection.fixtures';
import { importedMeshStore } from '../viewport/importedMeshStore';
import {
  applyCadSolveSelection, cadSolveSelection, discardParkedCadModel, importedModelBlocker, openImported,
  reopenLastModel, resetImportedModelStore, unchangedSetup, useImportedModelStore,
} from './importedModel';
import { migrateLastModel, readLastModel, rememberLastModel } from './lastModel';
import { useMeshImportStore } from './meshImport';
import { useCadReturnStore } from './cadReturn';
import { noteSolveSettingsEdit } from './solveSettingsEdits';
import '../shell/cadlink/restores';
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

  it('keeps an opened CAD setup when the same snapshot arrives again, not when another does', async () => {
    useImportedModelStore.setState({ input: 'cad', phase: 'ready', modelId: 'wgl_speaker', prepared: cadRecord,
      setup: setup as never, restoredRevisionId: 'wgs_cad' });
    await openImported({ kind: 'cad', record: { ...cadRecord, model_id: 'wgl_speaker' } });
    expect(unchangedSetup('cad', cadRecord.ingest_id)).toEqual({ setup_revision_id: 'wgs_cad' });
    await openImported({ kind: 'cad', record: { ...cadRecord, ingest_id: 'wgi_newer', model_id: 'wgl_speaker' } });
    expect(useImportedModelStore.getState()).toMatchObject({ restoredRevisionId: null, setup: null });
  });

  it('fences a CAD open by the open, so a file opened meanwhile keeps its own state', async () => {
    const ingest = deferred<Response>();
    const cadRun = { id: 'cad-run', run_number: 3, config_summary: { geometry_type: 'imported' },
      cad_source: { ingest_id: cadRecord.ingest_id } } as unknown as JobItem;
    const showIngestedMesh = vi.fn(async () => undefined);
    const cadFetcher = vi.fn<typeof fetch>(async (url) => String(url).endsWith('/workspace') ? json({
      job_id: 'cad-run', model: { model_id: 'wgl_a', producer: 'cad', display_name: 'A' },
      prepared: { ingest_id: cadRecord.ingest_id, model_id: 'wgl_a', producer: 'cad',
        manifest_sha256: cadRecord.manifest_sha256, artifact_sha256: cadRecord.artifact_sha256 },
      original_available: false, setup: { revision_id: 'wgs_a', content: { schema_version: 1,
        geometry: { drive_channels: [{ id: 'drive-hf', source_ids: ['source-hf'] }], skipped_source_ids: [] },
        options: { engine: 'beat-cpu', frequencies_hz: [111, 222] } } },
    }) : ingest.promise);
    const cadOpen = openImported({ kind: 'run', job: cadRun, display: { showIngestedMesh } }, cadFetcher);
    await vi.waitFor(() => expect(cadFetcher).toHaveBeenCalledTimes(2));
    expect(await openImported({ kind: 'run', job: run() }, vi.fn<typeof fetch>(async () => json(workspace())))).toBe(true);
    const fileOptions = JSON.stringify(useImportedSolveOptionsStore.getState().options());
    const cadBefore = useCadReturnStore.getState();
    ingest.resolve(json(cadRecord));
    expect(await cadOpen).toBe(false);
    expect(JSON.stringify(useImportedSolveOptionsStore.getState().options())).toBe(fileOptions);
    expect(useImportedSolveOptionsStore.getState().frequencyListText).toBe('100\n200\n400');
    expect(useCadReturnStore.getState()).toBe(cadBefore);
    expect(showIngestedMesh).not.toHaveBeenCalled();
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'file', restoredRevisionId: 'wgs_run' });
  });

  it('fills the rail from a setup that is not the run\'s exactly, but does not replay it', async () => {
    const lossy = vi.fn<typeof fetch>(async () => json(workspace({ setup: { revision_id: 'wgs_rebuilt', content: setup,
      derived: true, exact: false, note: 'The settings this run recorded are no longer retained.' } })));
    expect(await openImported({ kind: 'run', job: run() }, lossy)).toBe(true);
    expect(useImportedSolveOptionsStore.getState().frequencySpacing).toBe('linear');
    expect(unchangedSetup('file', mesh.ingest_id)).toBeNull();
    expect(useMeshImportStore.getState().notice).toContain('no longer retained');
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

  it('drops CAD\'s parked model and options when another selection is made while a file is on screen', async () => {
    await openImported({ kind: 'cad', record: { ...cadRecord, model_id: 'wgl_old' } });
    useImportedSolveOptionsStore.setState({ engine: 'beat-cpu' });
    workspaceModeStore.setImportedInput('file');
    expect(cadSolveSelection()).toMatchObject({ engine: 'beat-cpu' });
    discardParkedCadModel();
    expect(cadSolveSelection()).toBeNull();
    useSolveOptionsStore.setState({ engine: 'bempp' });
    workspaceModeStore.setImportedInput('cad');
    // Seeded afresh from the parametric options, and the old model is gone.
    expect(useImportedSolveOptionsStore.getState().engine).toBe('bempp');
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'cad', modelId: null, phase: 'empty' });
  });

  it('ends a parked setup when its own input is edited, never the one on screen', async () => {
    await openImported({ kind: 'run', job: run() }, vi.fn<typeof fetch>(async () => json(workspace())));
    workspaceModeStore.setImportedInput('cad');
    // A CAD-rail edit while CAD is on screen is CAD's: the parked file setup stays.
    noteSolveSettingsEdit({ control: 'setDriveVoltage' });
    workspaceModeStore.setImportedInput('file');
    expect(unchangedSetup('file', mesh.ingest_id)).toEqual({ setup_revision_id: 'wgs_run' });
    // A file-rail edit made while CAD is on screen ends the parked file setup.
    workspaceModeStore.setImportedInput('cad');
    noteSolveSettingsEdit({ source: 'meshImport', control: 'motion' });
    workspaceModeStore.setImportedInput('file');
    expect(unchangedSetup('file', mesh.ingest_id)).toBeNull();
  });
});

describe('the last model', () => {
  const modelRead = () => ({ model: { model_id: mesh.model_id, producer: 'mesh-file', display_name: 'speaker.msh' },
    prepared: mesh, latest_prepared: mesh, original_available: true });
  const fileFetcher = () => vi.fn<typeof fetch>(async (url) => String(url).includes('/setup')
    ? json({ setup, revision_id: 'wgs_saved' }) : json(modelRead()));
  beforeEach(() => workspaceModeStore.setImportedInput('file'));
  const gone = () => vi.fn<typeof fetch>(async () => json({ error: { code: 'model_not_found', message: 'Imported model not found.' } }, 404));

  it('points at the model on screen once it is ready, and at no unfiled mesh', async () => {
    workspaceModeStore.setImportedInput('file');
    await openImported({ kind: 'file', prepared: { ...mesh, model_id: null } });
    expect(readLastModel('file')).toBeNull();
    await openImported({ kind: 'file', prepared: mesh });
    expect(readLastModel('file')).toEqual({ model_id: mesh.model_id, input: 'file' });
  });

  it('reopens a file model in Imported mode through openImported', async () => {
    rememberLastModel({ model_id: mesh.model_id, input: 'file' });
    workspaceModeStore.setMode('cad');
    const fetcher = fileFetcher();
    expect(await reopenLastModel(fetcher)).toBe(true);
    expect(useImportedModelStore.getState()).toMatchObject({ phase: 'ready', input: 'file', modelId: mesh.model_id, restoredRevisionId: 'wgs_saved' });
    expect(workspaceModeStore.getSnapshot()).toMatchObject({ mode: 'cad', importedInput: 'file' });
  });

  it('opens nothing and does not change the mode in Parametric', async () => {
    rememberLastModel({ model_id: mesh.model_id, input: 'file' });
    const fetcher = fileFetcher();
    expect(await reopenLastModel(fetcher)).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(useImportedModelStore.getState().phase).toBe('empty');
    expect(readLastModel('file')).not.toBeNull();
  });

  it('does not open over a model the person already opened', async () => {
    rememberLastModel({ model_id: 'mesh-file:sha256:other', input: 'file' });
    workspaceModeStore.setMode('cad');
    workspaceModeStore.setImportedInput('file');
    await openImported({ kind: 'file', prepared: mesh });
    const fetcher = fileFetcher();
    expect(await reopenLastModel(fetcher)).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('clears a missing model once and leaves the empty Imported state, with no error on the next start', async () => {
    rememberLastModel({ model_id: 'mesh-file:sha256:gone', input: 'file' });
    workspaceModeStore.setMode('cad');
    const first = gone();
    expect(await reopenLastModel(first)).toBe(false);
    expect(first).toHaveBeenCalledTimes(1);
    expect(readLastModel('file')).toBeNull();
    expect(useImportedModelStore.getState()).toMatchObject({ phase: 'empty', error: null });
    expect(useMeshImportStore.getState().error).toBeNull();
    const second = gone();
    expect(await reopenLastModel(second)).toBe(false);
    expect(second).not.toHaveBeenCalled();
  });

  it('clears a model that keeps no preparation, but keeps the pointer through a transport failure', async () => {
    rememberLastModel({ model_id: mesh.model_id, input: 'file' });
    workspaceModeStore.setMode('cad');
    await expect(reopenLastModel(vi.fn<typeof fetch>(async () => { throw new TypeError('offline'); }))).rejects.toThrow('offline');
    expect(readLastModel('file')).not.toBeNull();
    expect(await reopenLastModel(vi.fn<typeof fetch>(async () => json({ ...modelRead(), prepared: undefined, latest_prepared: null })))).toBe(false);
    expect(readLastModel('file')).toBeNull();
  });

  it('leaves a CAD model that was never solved alone: nothing to show, nothing to clear', async () => {
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' });
    workspaceModeStore.setMode('cad');
    workspaceModeStore.setImportedInput('cad');
    const fetcher = vi.fn<typeof fetch>(async (url) => String(url).includes('/setup')
      ? json({ setup: null }) : json({ model: { model_id: 'wgl_project', producer: 'cad', display_name: 'Speaker' }, latest_prepared: { ...mesh, producer: 'cad' }, original_available: false }));
    expect(await reopenLastModel(fetcher)).toBe(false);
    expect(useImportedModelStore.getState().phase).toBe('empty');
    expect(readLastModel('cad')).toEqual({ model_id: 'wgl_project', input: 'cad' });
  });

  it('clears a broken model after reporting it once, so the next start does not fail again', async () => {
    rememberLastModel({ model_id: mesh.model_id, input: 'file' });
    workspaceModeStore.setMode('cad');
    const broken = () => vi.fn<typeof fetch>(async () => json({ error: { code: 'internal', message: 'The mesh store is damaged.' } }, 500));
    await expect(reopenLastModel(broken())).rejects.toThrow('damaged');
    expect(readLastModel('file')).toBeNull();
    const next = broken();
    expect(await reopenLastModel(next)).toBe(false);
    expect(next).not.toHaveBeenCalled();
  });

  it('clears a model whose preparation is unavailable, and one whose open fails on missing artifacts', async () => {
    rememberLastModel({ model_id: mesh.model_id, input: 'file' });
    workspaceModeStore.setMode('cad');
    expect(await reopenLastModel(vi.fn<typeof fetch>(async () =>
      json({ error: { code: 'prepared_unavailable', message: 'gone' } }, 409)))).toBe(false);
    expect(readLastModel('file')).toBeNull();
    rememberLastModel({ model_id: mesh.model_id, input: 'file' });
    await expect(reopenLastModel(vi.fn<typeof fetch>(async (url) => String(url).includes('/setup')
      ? json({ setup, revision_id: 'x' }) : json({ ...modelRead(), prepared: undefined, latest_prepared: { ...mesh, msh_text: undefined } })))).rejects.toThrow();
    expect(readLastModel('file')).toBeNull();
  });

  it('reopens the pointer of the input the person last chose and never flips it', async () => {
    rememberLastModel({ model_id: mesh.model_id, input: 'file' });
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' });
    workspaceModeStore.setMode('cad');
    workspaceModeStore.setImportedInput('file');
    expect(await reopenLastModel(fileFetcher())).toBe(true);
    expect(workspaceModeStore.getSnapshot().importedInput).toBe('file');
    expect(readLastModel('cad')).toEqual({ model_id: 'wgl_project', input: 'cad' });
  });

  it('hands a never-solved CAD model to the unsolved opener, and reports nothing when none is given', async () => {
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' });
    workspaceModeStore.setMode('cad');
    workspaceModeStore.setImportedInput('cad');
    const fetcher = vi.fn<typeof fetch>(async (url) => String(url).includes('/setup')
      ? json({ setup: null }) : json({ model: { model_id: 'wgl_project', producer: 'cad', display_name: 'Speaker' }, latest_prepared: { ...mesh, producer: 'cad' }, original_available: false }));
    const openUnsolved = vi.fn(async () => true);
    expect(await reopenLastModel(fetcher, { openUnsolved })).toBe(true);
    expect(openUnsolved).toHaveBeenCalledWith({ model_id: 'wgl_project', input: 'cad' }, expect.objectContaining({ ingest_id: mesh.ingest_id }));
    expect(await reopenLastModel(fetcher)).toBe(false);
    expect(readLastModel('cad')).not.toBeNull();
  });

  it('leaves the pointer to a guard that declines it', async () => {
    rememberLastModel({ model_id: mesh.model_id, input: 'file' });
    workspaceModeStore.setMode('cad');
    const fetcher = fileFetcher();
    expect(await reopenLastModel(fetcher, { shouldTry: () => false })).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  describe('migration', () => {
    it('moves the saved mesh setup pointer, then drops both old keys', async () => {
      localStorage.setItem('wg-saved-mesh-setup', JSON.stringify({ model_id: mesh.model_id, prepared_id: mesh.ingest_id }));
      localStorage.setItem('wg2.cad.project.v1', 'wgl_project');
      await migrateLastModel();
      expect(readLastModel('file')).toEqual({ model_id: mesh.model_id, input: 'file' });
      expect(localStorage.getItem('wg-saved-mesh-setup')).toBeNull();
      expect(localStorage.getItem('wg2.cad.project.v1')).toBeNull();
    });

    it('moves the remembered CAD project (a lineage is the CAD model id), keeping both inputs', async () => {
      localStorage.setItem('wg-saved-mesh-setup', JSON.stringify({ model_id: mesh.model_id, prepared_id: mesh.ingest_id }));
      localStorage.setItem('wg2.cad.project.v1', ' wgl_project ');
      await migrateLastModel();
      expect(readLastModel('cad')).toEqual({ model_id: 'wgl_project', input: 'cad' });
      expect(readLastModel('file')).toEqual({ model_id: mesh.model_id, input: 'file' });
    });

    it('reads the single pointer an earlier build wrote as that input\'s pointer', () => {
      localStorage.setItem('wg2.last.model.v1', JSON.stringify({ model_id: 'wgl_project', input: 'cad' }));
      expect(readLastModel('cad')).toEqual({ model_id: 'wgl_project', input: 'cad' });
      expect(readLastModel('file')).toBeNull();
    });

    it('resolves the bare prepared id form once, keeps it through a transport failure, and drops it when unknown', async () => {
      localStorage.setItem('wg-saved-mesh-setup', mesh.ingest_id);
      await migrateLastModel(vi.fn<typeof fetch>(async () => { throw new TypeError('offline'); }));
      expect(localStorage.getItem('wg-saved-mesh-setup')).toBe(mesh.ingest_id);
      expect(readLastModel('file')).toBeNull();
      await migrateLastModel(vi.fn<typeof fetch>(async () => json({ model_id: mesh.model_id, prepared_id: mesh.ingest_id })));
      expect(readLastModel('file')).toEqual({ model_id: mesh.model_id, input: 'file' });
      expect(localStorage.getItem('wg-saved-mesh-setup')).toBeNull();
      localStorage.setItem('wg-saved-mesh-setup', 'wgi_unknown');
      await migrateLastModel(vi.fn<typeof fetch>(async () => new Response('{}', { status: 404 })));
      expect(localStorage.getItem('wg-saved-mesh-setup')).toBeNull();
      expect(readLastModel('file')).toEqual({ model_id: mesh.model_id, input: 'file' });
    });

    it('an existing pointer wins over the old key for the same input only', async () => {
      rememberLastModel({ model_id: mesh.model_id, input: 'file' });
      localStorage.setItem('wg2.cad.project.v1', 'wgl_project');
      localStorage.setItem('wg-saved-mesh-setup', JSON.stringify({ model_id: 'mesh-file:sha256:older', prepared_id: 'x' }));
      await migrateLastModel();
      expect(readLastModel('file')?.model_id).toBe(mesh.model_id);
      expect(readLastModel('cad')?.model_id).toBe('wgl_project');
      expect(localStorage.getItem('wg2.cad.project.v1')).toBeNull();
    });
  });
});
