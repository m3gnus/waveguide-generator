import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { workspaceModeStore } from './workspaceMode';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { editMeshImport, fileMeshSubmission, fileSolvePress, finishFileSolvePress, loadMeshFile,
  prepareMeshFile, loadMeshReplay, restoreMeshImport, restorePendingMeshPress, restoreSavedMeshSetup, saveMeshSetup, useMeshImportStore } from './meshImport';
import type { RegisteredMesh, MeshSummary } from '../api/meshImport';
import { bindImportedSetup, type ImportedSetup } from '../jobs/importedSetup';
import type { JobItem } from '../api/jobsSocket';
import { runSourceForJob } from '../jobs/exportNaming';
import { runMatchesContext, runContext } from '../results/runCoherence';

const MSH = new TextDecoder().decode(readFileSync('../server/tests/fixtures/mesh_import/tetra.msh'));
const summary: MeshSummary = { node_count: 4, triangle_count: 4, dimensions: [100, 100, 100], dimensions_mm: [100, 100, 100],
  groups: [{ tag: 1, name: 'Rigid', triangle_count: 3 }, { tag: 2, name: 'HF', triangle_count: 1 }] };
const record: RegisteredMesh = { ingest_id: 'wgi_file', producer: 'mesh-file', manifest_sha256: 'sha256:manifest',
  artifact_sha256: 'sha256:artifact', summary,
  source_file: { filename: 'speaker.msh', transform: { units: 'mm', forward: '+z', up: '+y' }, source_tags: [2] },
  source_tags: { group_2: 2 }, msh_text: MSH };
const initial = useMeshImportStore.getState();
const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }

function retainedJobFetcher(job: JobItem, mesh = record) {
  const effective_request = bindImportedSetup(mesh, {
    geometry: { skipped_source_ids: [], drive_channels: (job.cad_setup?.drive_channels ?? [{ id: 'group_2', source_ids: ['group_2'] }])
      .map(channel => ({ ...channel, motion: channel.motion ?? 'normal', driver: channel.driver ?? undefined })) },
    options: job.solve_options as unknown as ImportedSetup['options'],
  });
  return vi.fn<typeof fetch>(async url => response(String(url).endsWith('/requests') ? { effective_request } : mesh));
}

beforeEach(() => {
  useMeshImportStore.setState(initial, true);
  importedMeshStore.clear();
  workspaceModeStore.setMode('parametric');
  resetSolveOptionsStore();
  sessionStorage.clear();
  localStorage.removeItem('wg-saved-mesh-setup');
});
afterEach(() => vi.restoreAllMocks());

