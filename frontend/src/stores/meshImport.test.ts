import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { workspaceModeStore } from './workspaceMode';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { editMeshImport, fileMeshSubmission, loadMeshFile, prepareMeshFile, loadMeshReplay, registeredMesh,
  saveMeshSetup, setMeshMotion, useMeshImportStore } from './meshImport';
import { openImported, reopenLastModel, resetImportedModelStore, unchangedSetup, useImportedModelStore } from './importedModel';
import { readLastModel } from './lastModel';
import type { RegisteredMesh, MeshSummary } from '../api/meshImport';
import type { ImportedSetup } from '../jobs/importedSetup';
import type { JobItem } from '../api/jobsSocket';
import { runSourceForJob } from '../jobs/exportNaming';
import { runMatchesContext, runContext } from '../results/runCoherence';

const MSH = new TextDecoder().decode(readFileSync('../server/tests/fixtures/mesh_import/tetra.msh'));
const summary: MeshSummary = { node_count: 4, triangle_count: 4, dimensions: [100, 100, 100], dimensions_mm: [100, 100, 100],
  groups: [{ tag: 1, name: 'Rigid', triangle_count: 3 }, { tag: 2, name: 'HF', triangle_count: 1 }] };
const record: RegisteredMesh = { ingest_id: 'wgi_file', model_id: 'mesh-file:sha256:artifact', producer: 'mesh-file', manifest_sha256: 'sha256:manifest',
  artifact_sha256: 'sha256:artifact', summary,
  source_file: { filename: 'speaker.msh', transform: { units: 'mm', forward: '+z', up: '+y' }, source_tags: [2] },
  source_tags: { group_2: 2 }, msh_text: MSH };
const initial = useMeshImportStore.getState();
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
/** What `/api/models/...` answers for a prepared mesh. */
const prepared = (mesh: RegisteredMesh) => response({ model: { model_id: mesh.model_id, producer: 'mesh-file', display_name: 'speaker.msh' },
  prepared: mesh, latest_prepared: mesh, original_available: true });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }

const fileJob = (id = 'file-job') => ({ id, run_number: 1, cad_source: { ingest_id: record.ingest_id },
  config_summary: { geometry_type: 'imported', import_origin: 'mesh-file' } }) as unknown as JobItem;
const setupOf = (options: Record<string, unknown>, motion: 'normal' | 'axial' = 'normal'): ImportedSetup => ({
  geometry: { skipped_source_ids: [], drive_channels: [{ id: 'group_2', source_ids: ['group_2'], motion }] },
  options: options as unknown as ImportedSetup['options'],
});
/** `GET /api/runs/{id}/workspace` for a file run. */
function workspaceFetcher(setup: ImportedSetup, mesh = record, { revision = 'wgs_recorded', original = true } = {}) {
  return vi.fn<typeof fetch>(async (url) => String(url).startsWith('/api/runs/') ? response({
    job_id: 'file-job', model: { model_id: mesh.model_id, producer: 'mesh-file', display_name: 'speaker.msh' },
    prepared: mesh, original_available: original,
    setup: { revision_id: revision, content: { schema_version: 1, ...setup } }, solve_options: setup.options,
  }) : prepared(mesh));
}
/** A prepared file on screen, as a registration leaves it. */
function showPrepared(mesh = record) {
  workspaceModeStore.setImportedInput('file');
  void openImported({ kind: 'file', prepared: mesh });
  importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', mesh.ingest_id));
}

