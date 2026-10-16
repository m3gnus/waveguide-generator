import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { workspaceModeStore } from './workspaceMode';
import { resetSolveOptionsStore } from './solveOptions';
import { editMeshImport, fileMeshSubmission, fileSolvePress, finishFileSolvePress, loadMeshFile,
  prepareMeshFile, restoreMeshImport, restorePendingMeshPress, useMeshImportStore } from './meshImport';
import type { RegisteredMesh, MeshSummary } from '../api/meshImport';
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

beforeEach(() => {
  useMeshImportStore.setState(initial, true);
  importedMeshStore.clear();
  workspaceModeStore.setMode('parametric');
  resetSolveOptionsStore();
  sessionStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

describe('standalone imported mesh', () => {
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
    expect(body.options.polar_config.angle_range).toEqual([-180, 180, 73]);
    editMeshImport({ units: 'in' });
    expect(() => fileMeshSubmission()).toThrow('Choose units');
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
    expect(await restoreMeshImport(job, vi.fn(async () => response(record)))).toBe(true);
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(useMeshImportStore.getState().original).toBeNull();
    expect(fileMeshSubmission().options.frequency_range).toEqual([100, 200]);
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
    await restoreMeshImport(job, vi.fn(async () => response(record)));
    const body = fileMeshSubmission();
    expect(body.options).toMatchObject({ frequency_range: [250, 8000], num_frequencies: 17, engine: 'metal', accuracy: 'accurate' });
    expect(body.geometry.drive_channels[0].motion).toBe('axial');
  });

  it('shows excitation by registered tag instead of trusting a group named HF', async () => {
    const selectedRigid = { ...record, source_tags: { group_1: 1 } };
    const job = { cad_source: { ingest_id: record.ingest_id }, solve_options: {} } as unknown as JobItem;
    await restoreMeshImport(job, vi.fn(async () => response(selectedRigid)));
    const surfaces = importedMeshStore.getSnapshot().file!.scene.surfaces;
    expect(surfaces.find(surface => surface.role.includes('Rigid'))?.materialClass).toBe('source-smooth');
    expect(surfaces.find(surface => surface.role.includes('HF'))?.materialClass).toBe('horn-smooth');
  });

  it('recovers the same ingest and pending press after a reload, without another registration', async () => {
    useMeshImportStore.setState({ registered: record, motions: { group_2: 'axial' }, startHz: 250, endHz: 8000, count: 17 });
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