describe('standalone imported mesh', () => {
  it('saves and restores a prepared setup before any Solve', async () => {
    useMeshImportStore.setState({ registered: record, motions: { group_2: 'axial' } });
    useImportedSolveOptionsStore.getState().setSweep({ startHz: 250, endHz: 8000, count: 17 });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', record.ingest_id));
    const expected = fileMeshSubmission();
    const save = vi.fn<typeof fetch>(async () => response({ revision_id: 'wgs_saved' }));
    await saveMeshSetup(save);
    const setup = JSON.parse(save.mock.calls[0][1]?.body as string);
    expect(setup.geometry).not.toHaveProperty('ingest_id');
    expect(setup.geometry).not.toHaveProperty('mesh');
    useMeshImportStore.setState(initial, true);
    importedMeshStore.clear();
    expect(await restoreSavedMeshSetup(vi.fn(async url => response(String(url).endsWith('/setup') ? { setup } : record)))).toBe(true);
    expect(fileMeshSubmission()).toEqual(expected);
  });

  it('does not mark settings edited during or after saving as saved', async () => {
    useMeshImportStore.setState({ registered: record });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', record.ingest_id));
    const wait = deferred<Response>();
    const saving = saveMeshSetup(vi.fn(() => wait.promise));
    useImportedSolveOptionsStore.getState().setSweep({ count: 17 });
    wait.resolve(response({ revision_id: 'wgs_old' }));
    await saving;
    expect(useMeshImportStore.getState().notice).toContain('current edits are not saved');
    await saveMeshSetup(vi.fn(async () => response({ revision_id: 'wgs_new' })));
    expect(useMeshImportStore.getState().notice).toContain('Mesh setup saved.');
    useImportedSolveOptionsStore.getState().setSweep({ count: 19 });
    expect(useMeshImportStore.getState().notice).toBeNull();
  });
  it('tracks borrowed engine and accuracy changes during and after Save', async () => {
    useMeshImportStore.setState({ registered: record });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', record.ingest_id));
    const wait = deferred<Response>();
    const saving = saveMeshSetup(vi.fn(() => wait.promise));
    useImportedSolveOptionsStore.getState().setEngine('bempp');
    wait.resolve(response({ revision_id: 'wgs_old' }));
    await saving;
    expect(useMeshImportStore.getState().notice).toContain('current edits are not saved');
    await saveMeshSetup(vi.fn(async () => response({ revision_id: 'wgs_new' })));
    useImportedSolveOptionsStore.setState({ accuracy: 'accurate' });
    expect(useMeshImportStore.getState().notice).toBeNull();
  });
  it('inspects original bytes on the backend, then prepares explicit units and groups', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => response(summary));
    await loadMeshFile('speaker.msh', MSH, fetcher);
    expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ filename: 'speaker.msh', msh_text: MSH });
    expect(() => fileMeshSubmission()).toThrow('Choose units');
    editMeshImport({ units: 'mm', selected: [2] });
    const register = vi.fn<typeof fetch>(async () => response(record));
    await prepareMeshFile(register);
    expect(JSON.parse(register.mock.calls[0][1]?.body as string)).toMatchObject({ units: 'mm', source_tags: [2], msh_text: MSH });
    expect(importedMeshStore.getSnapshot().file?.ingestId).toBe(record.ingest_id);
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
    await prepareMeshFile(vi.fn<typeof fetch>(async () => response(record)));
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
    useMeshImportStore.setState({ registered: record });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', record.ingest_id));
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

  it('restores an old saved file setup that carried a range and count as the same frequencies', async () => {
    useMeshImportStore.setState({ registered: record });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', record.ingest_id));
    const saved = fileMeshSubmission();
    const { type: _t, ingest_id: _i, manifest_sha256: _m, artifact_sha256: _a, acknowledged_findings: _f, ...geometry } = saved.geometry;
    // An old setup: the range and count were stored in the options, and nothing else about the sweep.
    const options = { ...saved.options, frequency_range: [210, 6300] as [number, number], num_frequencies: 23 };
    useMeshImportStore.setState(initial, true);
    importedMeshStore.clear();
    resetSolveOptionsStore();
    localStorage.setItem('wg-saved-mesh-setup', record.ingest_id);
    const fetcher = vi.fn(async (url: RequestInfo | URL) => response(String(url).endsWith('/setup') ? { setup: { geometry, options } } : record));
    expect(await restoreSavedMeshSetup(fetcher as unknown as typeof fetch)).toBe(true);
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ sweepStartHz: 210, sweepEndHz: 6300, sweepCount: 23, frequencyMode: 'range' });
    expect(fileMeshSubmission().options).toMatchObject({ frequency_range: [210, 6300], num_frequencies: 23 });
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
    wait.resolve(response(record));
    await pending;
    expect(useMeshImportStore.getState().registered).toBeNull();
    expect(useMeshImportStore.getState().busy).toBe(false);
    expect(() => fileMeshSubmission()).toThrow('Choose units');
  });

  it('restores the retained file in file mode, with no CAD identity or original file', async () => {
    const job = { id: 'file-job', cad_source: { ingest_id: record.ingest_id },
      config_summary: { geometry_type: 'imported', import_origin: 'mesh-file' },
      solve_options: { frequencies_hz: [100, 200] } } as unknown as JobItem;
    expect(await restoreMeshImport(job, retainedJobFetcher(job))).toBe(true);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(workspaceModeStore.getSnapshot().importedInput).toBe('file');
    expect(useMeshImportStore.getState().original).toBeNull();
    expect(fileMeshSubmission().options.frequencies_hz).toEqual([100, 200]);
    expect(runSourceForJob(job)).toBe('mesh-file');
    expect(runMatchesContext(job, runContext())).toBe('current');
    expect(runMatchesContext({ ...job, config_summary: {} }, runContext())).toBe('other-model');
  });

  it('does not submit a prepared record while a different file occupies the viewport', () => {
    useMeshImportStore.setState({ registered: record });
    importedMeshStore.setFile(createImportedMeshScene('different.msh', parseMSH(MSH), 'file', 'wgi_other'));
    expect(() => fileMeshSubmission()).toThrow('Choose units');
  });

  it('restores generated sweep, axial motion and solver settings without borrowing the open design', async () => {
    const job = { cad_source: { ingest_id: record.ingest_id },
      solve_options: { frequencies_hz: null, frequency_range: [250, 8000], num_frequencies: 17, engine: 'metal', accuracy: 'accurate' },
      cad_setup: { drive_channels: [{ id: 'group_2', source_ids: ['group_2'], motion: 'axial' }] } } as unknown as JobItem;
    await restoreMeshImport(job, retainedJobFetcher(job));
    const body = fileMeshSubmission();
    expect(body.options).toMatchObject({ frequency_range: [250, 8000], num_frequencies: 17, engine: 'metal', accuracy: 'accurate' });
    expect(body.geometry.drive_channels[0].motion).toBe('axial');
  });

  it('shows excitation by registered tag instead of trusting a group named HF', async () => {
    const selectedRigid = { ...record, source_tags: { group_1: 1 } };
    const job = { cad_source: { ingest_id: record.ingest_id }, solve_options: {} } as unknown as JobItem;
    await restoreMeshImport(job, retainedJobFetcher(job, selectedRigid));
    const surfaces = importedMeshStore.getSnapshot().file!.scene.surfaces;
    expect(surfaces.find(surface => surface.role.includes('Rigid'))?.materialClass).toBe('source-smooth');
    expect(surfaces.find(surface => surface.role.includes('HF'))?.materialClass).toBe('horn-smooth');
  });

  it('recovers the same ingest and pending press after a reload, without another registration', async () => {
    useMeshImportStore.setState({ registered: record, motions: { group_2: 'axial' } });
    useImportedSolveOptionsStore.getState().setSweep({ startHz: 250, endHz: 8000, count: 17 });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', record.ingest_id));
    const original = fileMeshSubmission();
    const press = fileSolvePress(original, 'lost response');
    useMeshImportStore.setState(initial, true);
    importedMeshStore.clear();
    const fetcher = vi.fn<typeof fetch>(async () => response(record));
    expect(await restorePendingMeshPress(fetcher)).toBe(true);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/mesh-import/${record.ingest_id}`);
    const restored = fileMeshSubmission();
    expect(restored).toEqual(original);
    expect(fileSolvePress(restored, 'new time')).toMatchObject(press);
  });

  it.each(['manifest_sha256', 'artifact_sha256'] as const)('refuses pending recovery when retained %s changed', async key => {
    useMeshImportStore.setState({ registered: record });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', record.ingest_id));
    fileSolvePress(fileMeshSubmission(), 'unanswered');
    useMeshImportStore.setState(initial, true);
    importedMeshStore.clear();
    await expect(restorePendingMeshPress(vi.fn(async () => response({ ...record, [key]: 'changed' })))).rejects.toThrow('does not match');
    expect(importedMeshStore.getSnapshot().file).toBeNull();
    expect(useMeshImportStore.getState().registered).toBeNull();
  });

  it('reuses the unanswered press ID and label, then gives a deliberate solve a new ID', () => {
    useMeshImportStore.setState({ registered: record });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', record.ingest_id));
    const body = fileMeshSubmission();
    const first = fileSolvePress(body, 'first label');
    expect(fileSolvePress(body, 'later label')).toMatchObject(first);
    finishFileSolvePress(first.id);
    expect(fileSolvePress(body, 'new label').id).not.toBe(first.id);
  });
});


describe('portable mesh replay', () => {
  function exactRequest() {
    useMeshImportStore.setState({ registered: record });
    importedMeshStore.setFile(createImportedMeshScene('speaker.msh', parseMSH(MSH), 'file', record.ingest_id));
    const request = fileMeshSubmission();
    request.options.frequencies_hz = [137, 211, 3987];
    request.options.polar_config = { ...request.options.polar_config, distance: 3.7, norm_angle: 17, enabled_axes: ['vertical'] };
    request.geometry.drive_voltage_v = 4.2;
    return request;
  }

  it('preserves the complete request through open, Save, reload and unanswered press recovery', async () => {
    const request = exactRequest();
    const opening = vi.fn(async () => response({ record, request }));
    expect(await loadMeshReplay('{"version":1}', opening)).toBe(true);
    expect(opening.mock.calls[0]).toEqual(['/api/mesh-replay', expect.objectContaining({ body: '{"version":1}' })]);
    expect(fileMeshSubmission()).toEqual(request);
    const mutated = fileMeshSubmission();
    mutated.options.frequencies_hz![0] = 1;
    expect(fileMeshSubmission()).toEqual(request);
    const save = vi.fn<typeof fetch>(async () => response({ revision_id: 'saved' }));
    await saveMeshSetup(save);
    const setup = JSON.parse(save.mock.calls[0][1]?.body as string);
    useMeshImportStore.setState(initial, true);
    importedMeshStore.clear();
    await restoreSavedMeshSetup(vi.fn(async url => response(String(url).endsWith('/setup') ? { setup } : record)));
    expect(fileMeshSubmission()).toEqual(request);
    const press = fileSolvePress(fileMeshSubmission(), 'Replay');
    useMeshImportStore.setState(initial, true);
    importedMeshStore.clear();
    await restorePendingMeshPress(vi.fn(async () => response(record)));
    expect(fileMeshSubmission()).toEqual(request);
    expect(fileSolvePress(fileMeshSubmission(), 'Other label')).toEqual(expect.objectContaining({ id: press.id, label: 'Replay' }));
  });

  it('restores a selected run with its full effective request, never execution settings', async () => {
    const request = exactRequest();
    request.geometry.drive_channels[0].driver = { name: 'Frozen driver', re_ohm: 6.2 };
    const job = { id: 'saved-run', cad_source: { ingest_id: record.ingest_id }, solve_options: {} } as JobItem;
    const fetcher = vi.fn<typeof fetch>(async url => response(String(url).endsWith('/requests')
      ? { effective_request: { ...request, parent_job_id: 'old-parent', client_request_id: 'spent' }, execution_request: { ...request, options: {} } } : record));
    expect(await restoreMeshImport(job, fetcher)).toBe(true);
    expect(fetcher.mock.calls[0][0]).toBe('/api/jobs/saved-run/requests');
    expect(fileMeshSubmission()).toEqual(request);
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ sweepStartHz: 137, sweepEndHz: 3987, sweepCount: 3, frequencyMode: 'list' });
    useImportedSolveOptionsStore.setState({ engine: 'bempp' });
    expect(fileMeshSubmission()).toEqual(request);
  });

  it.each(['ingest_id', 'manifest_sha256', 'artifact_sha256'] as const)('refuses a retained run with mismatched %s', async key => {
    const request = exactRequest();
    request.geometry[key] = 'different';
    const job = { id: 'saved-run', cad_source: { ingest_id: record.ingest_id } } as JobItem;
    await expect(restoreMeshImport(job, vi.fn(async url => response(String(url).endsWith('/requests')
      ? { effective_request: request } : record)))).rejects.toThrow(/does not match/);
    expect(() => fileMeshSubmission()).toThrow('does not match');
  });

  it('refuses unavailable run settings without borrowing the open model', async () => {
    exactRequest();
    const job = { id: 'missing-run', cad_source: { ingest_id: record.ingest_id } } as JobItem;
    await expect(restoreMeshImport(job, vi.fn(async () => new Response('{}', { status: 404 })))).rejects.toThrow('404');
    expect(useMeshImportStore.getState().busy).toBe(false);
    expect(() => fileMeshSubmission()).toThrow('404');
  });

  it.each(['request', 'mesh'])('ignores a selected run whose %s response arrives after navigation', async stage => {
    const request = exactRequest();
    const wait = deferred<Response>();
    const job = { id: 'saved-run', cad_source: { ingest_id: record.ingest_id } } as JobItem;
    const meshStarted = deferred<void>();
    const restoring = restoreMeshImport(job, vi.fn(async url => {
      if (stage === 'request') return wait.promise;
      if (String(url).endsWith('/requests')) return response({ effective_request: request });
      meshStarted.resolve();
      return wait.promise;
    }));
    if (stage === 'mesh') await meshStarted.promise;
    importedMeshStore.showParametric();
    wait.resolve(response(stage === 'request' ? { effective_request: request } : record));
    expect(await restoring).toBe(false);
    expect(importedMeshStore.getSnapshot().showing).toBe('parametric');
    expect(useMeshImportStore.getState().busy).toBe(false);
  });

  it.each(['local', 'global'] as const)('ends exact replay on a %s setting edit', async kind => {
    const request = exactRequest();
    await loadMeshReplay('{}', vi.fn(async () => response({ record, request })));
    if (kind === 'local') useMeshImportStore.setState({ motions: { group_2: 'axial' } });
    else useImportedSolveOptionsStore.getState().updatePolar({ distance: 9 });
    expect(useMeshImportStore.getState().exactSubmission).toBeNull();
    expect(useMeshImportStore.getState().notice).toContain('starts a new setup');
    // The restored list is the imported options' own, so it carries over to the new setup.
    expect(fileMeshSubmission().options.frequencies_hz).toEqual([137, 211, 3987]);
  });

  it('keeps exact replay through an edit to the parametric options', async () => {
    const request = exactRequest();
    await loadMeshReplay('{}', vi.fn(async () => response({ record, request })));
    useSolveOptionsStore.getState().updatePolar({ distance: 9 });
    useSolveOptionsStore.getState().setEngine('bempp');
    expect(useMeshImportStore.getState().exactSubmission).not.toBeNull();
    expect(fileMeshSubmission()).toEqual(request);
  });

  it('keeps exact replay through late hydration and automatic solve-options transitions', async () => {
    const request = exactRequest();
    await loadMeshReplay('{}', vi.fn(async () => response({ record, request })));
    useImportedSolveOptionsStore.setState(state => ({ engine: 'bempp', accuracy: 'accurate', polar: { ...state.polar, distance: 9 } }));
    await useSolveOptionsStore.persist.rehydrate();
    expect(fileMeshSubmission()).toEqual(request);
  });

  it.each(['engine', 'accuracy'] as const)('honors a user %s choice after ending exact replay', async control => {
    const request = exactRequest();
    request.options.engine = 'metal';
    request.options.accuracy = 'accurate';
    await loadMeshReplay('{}', vi.fn(async () => response({ record, request })));
    if (control === 'engine') useImportedSolveOptionsStore.getState().setEngine('bempp');
    else useImportedSolveOptionsStore.getState().setAccuracy('fast');
    const options = fileMeshSubmission().options;
    expect(options.engine).toBe(control === 'engine' ? 'bempp' : 'auto');
    expect(options.accuracy).toBeUndefined();
    expect(useMeshImportStore.getState().notice).toContain('starts a new setup');
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