beforeEach(() => {
  useMeshImportStore.setState(initial, true);
  importedMeshStore.clear();
  workspaceModeStore.setMode('parametric');
  resetSolveOptionsStore();
  resetImportedModelStore();
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

describe('standalone imported mesh', () => {
  it('saves a prepared setup before any Solve and reopens its model with it', async () => {
    showPrepared();
    useMeshImportStore.setState({ motions: { group_2: 'axial' } });
    useImportedSolveOptionsStore.getState().setSweep({ startHz: 250, endHz: 8000, count: 17 });
    const expected = fileMeshSubmission();
    const save = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_saved' }));
    await saveMeshSetup(save);
    const { prepared_id: savedFor, setup } = JSON.parse(save.mock.calls[0][1]?.body as string);
    expect(savedFor).toBe(record.ingest_id);
    expect(save.mock.calls[0][0]).toBe(`/api/models/${encodeURIComponent(record.model_id)}/setup`);
    expect(setup.geometry).not.toHaveProperty('ingest_id');
    expect(setup.geometry).not.toHaveProperty('mesh');
    useMeshImportStore.setState(initial, true);
    resetImportedModelStore();
    importedMeshStore.clear();
    const fetcher = vi.fn<typeof fetch>(async url => String(url).includes('/setup?')
      ? response({ setup, revision_id: 'wgs_saved' }) : prepared(record));
    // Showing the prepared mesh pointed the last model at it; saving needs no pointer of its own.
    expect(readLastModel('file')).toEqual({ model_id: record.model_id, input: 'file' });
    workspaceModeStore.setMode('cad');
    expect(await reopenLastModel(fetcher)).toBe(true);
    expect(fetcher.mock.calls.map(([url]) => String(url))).toContain(`/api/models/${encodeURIComponent(record.model_id)}`);
    expect(fileMeshSubmission()).toEqual(expected);
    // Reopened unedited, a Solve names the saved revision.
    expect(unchangedSetup('file', record.ingest_id)).toEqual({ setup_revision_id: 'wgs_saved' });
  });

  it('does not mark settings edited during or after saving as saved', async () => {
    showPrepared();
    const wait = deferred<Response>();
    const saving = saveMeshSetup(vi.fn(() => wait.promise));
    useImportedSolveOptionsStore.getState().setSweep({ count: 17 });
    wait.resolve(response({ revision_id: 'wgs_old' }));
    await saving;
    expect(useMeshImportStore.getState().notice).toContain('current edits are not saved');
    await saveMeshSetup(vi.fn(async () => response({ revision_id: 'wgs_new' })));
    expect(useMeshImportStore.getState().notice).toContain('Mesh setup saved with this model.');
    useImportedSolveOptionsStore.getState().setSweep({ count: 19 });
    expect(useMeshImportStore.getState().notice).toBeNull();
  });

  it('tracks borrowed engine changes during Save', async () => {
    showPrepared();
    const wait = deferred<Response>();
    const saving = saveMeshSetup(vi.fn(() => wait.promise));
    useImportedSolveOptionsStore.getState().setEngine('bempp');
    wait.resolve(response({ revision_id: 'wgs_old' }));
    await saving;
    expect(useMeshImportStore.getState().notice).toContain('current edits are not saved');
  });

  it('inspects original bytes on the backend, then prepares explicit units and groups', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => response(summary));
    await loadMeshFile('speaker.msh', MSH, fetcher);
    expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ filename: 'speaker.msh', msh_text: MSH });
    expect(() => fileMeshSubmission()).toThrow('Choose units');
    editMeshImport({ units: 'mm', selected: [2] });
    const register = vi.fn<typeof fetch>(async () => prepared(record));
    await prepareMeshFile(register);
    expect(register.mock.calls[0][0]).toBe('/api/models/files');
    expect(JSON.parse(register.mock.calls[0][1]?.body as string)).toMatchObject({ units: 'mm', source_tags: [2], msh_text: MSH });
    expect(importedMeshStore.getSnapshot().file?.ingestId).toBe(record.ingest_id);
    // A registration fills the store: the file's model and its prepared mesh.
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'file', modelId: record.model_id, phase: 'ready',
      fileDraft: { units: 'mm', forward: '+z', up: '+y', selected: [2] }, restoredRevisionId: null });
    const body = fileMeshSubmission();
    expect(body.geometry).not.toHaveProperty('mesh');
    expect(body.geometry.drive_channels).toEqual([{ id: 'group_2', source_ids: ['group_2'], motion: 'normal' }]);
    expect(body.options.symmetry).toBe('full');
    // No hard-coded rig: the imported instance's polar settings and spacing go out.
    expect(body.options.polar_config.angle_range).toEqual([0, 180, 37]);
    expect(body.options.frequency_spacing).toBe('log');
    editMeshImport({ units: 'in' });
    expect(() => fileMeshSubmission()).toThrow('Choose units');
  });

  it('solves with the imported options\' polar settings and frequency spacing, never the parametric ones', async () => {
    await loadMeshFile('speaker.msh', MSH, vi.fn<typeof fetch>(async () => response(summary)));
    editMeshImport({ units: 'mm', selected: [2] });
    await prepareMeshFile(vi.fn<typeof fetch>(async () => prepared(record)));
    const parametricBefore = JSON.stringify(useSolveOptionsStore.getState().options());
    useImportedSolveOptionsStore.getState().setFrequencySpacing('linear');
    useImportedSolveOptionsStore.getState().updatePolar({
      angleStart: -90, angleEnd: 90, angleStep: 10, distance: 3.5, normAngle: 15, diagonalAngle: 30,
      enabledAxes: ['horizontal'], observationOrigin: 'throat', sphericalSampling: true, fieldPlane: false,
    });
    const { options } = fileMeshSubmission();
    expect(options.frequency_spacing).toBe('linear');
    expect(options.polar_config).toEqual({
      angle_range: [-90, 90, 19], angle_step: 10, enabled_axes: ['horizontal'], distance: 3.5, norm_angle: 15,
      inclination: 30, observation_origin: 'throat', spherical_sampling: true, field_plane: false,
    });
    expect(JSON.stringify(useSolveOptionsStore.getState().options())).toBe(parametricBefore);
  });

  it('honours list-mode frequencies and range-mode edits in the submitted request', async () => {
    showPrepared();
    const imported = useImportedSolveOptionsStore.getState();
    imported.setSweep({ startHz: 120, endHz: 4000, count: 9 });
    expect(fileMeshSubmission().options).toMatchObject({ frequency_range: [120, 4000], num_frequencies: 9 });
    imported.setFrequencyMode('list');
    imported.setFrequencyListText('300 700 1500');
    const listed = fileMeshSubmission().options;
    expect(listed.frequencies_hz).toEqual([300, 700, 1500]);
    expect(listed).not.toHaveProperty('frequency_range');
    imported.setFrequencyListText('not a list');
    expect(() => fileMeshSubmission()).toThrow('Frequency list is not usable');
    imported.setFrequencyMode('range');
    imported.setSweep({ count: 1 });
    expect(() => fileMeshSubmission()).toThrow('2–401');
  });

  it('reopens an old saved file setup that carried a range and count as the same frequencies', async () => {
    showPrepared();
    const saved = fileMeshSubmission();
    const { type: _t, ingest_id: _i, manifest_sha256: _m, artifact_sha256: _a, acknowledged_findings: _f, ...geometry } = saved.geometry;
    // An old setup: the range and count were stored in the options, and nothing else about the sweep.
    const options = { ...saved.options, frequency_range: [210, 6300] as [number, number], num_frequencies: 23 };
    useMeshImportStore.setState(initial, true);
    resetImportedModelStore();
    importedMeshStore.clear();
    resetSolveOptionsStore();
    localStorage.removeItem('wg2.last.model.v1');
    localStorage.setItem('wg-saved-mesh-setup', JSON.stringify({ model_id: record.model_id, prepared_id: record.ingest_id }));
    const fetcher = vi.fn(async (url: RequestInfo | URL) => String(url).includes('/setup?') ? response({ setup: { geometry, options } }) : prepared(record));
    workspaceModeStore.setMode('cad');
    expect(await reopenLastModel(fetcher as unknown as typeof fetch)).toBe(true);
    expect(localStorage.getItem('wg-saved-mesh-setup')).toBeNull();
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ sweepStartHz: 210, sweepEndHz: 6300, sweepCount: 23, frequencyMode: 'range' });
    expect(fileMeshSubmission().options).toMatchObject({ frequency_range: [210, 6300], num_frequencies: 23 });
  });

  it('never sends a ground plane for a file mesh, whatever the parametric options say', async () => {
    useSolveOptionsStore.getState().updateGroundPlane({ enabled: true, height_m: 1 });
    await loadMeshFile('speaker.msh', MSH, vi.fn<typeof fetch>(async () => response(summary)));
    expect(useImportedSolveOptionsStore.getState().groundPlane.enabled).toBe(false);
    showPrepared();
    // Even if something enabled it on the shared instance, the request drops it.
    useImportedSolveOptionsStore.getState().updateGroundPlane({ enabled: true });
    expect(fileMeshSubmission().options).not.toHaveProperty('ground_plane');
    useSolveOptionsStore.getState().updateGroundPlane({ enabled: false });
  });

  it('resolves a pointer from before model records to its model, once', async () => {
    showPrepared();
    const { type: _t, ingest_id: _i, manifest_sha256: _m, artifact_sha256: _a, acknowledged_findings: _f, ...geometry } = fileMeshSubmission().geometry;
    const options = fileMeshSubmission().options;
    useMeshImportStore.setState(initial, true);
    resetImportedModelStore();
    importedMeshStore.clear();
    localStorage.removeItem('wg2.last.model.v1');
    localStorage.setItem('wg-saved-mesh-setup', record.ingest_id);
    const fetcher = vi.fn(async (url: RequestInfo | URL) => String(url).startsWith('/api/models?')
      ? response({ model_id: record.model_id, prepared_id: record.ingest_id })
      : String(url).includes('/setup?') ? response({ setup: { geometry, options } }) : prepared(record));
    workspaceModeStore.setMode('cad');
    expect(await reopenLastModel(fetcher as unknown as typeof fetch)).toBe(true);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/models?prepared_id=${record.ingest_id}`);
    expect(localStorage.getItem('wg-saved-mesh-setup')).toBeNull();
    expect(readLastModel('file')).toEqual({ model_id: record.model_id, input: 'file' });
    expect(registeredMesh()?.ingest_id).toBe(record.ingest_id);
  });

  it('drops an old pointer only when the server has no model for it', async () => {
    localStorage.setItem('wg-saved-mesh-setup', 'wgi_offline');
    workspaceModeStore.setMode('cad');
    expect(await reopenLastModel(vi.fn(async () => { throw new TypeError('offline'); }) as unknown as typeof fetch)).toBe(false);
    expect(localStorage.getItem('wg-saved-mesh-setup')).toBe('wgi_offline');
    const gone = vi.fn(async () => new Response('{}', { status: 404 }));
    expect(await reopenLastModel(gone as unknown as typeof fetch)).toBe(false);
    expect(gone).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('wg-saved-mesh-setup')).toBeNull();
    expect(readLastModel('file')).toBeNull();
  });

  it('opens a new file with a one-way copy of the parametric options', async () => {
    useSolveOptionsStore.getState().setFrequencySpacing('linear');
    useSolveOptionsStore.getState().updatePolar({ distance: 4 });
    await loadMeshFile('speaker.msh', MSH, vi.fn<typeof fetch>(async () => response(summary)));
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ frequencySpacing: 'linear', polar: { distance: 4 } });
    useImportedSolveOptionsStore.getState().updatePolar({ distance: 7 });
    expect(useSolveOptionsStore.getState().polar.distance).toBe(4);
  });

  it('never lets a late inspection replace a newer viewport choice', async () => {
    const wait = deferred<Response>();
    const loading = loadMeshFile('old.msh', MSH, vi.fn(() => wait.promise));
    importedMeshStore.showParametric();
    wait.resolve(response(summary));
    await loading;
    expect(importedMeshStore.getSnapshot().showing).toBe('parametric');
    expect(importedMeshStore.getSnapshot().file).toBeNull();
  });

  it('fences file reading before the parser starts', async () => {
    const generation = importedMeshStore.beginIntent();
    importedMeshStore.showParametric();
    const fetcher = vi.fn();
    await loadMeshFile('late-read.msh', MSH, fetcher, generation);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('refuses a preparation whose group choices changed while the backend ran', async () => {
    await loadMeshFile('speaker.msh', MSH, vi.fn(async () => response(summary)));
    editMeshImport({ units: 'mm', selected: [2] });
    const wait = deferred<Response>();
    const pending = prepareMeshFile(vi.fn(() => wait.promise));
    editMeshImport({ selected: [1] });
    wait.resolve(prepared(record));
    await pending;
    expect(registeredMesh()).toBeNull();
    expect(useMeshImportStore.getState().busy).toBe(false);
    expect(() => fileMeshSubmission()).toThrow('Choose units');
  });

  it('does not submit a prepared record while a different file occupies the viewport', () => {
    showPrepared();
    importedMeshStore.setFile(createImportedMeshScene('different.msh', parseMSH(MSH), 'file', 'wgi_other'));
    expect(() => fileMeshSubmission()).toThrow('Choose units');
  });
});

describe('a reopened file run', () => {
  it('opens the retained file in file mode, with no CAD identity or original file', async () => {
    const job = fileJob();
    const fetcher = workspaceFetcher(setupOf({ frequencies_hz: [100, 200] }));
    expect(await openImported({ kind: 'run', job }, fetcher)).toBe(true);
    expect(fetcher.mock.calls[0][0]).toBe('/api/runs/file-job/workspace');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(workspaceModeStore.getSnapshot().importedInput).toBe('file');
    expect(useMeshImportStore.getState().original).toBeNull();
    expect(fileMeshSubmission().options.frequencies_hz).toEqual([100, 200]);
    expect(runSourceForJob(job)).toBe('mesh-file');
    expect(runMatchesContext(job, runContext())).toBe('current');
    expect(runMatchesContext({ ...job, config_summary: {} }, runContext())).toBe('other-model');
  });

  it('is editable: it is prepared again from the original the server retained', async () => {
    await openImported({ kind: 'run', job: fileJob() }, workspaceFetcher(setupOf({})));
    expect(useMeshImportStore.getState().original).toBeNull();
    expect(useImportedModelStore.getState()).toMatchObject({ originalAvailable: true, restoredRevisionId: 'wgs_recorded',
      fileDraft: { units: 'mm', forward: '+z', up: '+y', selected: [2] } });
    editMeshImport({ units: 'cm' });
    // Changing the preparation ends the opened setup.
    expect(useImportedModelStore.getState()).toMatchObject({ prepared: null, restoredRevisionId: null, setup: null });
    const again = { ...record, ingest_id: 'wgi_again', source_file: { ...record.source_file, transform: { units: 'cm', forward: '+z', up: '+y' } } };
    const fetcher = vi.fn<typeof fetch>(async () => prepared(again));
    await prepareMeshFile(fetcher);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/models/${encodeURIComponent(record.model_id)}/prepare`);
    expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ units: 'cm', forward: '+z', up: '+y', source_tags: [2] });
    expect(useImportedModelStore.getState()).toMatchObject({ prepared: again, modelId: record.model_id });
    expect(fileMeshSubmission().geometry.ingest_id).toBe('wgi_again');
  });

  it('opens generated sweep, axial motion and solver settings without borrowing the open design', async () => {
    const setup = setupOf({ frequencies_hz: null, frequency_range: [250, 8000], num_frequencies: 17, engine: 'metal', accuracy: 'accurate' }, 'axial');
    await openImported({ kind: 'run', job: fileJob() }, workspaceFetcher(setup));
    const body = fileMeshSubmission();
    expect(body.options).toMatchObject({ frequency_range: [250, 8000], num_frequencies: 17, engine: 'metal', accuracy: 'accurate' });
    expect(body.geometry.drive_channels[0].motion).toBe('axial');
  });

  it('shows excitation by registered tag instead of trusting a group named HF', async () => {
    const selectedRigid = { ...record, source_tags: { group_1: 1 } };
    await openImported({ kind: 'run', job: fileJob() }, workspaceFetcher(setupOf({}), selectedRigid));
    const surfaces = importedMeshStore.getSnapshot().file!.scene.surfaces;
    expect(surfaces.find(surface => surface.role.includes('Rigid'))?.materialClass).toBe('source-smooth');
    expect(surfaces.find(surface => surface.role.includes('HF'))?.materialClass).toBe('horn-smooth');
  });

  it('keeps the setup revision the run was made from until a setting is edited', async () => {
    await openImported({ kind: 'run', job: fileJob() }, workspaceFetcher(setupOf({ frequencies_hz: [100, 200] })));
    expect(unchangedSetup('file', record.ingest_id)).toEqual({ setup_revision_id: 'wgs_recorded' });
    // Parametric edits are not this model's.
    useSolveOptionsStore.getState().updatePolar({ distance: 9 });
    expect(unchangedSetup('file', record.ingest_id)).toEqual({ setup_revision_id: 'wgs_recorded' });
    // Late hydration and automatic transitions are not edits either.
    useImportedSolveOptionsStore.setState({ engine: 'bempp' });
    expect(unchangedSetup('file', record.ingest_id)).not.toBeNull();
    setMeshMotion('group_2', 'axial');
    expect(unchangedSetup('file', record.ingest_id)).toBeNull();
  });

  it('refuses a run whose retained mesh is gone, with the server\'s words', async () => {
    showPrepared();
    const gone = vi.fn<typeof fetch>(async () => response({ job_id: 'file-job', original_available: false,
      refusal: { code: 'prepared_unavailable', message: 'This run\'s prepared mesh is no longer retained. Import the geometry again.' } }));
    await expect(openImported({ kind: 'run', job: fileJob() }, gone)).rejects.toThrow('no longer retained');
    expect(useMeshImportStore.getState().busy).toBe(false);
    expect(useMeshImportStore.getState().error).toContain('no longer retained');
    expect(() => fileMeshSubmission()).toThrow('no longer retained');
  });

  it('refuses a response naming another prepared mesh', async () => {
    const job = fileJob();
    const fetcher = vi.fn<typeof fetch>(async () => response({ job_id: job.id, original_available: true,
      prepared: { ...record, producer: 'cad' }, setup: { revision_id: 'wgs', content: setupOf({}) } }));
    await expect(openImported({ kind: 'run', job }, fetcher)).rejects.toThrow(/another kind/);
  });

  it('ignores a run whose workspace arrives after navigation', async () => {
    const wait = deferred<Response>();
    const opening = openImported({ kind: 'run', job: fileJob() }, vi.fn(() => wait.promise));
    importedMeshStore.showParametric();
    wait.resolve(await workspaceFetcher(setupOf({}))('/api/runs/file-job/workspace'));
    expect(await opening).toBe(false);
    expect(importedMeshStore.getSnapshot().showing).toBe('parametric');
  });
});

