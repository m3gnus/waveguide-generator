import { saveImportedSetup } from '../api/importedModels';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { workspaceModeStore } from './workspaceMode';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { editMeshImport, fileMeshSubmission, loadMeshFile, prepareMeshFile, loadMeshReplay, registeredMesh,
  fileMeshSetup, fileSetupForSave, useMeshImportStore } from './meshImport';
import { openImported, reopenLastModel, resetImportedModelStore, unchangedSetup, useImportedModelStore, editFileDraft } from './importedModel';
import { fileSimulationSetup, importedSimulationSetup } from './importedSimulationSetup';
import { readLastModel } from './lastModel';
import type { RegisteredMesh, MeshSummary } from '../api/meshImport';
import type { ImportedSetup } from '../jobs/importedSetup';
import { bindImportedSetup } from '../jobs/importedSetup';
import type { JobItem } from '../api/jobsSocket';
import { runSourceForJob } from '../jobs/exportNaming';
import { runVerdict, currentModel } from '../model/currentModel';

const MSH = new TextDecoder().decode(readFileSync('../server/tests/fixtures/mesh_import/tetra.msh'));
// Also validated by server/tests/test_imported_setup_edits.py using validate_setup.
const editContract = JSON.parse(new TextDecoder().decode(readFileSync('../server/tests/fixtures/imported_setup_edits.json'))) as {
  setups: Record<string, ImportedSetup>;
  edits: Array<{ name: string; base: string; edit: { source_id?: string; motions?: Array<'normal' | 'axial'>; selected?: number[] }; expected: ImportedSetup }>;
};
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

function editSourceMotion(sourceId: string, motion: 'normal' | 'axial') {
  const simulation = fileSimulationSetup.get();
  const channel = simulation.driveChannels.find(item => item.source_ids.includes(sourceId));
  if (channel) simulation.setChannelMotion(channel.id, motion);
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
  it('automatically prepares confident server guesses with roles and the detected domain', async () => {
    const guessed = { ...summary, suggested_units: 'mm', detected_planes: ['x0', 'y0'], kept_sides: { x0: 'negative', y0: 'positive' },
      groups: summary.groups.map(group => ({ ...group, suggested_role: group.tag === 2 ? 'MF' : 'RIGID', role_from: 'file' })) };
    const mesh = { ...record, sources: [{ id: 'group_2', role: 'MF' }],
      source_file: { ...record.source_file, roles: { 1: 'RIGID', 2: 'MF' }, domain_planes: ['x0', 'y0'], reflect_planes: null },
      symmetry: { domain_planes: ['x0', 'y0'], cut_planes: [], reflected_planes: ['x0'] } };
    const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect') ? response(guessed) : prepared(mesh));
    await loadMeshFile('speaker.msh', MSH, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string)).toMatchObject({
      roles: { 1: 'RIGID', 2: 'MF' }, units: 'mm', domain_planes: ['x0', 'y0'], reflect_planes: null,
    });
    expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string)).not.toHaveProperty('source_tags');
    expect(registeredMesh()?.sources?.[0].role).toBe('MF');
    expect(fileMeshSubmission().options.symmetry).toBe('auto');
    expect(importedMeshStore.getSnapshot().file?.scene.surfaces.find(surface => surface.sourceRole)?.sourceRole).toBe('MF');
    await editMeshImport({ roles: { 1: 'RIGID', 2: 'LF' } }, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(JSON.parse(fetcher.mock.calls[2][1]?.body as string).roles).toEqual({ 1: 'RIGID', 2: 'LF' });
  });

  it('waits for ambiguous role guesses and units, then prepares after the choices', async () => {
    const guessed = { ...summary, suggested_units: null,
      groups: summary.groups.map(group => ({ ...group, suggested_role: 'RIGID', needs_role_choice: group.tag === 2 })) };
    const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect') ? response(guessed) : prepared(record));
    await loadMeshFile('speaker.msh', MSH, fetcher);
    await editMeshImport({ units: 'mm' }, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(useImportedModelStore.getState().fileDraft.roles?.[2]).toBeNull();
    await editMeshImport({ roles: { 1: 'RIGID', 2: 'HF' } }, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(registeredMesh()?.ingest_id).toBe(record.ingest_id);
  });

  it('rechecks detection in the chosen axes before automatically preparing', async () => {
    const guessed = { ...summary, suggested_units: 'mm', detected_planes: ['x0'], groups: summary.groups.map(group => ({ ...group, suggested_role: group.tag === 2 ? 'HF' : 'RIGID' })) };
    const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect') ? response(guessed) : prepared(record));
    await loadMeshFile('speaker.msh', MSH, fetcher);
    fetcher.mockImplementation(async url => String(url).endsWith('/inspect') ? response({ ...guessed, detected_planes: ['y0'] }) : prepared(record));
    await editMeshImport({ forward: '+z', up: '+x' }, fetcher);
    expect(JSON.parse(fetcher.mock.calls[2][1]?.body as string)).toMatchObject({ units: 'mm', forward: '+z', up: '+x' });
    expect(JSON.parse(fetcher.mock.calls[3][1]?.body as string)).toMatchObject({ domain_planes: ['y0'], forward: '+z', up: '+x' });
  });

  it('allows later edits to replace an in-flight preparation and ignores its stale failure', async () => {
    showPrepared();
    const earlier = deferred<Response>();
    const first = editMeshImport({ units: 'cm' }, vi.fn(() => earlier.promise));
    const newer = { ...record, ingest_id: 'wgi_newer' };
    await editMeshImport({ units: 'in' }, vi.fn(async () => prepared(newer)));
    earlier.resolve(response({ detail: 'Earlier mesh could not be prepared.' }, 422));
    await first;
    expect(registeredMesh()?.ingest_id).toBe('wgi_newer');
    expect(useMeshImportStore.getState()).toMatchObject({ busy: false, error: null });
  });

  it('keeps a preparation refusal until an explicit retry or edit', async () => {
    showPrepared();
    const fetcher = vi.fn<typeof fetch>(async () => response({ detail: 'Close the opening in the meshing tool.' }, 422));
    await editMeshImport({ reflectPlanes: [] }, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(useMeshImportStore.getState().error).toBe('Close the opening in the meshing tool.');
    await prepareMeshFile(vi.fn(async () => prepared(record)));
    expect(useMeshImportStore.getState()).toMatchObject({ busy: false, error: null });
    expect(registeredMesh()?.ingest_id).toBe(record.ingest_id);
  });

  it('saves a prepared setup before any Solve and reopens its model with it', async () => {
    showPrepared();
    editSourceMotion('group_2', 'axial');
    useImportedSolveOptionsStore.getState().setSweep({ startHz: 250, endHz: 8000, count: 17 });
    const expected = fileMeshSubmission();
    const save = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_saved' }));
    await saveImportedSetup(registeredMesh()!.model_id, registeredMesh()!.ingest_id, fileMeshSetup(), save);
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

  it('inspects original bytes on the backend, then prepares explicit units and groups', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => response(summary));
    await loadMeshFile('speaker.msh', MSH, fetcher);
    expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ filename: 'speaker.msh', msh_text: MSH });
    expect(() => fileMeshSubmission()).toThrow('Choose units');
    editFileDraft({ units: 'mm', selected: [2] });
    const register = vi.fn<typeof fetch>(async () => prepared(record));
    await prepareMeshFile(register);
    expect(register.mock.calls[0][0]).toBe('/api/models/files');
    expect(JSON.parse(register.mock.calls[0][1]?.body as string)).toMatchObject({ units: 'mm', roles: { 2: 'HF' }, domain_planes: [], reflect_planes: null, msh_text: MSH });
    expect(importedMeshStore.getSnapshot().file?.ingestId).toBe(record.ingest_id);
    // A registration fills the store: the file's model and its prepared mesh.
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'file', modelId: record.model_id, phase: 'ready',
      fileDraft: { units: 'mm', forward: '+z', up: '+y', selected: [2] }, restoredRevisionId: null });
    const body = fileMeshSubmission();
    expect(body.geometry).not.toHaveProperty('mesh');
    expect(body.geometry.drive_channels).toEqual([{ id: 'group_2', source_ids: ['group_2'], motion: 'normal' }]);
    expect(body.options.symmetry).toBe('auto');
    // No hard-coded rig: the imported instance's polar settings and spacing go out.
    expect(body.options.polar_config.angle_range).toEqual([0, 180, 37]);
    expect(body.options.frequency_spacing).toBe('log');
    editFileDraft({ units: 'in' });
    expect(() => fileMeshSubmission()).toThrow('Choose units');
  });

  it('solves with the imported options\' polar settings and frequency spacing, never the parametric ones', async () => {
    await loadMeshFile('speaker.msh', MSH, vi.fn<typeof fetch>(async () => response(summary)));
    editFileDraft({ units: 'mm', selected: [2] });
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
    editFileDraft({ units: 'mm', selected: [2] });
    const wait = deferred<Response>();
    const pending = prepareMeshFile(vi.fn(() => wait.promise));
    editFileDraft({ selected: [1] });
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
    expect(runVerdict(job, currentModel())).toBe('current');
    expect(runVerdict({ ...job, config_summary: {} }, currentModel())).toBe('other-model');
  });

  it('is editable: it is prepared again from the original the server retained', async () => {
    await openImported({ kind: 'run', job: fileJob() }, workspaceFetcher(setupOf({})));
    expect(useMeshImportStore.getState().original).toBeNull();
    expect(useImportedModelStore.getState()).toMatchObject({ originalAvailable: true, restoredRevisionId: 'wgs_recorded',
      fileDraft: { units: 'mm', forward: '+z', up: '+y', selected: [2] } });
    editFileDraft({ units: 'cm' });
    // Changing the preparation ends the opened setup.
    expect(useImportedModelStore.getState()).toMatchObject({ prepared: null, restoredRevisionId: null, setup: null });
    const again = { ...record, ingest_id: 'wgi_again', source_file: { ...record.source_file, transform: { units: 'cm', forward: '+z', up: '+y' } } };
    const fetcher = vi.fn<typeof fetch>(async () => prepared(again));
    await prepareMeshFile(fetcher);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/models/${encodeURIComponent(record.model_id)}/prepare`);
    expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ units: 'cm', forward: '+z', up: '+y', roles: { 2: 'HF' }, domain_planes: [], reflect_planes: null, remove_lid_planes: [] });
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
    expect(surfaces.find(surface => surface.key.endsWith(':1'))).toMatchObject({ sourceRole: 'HF', materialClass: 'hf-smooth' });
    expect(surfaces.find(surface => surface.key.endsWith(':2'))).toMatchObject({ sourceRole: null, materialClass: 'horn-smooth' });
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
    editSourceMotion('group_2', 'axial');
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

describe('server-valid file setup edit contract', () => {
  function meshFor(setup: ImportedSetup): RegisteredMesh {
    const ids = [...setup.geometry.drive_channels.flatMap(channel => channel.source_ids), ...setup.geometry.skipped_source_ids];
    const source_tags = Object.fromEntries(ids.map(id => [id, Number(id.slice('group_'.length))]));
    return { ...record, source_tags, source_file: { ...record.source_file, source_tags: Object.values(source_tags) } };
  }

  async function expectSavedSetup(setup: ImportedSetup) {
    const save = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_contract' }));
    await saveImportedSetup(registeredMesh()!.model_id, registeredMesh()!.ingest_id, fileMeshSetup(), save);
    expect(save).toHaveBeenCalledOnce();
    expect(JSON.parse(save.mock.calls[0][1]?.body as string).setup).toEqual({ schema_version: 1, ...setup });
    const { geometry, options } = setup;
    expect(fileMeshSubmission()).toEqual(bindImportedSetup(registeredMesh()!, { geometry, options }));
  }

  it.each(Object.entries(editContract.setups))('saves and solves valid unedited %s', async (_name, setup) => {
    const mesh = meshFor(setup);
    await loadMeshReplay('{}', vi.fn(async () => response({ record: mesh, request: bindImportedSetup(mesh, setup) })));
    // A replay carries wire physics; library provenance belongs to saved setups.
    await expectSavedSetup({ geometry: setup.geometry, options: setup.options });
  });

  it.each(editContract.edits)('$name', async ({ base, edit, expected }) => {
    const setup = editContract.setups[base];
    await openImported({ kind: 'run', job: fileJob() }, workspaceFetcher(setup, meshFor(setup)));
    for (const motion of edit.motions ?? []) editSourceMotion(edit.source_id!, motion);
    if (edit.selected !== undefined) {
      editFileDraft({ selected: edit.selected });
      const next: RegisteredMesh = { ...record, ingest_id: 'wgi_reprepared',
        source_tags: Object.fromEntries(edit.selected.map(tag => [`group_${tag}`, tag])),
        source_file: { ...record.source_file, source_tags: edit.selected } };
      const prepare = vi.fn(async () => prepared(next));
      await prepareMeshFile(prepare);
      expect(useMeshImportStore.getState().error).toBeNull();
      if (!edit.selected.length) {
        expect(prepare).not.toHaveBeenCalled();
        expect(registeredMesh()).toBeNull();
        expect(() => fileMeshSubmission()).toThrow('Choose units');
        return;
      }
    }
    await expectSavedSetup(expected);
  });
});

describe('portable mesh replay', () => {
  const replayRecord: RegisteredMesh = { ...record, source_tags: { group_1: 1, group_2: 2 },
    source_file: { ...record.source_file, source_tags: [1, 2] } };
  function exactRequest() {
    showPrepared(replayRecord);
    return bindImportedSetup(replayRecord, editContract.setups.replay);
  }
  const setupOfRequest = (request: ReturnType<typeof fileMeshSubmission>) => {
    const { type: _t, ingest_id: _i, manifest_sha256: _m, artifact_sha256: _a, acknowledged_findings: _f, ...geometry } = request.geometry;
    return { schema_version: 1, geometry, options: request.options };
  };

  it('solves the complete request until a setting is edited', async () => {
    const request = exactRequest();
    const opening = vi.fn(async () => response({ record: replayRecord, request }));
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
    await loadMeshReplay('{}', vi.fn(async () => response({ record: replayRecord, request })));
    useSolveOptionsStore.getState().updatePolar({ distance: 9 });
    useSolveOptionsStore.getState().setEngine('bempp');
    expect(unchangedSetup('file', record.ingest_id)).toEqual({ setup: setupOfRequest(request) });
  });

  it('saves and reopens the complete replay without changing its physics', async () => {
    const request = exactRequest();
    await loadMeshReplay('{}', vi.fn(async () => response({ record: replayRecord, request })));
    const before = fileMeshSubmission();
    const save = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_saved' }));
    await saveImportedSetup(registeredMesh()!.model_id, registeredMesh()!.ingest_id, fileMeshSetup(), save);
    const { setup } = JSON.parse(save.mock.calls[0][1]?.body as string);
    expect(setup).toEqual(setupOfRequest(request));
    expect(fileMeshSubmission()).toEqual(before);
    expect(await openImported({ kind: 'model', modelId: record.model_id }, vi.fn(async url =>
      String(url).includes('/setup?') ? response({ setup, revision_id: 'wgs_saved' }) : prepared(replayRecord)))).toBe(true);
    expect(useImportedModelStore.getState().setup).toEqual(setup);
    expect(fileMeshSubmission()).toEqual(before);
  });

  it('edits only the chosen controls, keeping retained drivers, combination, voltage and other options', async () => {
    const request = exactRequest();
    await loadMeshReplay('{}', vi.fn(async () => response({ record: replayRecord, request })));
    // Hydration is not a person's engine choice and must not ride along with a later edit.
    useImportedSolveOptionsStore.setState({ engine: 'bempp' });
    useImportedSolveOptionsStore.getState().updatePolar({ distance: 9 });
    const expected = structuredClone(request);
    expected.options.polar_config.distance = 9;
    expect(fileMeshSubmission()).toEqual(expected);
    editSourceMotion('group_2', 'axial');
    expected.geometry.drive_channels[0].motion = 'axial';
    delete expected.geometry.drive_channels[0].driver;
    expect(fileMeshSubmission()).toEqual(expected);
    const save = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_edited' }));
    await saveImportedSetup(registeredMesh()!.model_id, registeredMesh()!.ingest_id, fileMeshSetup(), save);
    expect(JSON.parse(save.mock.calls[0][1]?.body as string).setup).toEqual(setupOfRequest(expected));
  });

  it('keeps retained physics when only file preparation axes change', async () => {
    const request = exactRequest();
    await loadMeshReplay('{}', vi.fn(async () => response({ record: replayRecord, request })));
    editFileDraft({ forward: '+x', up: '+z' });
    expect(() => fileMeshSubmission()).toThrow('Choose units');
    const next = { ...replayRecord, ingest_id: 'wgi_rotated',
      source_file: { ...replayRecord.source_file, transform: { units: 'mm', forward: '+x', up: '+z' } } };
    const wait = deferred<Response>();
    const preparing = prepareMeshFile(vi.fn(() => wait.promise));
    useImportedSolveOptionsStore.getState().updatePolar({ distance: 8 });
    wait.resolve(prepared(next));
    await preparing;
    request.options.polar_config.distance = 8;
    expect(fileMeshSubmission()).toEqual({ ...request, geometry: { ...request.geometry, ingest_id: next.ingest_id } });
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

it('inspects the retained original when symmetry is changed after reopening', async () => {
  await openImported({ kind: 'model', modelId: record.model_id }, vi.fn(async url =>
    String(url).includes('/setup?') ? response({ setup: null }) : prepared(record)));
  const lids = { x0: { candidate: true, triangle_count: 2, groups: [{ tag: 1, triangle_count: 2 }] } };
  const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? response({ ...summary, lids }) : response({ detail: 'Remove the closed face in the symmetry settings.' }, 422));
  await editMeshImport({ domainPlanes: ['x0'] }, fetcher);
  expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ model_id: record.model_id, units: 'mm', forward: '+z', up: '+y' });
  expect(useMeshImportStore.getState().summary?.lids).toEqual(lids);
  expect(useImportedModelStore.getState().fileDraft.domainPlanes).toEqual(['x0']);
  await editMeshImport({ removeLidPlanes: ['x0'] }, fetcher);
  expect(JSON.parse(fetcher.mock.calls[2][1]?.body as string).remove_lid_planes).toEqual(['x0']);
});

it('reopens identical bytes through the model path without registering another mesh', async () => {
  const setup = setupOf({ frequencies_hz: [137, 211, 3987], engine: 'metal' }, 'axial');
  const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? response({ ...summary, known_model: { model_id: record.model_id, latest_prepared_id: record.ingest_id } })
    : String(url).includes('/setup?') ? response({ setup, revision_id: 'wgs_last' }) : prepared(record));
  await loadMeshFile('renamed.msh', MSH, fetcher);
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
    '/api/models/files/inspect', `/api/models/${encodeURIComponent(record.model_id)}?prepared_id=${record.ingest_id}`,
    `/api/models/${encodeURIComponent(record.model_id)}/setup?prepared_id=${record.ingest_id}`,
  ]);
  expect(unchangedSetup('file', record.ingest_id)).toEqual({ setup_revision_id: 'wgs_last' });
  expect(fileMeshSubmission()).toEqual(bindImportedSetup(record, setup));
  expect(useMeshImportStore.getState().notice).toBe('Settings from your last import');
});

it('reuses the previous preparation and exact physics for a changed file with the same groups', async () => {
  const setup = setupOf({ frequencies_hz: [137, 211, 3987], engine: 'metal' }, 'axial');
  const previous = { filename: 'speaker.msh', updated_at: '2026-10-26T08:31:00Z',
    preparation: { units: 'cm', forward: '+z', up: '+y', roles: { 1: 'RIGID', 2: 'MF' },
      domain_planes: ['x0'], reflect_planes: [], remove_lid_planes: ['x0'] }, setup };
  const mesh = { ...record, ingest_id: 'wgi_changed', source_file: { ...record.source_file,
    ...previous.preparation, transform: { units: 'cm', forward: '+z', up: '+y' } } };
  const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? response({ ...summary, previous_setup: previous }) : prepared(mesh));
  await loadMeshFile('speaker.msh', MSH, fetcher);
  expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string)).toMatchObject(previous.preparation);
  expect(fileMeshSubmission()).toEqual(bindImportedSetup(mesh, setup));
  expect(useMeshImportStore.getState().notice).toBe(`Using the settings from speaker.msh (${new Date(previous.updated_at).toLocaleString()})`);
  useImportedSolveOptionsStore.getState().setSweep({ count: 21 });
  expect(useMeshImportStore.getState().notice).toBeNull();
});

it('restores and edits shared drivers after a changed file reuses remembered settings', async () => {
  const mesh: RegisteredMesh = { ...record, source_tags: { group_1: 1, group_2: 2 },
    sources: [{ id: 'group_1', role: 'MF' }, { id: 'group_2', role: 'MF' }],
    source_file: { ...record.source_file, source_tags: [1, 2], roles: { 1: 'MF', 2: 'MF' } } };
  showPrepared(mesh);
  const simulation = importedSimulationSetup();
  simulation.setChannelDriverPreset('source:group_1', { id: 'catalog:woofer', label: 'Woofer', source: 'database', kind: 'lf', z_ohm: 8,
    xo_min_hz: null, base: { sd_cm2: 100, bl_t_m: 10, re_ohm: 6, mms_g: 20, fs_hz: 50 } });
  simulation.setDriveVoltage(4.2);
  simulation.setMaxDriveVoltage(18);
  const setup = fileMeshSetup();
  const previous = { filename: 'speaker.msh', updated_at: '2026-10-26T08:31:00Z',
    preparation: { units: 'mm', forward: '+z', up: '+y', roles: { 1: 'MF', 2: 'MF' } }, setup };
  const changed = { ...mesh, ingest_id: 'wgi_changed' };
  const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? response({ ...summary, previous_setup: previous }) : prepared(changed));
  await loadMeshFile('speaker.msh', MSH, fetcher);
  expect(fileMeshSubmission()).toEqual(bindImportedSetup(changed, setup));
  expect(importedSimulationSetup().driverGroups).toEqual([{ id: 'group:source%3Agroup_1', channelIds: ['source:group_1', 'source:group_2'] }]);
  importedSimulationSetup().setChannelDriverField('source:group_1', 're_ohm', 7);
  const edited = fileMeshSetup();
  expect(edited.geometry.drive_channels.map(channel => channel.source_ids)).toEqual([['group_1'], ['group_2']]);
  expect(edited.geometry.drive_channels.map(channel => channel.driver?.re_ohm)).toEqual([7, 7]);
  expect(edited.geometry).toMatchObject({ drive_voltage_v: 4.2, max_drive_voltage_v: 18 });
  expect(edited.driver_references).toEqual(setup.driver_references);
});

it.each([
  { existing: 1, share: true },
  { existing: 2, share: true },
  { existing: 2, share: false },
])('adds a same-role surface to the current sharing choice ($existing existing, share: $share)', async ({ existing, share }) => {
  const ids = Array.from({ length: existing }, (_, index) => `group_${index + 1}`);
  const mesh: RegisteredMesh = { ...record,
    source_tags: Object.fromEntries(ids.map((id, index) => [id, index + 1])),
    sources: ids.map(id => ({ id, role: 'MF' })),
    source_file: { ...record.source_file, source_tags: ids.map((_, index) => index + 1),
      roles: Object.fromEntries(ids.map((_, index) => [index + 1, 'MF'])) } };
  showPrepared(mesh);
  if (!share) importedSimulationSetup().setRoleSharing!('MF', false);
  const firstId = importedSimulationSetup().driveChannels[0].id;
  importedSimulationSetup().setChannelDriverPreset(firstId, { id: 'catalog:woofer', label: 'Woofer', source: 'database', kind: 'lf', z_ohm: 8,
    xo_min_hz: null, base: { sd_cm2: 100, bl_t_m: 10, re_ohm: 6, mms_g: 20, fs_hz: 50 } });
  const newId = `group_${existing + 1}`;
  const next: RegisteredMesh = { ...mesh, ingest_id: 'wgi_added',
    source_tags: { ...mesh.source_tags, [newId]: existing + 1 },
    sources: [...mesh.sources!, { id: newId, role: 'MF' }],
    source_file: { ...mesh.source_file, source_tags: [...mesh.source_file.source_tags, existing + 1],
      roles: { ...mesh.source_file.roles, [existing + 1]: 'MF' } } };
  await editMeshImport({ roles: next.source_file.roles }, vi.fn(async () => prepared(next)));
  expect(useMeshImportStore.getState().error).toBeNull();
  const simulation = importedSimulationSetup();
  const setup = fileMeshSetup();
  const added = setup.geometry.drive_channels.find(channel => channel.source_ids.includes(newId))!;
  if (share) {
    expect(simulation.driverGroups).toHaveLength(1);
    expect(simulation.driverGroups[0].channelIds).toHaveLength(existing + 1);
    expect(setup.geometry.drive_channels.every(channel => channel.driver?.re_ohm === 6)).toBe(true);
    expect(setup.driver_references?.[added.id]).toEqual({ driver_id: 'catalog:woofer', source: 'database' });
    expect(simulation.channelDrivers[added.id]).toEqual(simulation.channelDrivers[simulation.driverGroups[0].channelIds[0]]);
    await openImported({ kind: 'file', prepared: next, setup });
    expect(importedSimulationSetup().driverGroups[0].channelIds).toHaveLength(existing + 1);
    importedSimulationSetup().setChannelDriverField(added.id, 're_ohm', 7);
    expect(fileMeshSetup().geometry.drive_channels.map(channel => channel.driver?.re_ohm)).toEqual(Array(existing + 1).fill(7));
  } else {
    expect(simulation.driverGroups).toHaveLength(existing + 1);
    expect(added.id).toBe(`source:${newId}`);
    expect(added.driver).toBeUndefined();
    expect(setup.geometry.drive_channels.find(channel => channel.id === `source:${ids[0]}`)?.driver?.re_ohm).toBe(6);
  }
});

it('keeps an existing BEAT driver separate from a newly added same-role surface', async () => {
  const mesh: RegisteredMesh = { ...record, sources: [{ id: 'group_2', role: 'MF' }],
    source_file: { ...record.source_file, roles: { 1: 'RIGID', 2: 'MF' } } };
  showPrepared(mesh);
  importedSimulationSetup().setExteriorTransducer('source:group_2', { version: 1, motion_axis: [1, 0, 0] });
  const next: RegisteredMesh = { ...mesh, ingest_id: 'wgi_added', source_tags: { group_2: 2, group_1: 1 },
    sources: [{ id: 'group_2', role: 'MF' }, { id: 'group_1', role: 'MF' }],
    source_file: { ...mesh.source_file, source_tags: [2, 1], roles: { 1: 'MF', 2: 'MF' } } };
  await editMeshImport({ roles: next.source_file.roles }, vi.fn(async () => prepared(next)));
  const simulation = importedSimulationSetup();
  expect(simulation.driverGroups).toHaveLength(2);
  expect(simulation.driveChannels.find(channel => channel.source_ids.includes('group_2'))?.exterior_transducer?.motion_axis).toEqual([1, 0, 0]);
  expect(simulation.driveChannels.find(channel => channel.source_ids.includes('group_1'))?.exterior_transducer).toBeUndefined();
});

it('retains an incomplete shared driver when a same-role surface is added', async () => {
  const mesh: RegisteredMesh = { ...record, sources: [{ id: 'group_2', role: 'MF' }],
    source_file: { ...record.source_file, roles: { 1: 'RIGID', 2: 'MF' } } };
  showPrepared(mesh);
  importedSimulationSetup().setChannelDriverPreset('source:group_2', { id: 'unfinished', label: 'Unfinished', source: 'database', kind: 'lf',
    z_ohm: 8, xo_min_hz: null, base: {} });
  const next: RegisteredMesh = { ...mesh, ingest_id: 'wgi_added', source_tags: { group_2: 2, group_1: 1 },
    sources: [{ id: 'group_2', role: 'MF' }, { id: 'group_1', role: 'MF' }],
    source_file: { ...mesh.source_file, source_tags: [2, 1], roles: { 1: 'MF', 2: 'MF' } } };
  await editMeshImport({ roles: next.source_file.roles }, vi.fn(async () => prepared(next)));
  expect(importedSimulationSetup().driverGroups[0].channelIds).toEqual(['source:group_2', 'source:group_1']);
  expect(importedSimulationSetup().channelDrivers['source:group_1']?.preset?.id).toBe('unfinished');
  expect(() => fileMeshSetup()).toThrow('still needs');
});

it('reopens a labelled shared driver without library references after adding a surface', async () => {
  const mesh: RegisteredMesh = { ...record, ingest_id: 'wgi_added', source_tags: { group_2: 2, group_1: 1 },
    sources: [{ id: 'group_2', role: 'MF' }, { id: 'group_1', role: 'MF' }],
    source_file: { ...record.source_file, source_tags: [2, 1], roles: { 1: 'MF', 2: 'MF' } } };
  const setup: ImportedSetup = { geometry: { skipped_source_ids: [], drive_channels: [{ id: 'drive-mf', source_ids: ['group_2'],
    motion: 'normal', driver: { label: 'Manual driver', sd_cm2: 100, bl_t_m: 10, re_ohm: 6, mms_g: 20, fs_hz: 50 } }] },
    options: {} as ImportedSetup['options'] };
  const previous = { filename: 'speaker.msh', updated_at: '2026-10-26T08:31:00Z',
    preparation: { units: 'mm', forward: '+z', up: '+y', roles: { 1: 'MF', 2: 'MF' } }, setup };
  const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? response({ ...summary, previous_setup: previous }) : prepared(mesh));
  await loadMeshFile('speaker.msh', MSH, fetcher);
  expect(importedSimulationSetup().driverGroups).toEqual([{ id: 'drive-mf:2', channelIds: ['drive-mf', 'source:group_1'] }]);
  importedSimulationSetup().setChannelDriverField('drive-mf', 're_ohm', 7);
  expect(fileMeshSetup().geometry.drive_channels.map(channel => channel.driver?.re_ohm)).toEqual([7, 7]);
});

it('keeps retained crossover references when a shared singleton gains a surface', async () => {
  const mesh: RegisteredMesh = { ...record, source_tags: { group_1: 1, group_2: 2 },
    sources: [{ id: 'group_1', role: 'MF' }, { id: 'group_2', role: 'HF' }],
    source_file: { ...record.source_file, source_tags: [1, 2], roles: { 1: 'MF', 2: 'HF', 3: 'RIGID' } } };
  showPrepared(mesh);
  const setup = fileMeshSetup();
  const channel = { hp: null, lp: null, gain: { mode: 'auto' } as const, delay: { mode: 'auto' } as const, invert: null };
  const [mid, high] = importedSimulationSetup().driverGroups.map(group => group.id);
  setup.geometry.combine = { members: [mid, high], reference: mid,
    channels: { [mid]: channel, [high]: channel } };
  await openImported({ kind: 'file', prepared: mesh, setup });
  const next: RegisteredMesh = { ...mesh, ingest_id: 'wgi_added', source_tags: { ...mesh.source_tags, group_3: 3 },
    sources: [...mesh.sources!, { id: 'group_3', role: 'MF' }],
    source_file: { ...mesh.source_file, source_tags: [1, 2, 3], roles: { 1: 'MF', 2: 'HF', 3: 'MF' } } };
  await editMeshImport({ roles: next.source_file.roles }, vi.fn(async () => prepared(next)));
  expect(fileMeshSetup().geometry.combine).toEqual({ members: [mid, high],
    reference: mid, channels: { [mid]: channel, [high]: channel } });
});

it('rebuilds the crossover when re-preparation splits a driver group by role', async () => {
  const mesh: RegisteredMesh = { ...record, source_tags: { group_1: 1, group_2: 2, group_3: 3 },
    sources: [{ id: 'group_1', role: 'MF' }, { id: 'group_2', role: 'MF' }, { id: 'group_3', role: 'HF' }],
    source_file: { ...record.source_file, source_tags: [1, 2, 3], roles: { 1: 'MF', 2: 'MF', 3: 'HF' } } };
  showPrepared(mesh);
  importedSimulationSetup().setCombineEnabled(true);
  expect(fileMeshSetup().geometry.combine?.members).toHaveLength(2);
  const next = { ...mesh, ingest_id: 'wgi_roles_changed',
    sources: mesh.sources!.map(source => source.id === 'group_2' ? { ...source, role: 'LF' } : source),
    source_file: { ...mesh.source_file, roles: { 1: 'MF', 2: 'LF', 3: 'HF' } } };
  await editMeshImport({ roles: next.source_file.roles }, vi.fn(async () => prepared(next)));
  const setup = fileMeshSetup();
  expect(setup.geometry.drive_channels.map(channel => channel.id)).toEqual(['source:group_1', 'source:group_2', 'source:group_3']);
  expect(setup.geometry.driver_groups).toHaveLength(3);
  expect(setup.geometry.combine?.members).toHaveLength(3);
  expect(setup.geometry.combine?.members).toEqual(expect.arrayContaining(setup.geometry.driver_groups!.map(group => group.id)));
});

it.each(['resistance', 'physical crossover'] as const)(
  'keeps legacy channels separate through preparation when using %s', async reason => {
    const mesh: RegisteredMesh = { ...record, source_tags: { group_1: 1, group_2: 2, group_3: 3 },
      sources: [{ id: 'group_1', role: 'MF' }, { id: 'group_2', role: 'MF' }, { id: 'group_3', role: 'HF' }],
      source_file: { ...record.source_file, source_tags: [1, 2, 3], roles: { 1: 'MF', 2: 'MF', 3: 'HF' } } };
    showPrepared(mesh);
    const legacy: ImportedSetup = { geometry: { skipped_source_ids: [], drive_channels: [
      { id: 'drive-mf::group_1', source_ids: ['group_1'], motion: 'normal' },
      { id: 'drive-mf::group_2', source_ids: ['group_2'], motion: 'normal' },
      { id: 'drive-hf', source_ids: ['group_3'], motion: 'normal' },
    ], ...(reason === 'resistance' ? { rg_ohm: 0.5 } : {
      combine: { members: ['drive-mf::group_1', 'drive-hf'], crossovers_hz: [1200] },
    }) }, options: fileMeshSetup().options };
    await openImported({ kind: 'file', prepared: mesh, setup: legacy });
    const next = { ...mesh, ingest_id: 'wgi_extra', source_tags: { ...mesh.source_tags, group_4: 4 },
      sources: [...mesh.sources!, { id: 'group_4', role: 'MF' }],
      source_file: { ...mesh.source_file, source_tags: [1, 2, 3, 4], roles: { 1: 'MF', 2: 'MF', 3: 'HF', 4: 'MF' } } };
    await editMeshImport({ roles: next.source_file.roles }, vi.fn(async () => prepared(next)));
    expect(useMeshImportStore.getState().error).toBeNull();
    const setup = fileSetupForSave(fileMeshSetup());
    expect(setup.geometry.drive_channels.map(channel => channel.id)).toEqual([
      'drive-mf::group_1', 'drive-mf::group_2', 'drive-hf', 'source:group_4',
    ]);
    expect(setup.geometry.driver_groups?.every(group => group.channel_ids.length === 1)).toBe(true);
    expect(setup.geometry.rg_ohm).toEqual(legacy.geometry.rg_ohm);
    expect(setup.geometry.combine).toEqual(legacy.geometry.combine);
  },
);

it('keeps only matching roles when the re-export changes its group names', async () => {
  const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? response({ ...summary, suggested_units: 'mm', previous_roles: { 1: 'RIGID', 2: 'LF' } }) : prepared(record));
  await loadMeshFile('speaker.msh', MSH, fetcher);
  expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string)).toMatchObject({ roles: { 1: 'RIGID', 2: 'LF' }, units: 'mm' });
  expect(useMeshImportStore.getState().notice).toBeNull();
});

it('inspects remembered axes without replacing declared symmetry or sending setup fields to inspect', async () => {
  const preparation = { units: 'mm', forward: '+z', up: '+x', roles: { 1: 'RIGID', 2: 'HF' },
    domain_planes: ['x0'], reflect_planes: [], remove_lid_planes: ['x0'] };
  const mesh = { ...record, source_file: { ...record.source_file, ...preparation,
    transform: { units: 'mm', forward: '+z', up: '+x' } } };
  const fetcher = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(response({ ...summary, previous_setup: {
      filename: 'speaker.msh', updated_at: '2026-10-26T08:31:00Z', preparation, setup: null,
    } }))
    .mockResolvedValueOnce(response({ ...summary, detected_planes: [], lids: { x0: { candidate: true, triangle_count: 2, groups: [] } } }))
    .mockResolvedValueOnce(prepared(mesh));
  await loadMeshFile('speaker.msh', MSH, fetcher);
  expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string)).toEqual({ filename: 'speaker.msh', msh_text: MSH, units: 'mm', forward: '+z', up: '+x' });
  expect(JSON.parse(fetcher.mock.calls[2][1]?.body as string)).toMatchObject(preparation);
  expect(useMeshImportStore.getState().summary?.lids?.x0.candidate).toBe(true);
});

it('rebuilds missing retained artifacts through normal registration using the remembered settings', async () => {
  const setup = setupOf({ frequencies_hz: [137, 211], engine: 'metal' }, 'axial');
  const previous = { filename: 'speaker.msh', updated_at: '2026-10-26T08:31:00Z',
    preparation: { units: 'mm', forward: '+z', up: '+y', roles: { 1: 'RIGID', 2: 'MF' } }, setup };
  const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? response({ ...summary, known_model: { model_id: record.model_id, latest_prepared_id: record.ingest_id }, previous_setup: previous })
    : String(url) === '/api/models/files' ? prepared(record)
    : response({ error: { code: 'prepared_unavailable', message: 'Retained mesh unavailable. Import it again.' } }, 409));
  await loadMeshFile('speaker.msh', MSH, fetcher);
  expect(JSON.parse(fetcher.mock.calls[2][1]?.body as string)).toMatchObject({ ...previous.preparation, msh_text: MSH });
  expect(fileMeshSubmission()).toEqual(bindImportedSetup(record, setup));
  expect(useMeshImportStore.getState().error).toBeNull();
});

it('does not rebuild on a network error while reopening identical bytes', async () => {
  const fetcher = vi.fn<typeof fetch>(async url => {
    if (String(url).endsWith('/inspect')) return response({ ...summary, known_model: { model_id: record.model_id, latest_prepared_id: record.ingest_id } });
    throw new TypeError('offline');
  });
  await expect(loadMeshFile('speaker.msh', MSH, fetcher)).rejects.toThrow('offline');
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('re-inspects the latest frame when a role edit supersedes an axis inspection', async () => {
  showPrepared();
  useMeshImportStore.setState({ original: null, summary });
  const firstInspection = deferred<Response>();
  const first = editMeshImport({ up: '+x' }, vi.fn(() => firstInspection.promise));
  const newer = { ...record, ingest_id: 'wgi_latest_frame', source_file: { ...record.source_file,
    transform: { ...record.source_file.transform, up: '+x' }, roles: { 1: 'RIGID', 2: 'LF' }, domain_planes: ['y0'] } };
  const fetcher = vi.fn<typeof fetch>(async url => String(url).endsWith('/inspect')
    ? response({ ...summary, detected_planes: ['y0'] }) : prepared(newer));
  await editMeshImport({ roles: { 1: 'RIGID', 2: 'LF' } }, fetcher);
  firstInspection.resolve(response({ ...summary, detected_planes: ['x0'] }));
  await first;
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string)).toMatchObject({ up: '+x', roles: { 2: 'LF' }, domain_planes: ['y0'] });
  expect(useImportedModelStore.getState().fileDraft.domainPlanes).toEqual(['y0']);
  expect(registeredMesh()?.ingest_id).toBe('wgi_latest_frame');
});

it('previews a role edit immediately while keeping the prepared snapshot immutable', async () => {
  showPrepared();
  const snapshot = importedMeshStore.getSnapshot().file!;
  const waiting = deferred<Response>();
  const editing = editMeshImport({ roles: { 1: 'RIGID', 2: 'LF' } }, vi.fn(() => waiting.promise));
  const preview = importedMeshStore.getSnapshot().file!;
  expect(preview.scene.surfaces.find(surface => surface.role === 'imported.LF.2')?.sourceRole).toBe('LF');
  expect(snapshot.scene.surfaces).not.toEqual(preview.scene.surfaces);
  expect(preview.artifactToken).toBe(snapshot.artifactToken);
  expect(preview.scene.surfaces[0].positions).toBe(snapshot.scene.surfaces[0].positions);
  expect(registeredMesh()).toBeNull();
  waiting.resolve(response({ detail: 'Close the opening.' }, 422));
  await editing;
  expect(importedMeshStore.getSnapshot().file).toBe(preview);
});