describe('portable mesh replay', () => {
  function exactRequest() {
    showPrepared();
    const request = fileMeshSubmission();
    request.options.frequencies_hz = [137, 211, 3987];
    request.options.polar_config = { ...request.options.polar_config, distance: 3.7, norm_angle: 17, enabled_axes: ['vertical'] };
    request.geometry.drive_voltage_v = 4.2;
    return request;
  }
  const setupOfRequest = (request: ReturnType<typeof fileMeshSubmission>) => {
    const { type: _t, ingest_id: _i, manifest_sha256: _m, artifact_sha256: _a, acknowledged_findings: _f, ...geometry } = request.geometry;
    return { schema_version: 1, geometry, options: request.options };
  };

  it('solves the complete request until a setting is edited', async () => {
    const request = exactRequest();
    const opening = vi.fn(async () => response({ record, request }));
    expect(await loadMeshReplay('{"version":1}', opening)).toBe(true);
    expect(opening.mock.calls[0]).toEqual(['/api/mesh-replay', expect.objectContaining({ body: '{"version":1}' })]);
    // No revision is known for a replay: an unedited Solve sends its exact setup.
    expect(unchangedSetup('file', record.ingest_id)).toEqual({ setup: setupOfRequest(request) });
    expect(fileMeshSubmission().options.frequencies_hz).toEqual([137, 211, 3987]);
    useImportedSolveOptionsStore.getState().updatePolar({ distance: 9 });
    expect(unchangedSetup('file', record.ingest_id)).toBeNull();
    // The replayed list is the imported options' own, so it carries over to the new setup.
    expect(fileMeshSubmission().options.frequencies_hz).toEqual([137, 211, 3987]);
  });

  it('keeps exact replay through an edit to the parametric options', async () => {
    const request = exactRequest();
    await loadMeshReplay('{}', vi.fn(async () => response({ record, request })));
    useSolveOptionsStore.getState().updatePolar({ distance: 9 });
    useSolveOptionsStore.getState().setEngine('bempp');
    expect(unchangedSetup('file', record.ingest_id)).toEqual({ setup: setupOfRequest(request) });
  });

  it('does not let a delayed replay replace a newer viewport choice', async () => {
    const request = exactRequest();
    const wait = deferred<Response>();
    const opening = loadMeshReplay('{}', vi.fn(() => wait.promise));
    importedMeshStore.showParametric();
    wait.resolve(response({ record, request }));
    expect(await opening).toBe(false);
    expect(importedMeshStore.getSnapshot().showing).toBe('parametric');
    expect(useMeshImportStore.getState().busy).toBe(false);
  });

  it('rejects a response naming another retained mesh before changing the viewport', async () => {
    const request = exactRequest();
    await expect(loadMeshReplay('{}', vi.fn(async () => response({ record: { ...record, ingest_id: 'other' }, request })))).rejects.toThrow('does not match');
    expect(importedMeshStore.getSnapshot().file?.ingestId).toBe(record.ingest_id);
  });
});
