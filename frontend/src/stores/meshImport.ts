import { create } from 'zustand';
import { inspectMesh, registerMesh, readMeshImport, type MeshSummary, type RegisteredMesh } from '../api/meshImport';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { workspaceModeStore } from './workspaceMode';
import {
  applyWireOptionsToImportedSolveOptions, seedImportedSolveOptionsForFile,
  useImportedSolveOptionsStore, type SolveOptions,
} from './solveOptions';
import type { ImportedSolveSubmission } from '../jobs/actions';
import { subscribeSolveSettingsEdits } from './solveSettingsEdits';
import { openMeshReplay } from '../api/meshReplay';
import { bindImportedSetup } from '../jobs/importedSetup';
import { readImportedSetup, saveImportedSetup } from '../api/importedModels';
import { namespaceStorage } from './durableSettings';
import type { JobItem } from '../api/jobsSocket';
import { restoreImportedRun, assertRestoredIdentity, importedRunRestoreBlocker } from '../jobs/importedRunRestoration';

interface MeshImportState {
  filename: string;
  original: string | null;
  summary: MeshSummary | null;
  units: string;
  forward: string;
  up: string;
  selected: number[];
  registered: RegisteredMesh | null;
  motions: Record<string, 'normal' | 'axial'>;
  busy: boolean;
  error: string | null;
  notice: string | null;
  request: number;
  exactSubmission: ImportedSolveSubmission | null;
}
const initial: MeshImportState = {
  filename: '', original: null, summary: null, units: '', forward: '+z', up: '+y', selected: [],
  registered: null, motions: {}, busy: false, error: null, notice: null, request: 0, exactSubmission: null,
};
const savedSetupStorage = namespaceStorage('meshImport');
let requestNumber = 0;
export const useMeshImportStore = create<MeshImportState>(() => ({ ...initial }));

/** The imported options a file setup is made of; the sweep and list live there. */
function importedOptionsKey(): string {
  const o = useImportedSolveOptionsStore.getState();
  return JSON.stringify([o.engine, o.accuracy, o.sweepStartHz, o.sweepEndHz, o.sweepCount, o.frequencyMode,
    o.frequencyListText, o.frequencySpacing, o.polar, o.adaptiveFrequencySampling, o.meshValidationMode, o.verbose]);
}
function setupFingerprint(state: MeshImportState): string {
  return JSON.stringify([state.registered?.ingest_id, state.units, state.forward, state.up, state.selected,
    state.motions, importedOptionsKey(), state.exactSubmission]);
}
const EDITED_REPLAY_NOTICE = 'Replay settings changed. Solve now uses the mesh setup controls and starts a new setup.';
useMeshImportStore.subscribe((state, previous) => {
  if (state.exactSubmission && state.exactSubmission === previous.exactSubmission
    && setupFingerprint(state) !== setupFingerprint(previous)) {
    useMeshImportStore.setState({ exactSubmission: null, notice: EDITED_REPLAY_NOTICE });
    return;
  }
  if (state.notice === previous.notice && state.notice?.startsWith('Mesh setup saved.')
    && setupFingerprint(state) !== setupFingerprint(previous)) useMeshImportStore.setState({ notice: null });
});
useImportedSolveOptionsStore.subscribe((state, previous) => {
  if (useMeshImportStore.getState().notice?.startsWith('Mesh setup saved.') && state !== previous
    && JSON.stringify([state.engine, state.accuracy, state.sweepStartHz, state.sweepEndHz, state.sweepCount, state.frequencyMode,
      state.frequencyListText, state.frequencySpacing, state.polar])
    !== JSON.stringify([previous.engine, previous.accuracy, previous.sweepStartHz, previous.sweepEndHz, previous.sweepCount,
      previous.frequencyMode, previous.frequencyListText, previous.frequencySpacing, previous.polar])) {
    useMeshImportStore.setState({ notice: null });
  }
});

// Hydration and recalled designs use setState without an edit signal. They must
// not replace a portable replay's exact settings. Only a person operating a
// solve-options control ends replay; engine/accuracy are a coupled selection.
subscribeSolveSettingsEdits(edit => {
  if (edit?.source !== 'importedSolveOptions' || !useMeshImportStore.getState().exactSubmission) return;
  useMeshImportStore.setState({ exactSubmission: null, notice: EDITED_REPLAY_NOTICE });
});

/** Establish file ownership before reading bytes, including failed replacements. */
export function beginMeshFileImport(filename: string): number {
  const request = ++requestNumber;
  useMeshImportStore.setState({ ...initial, filename, busy: true, request });
  // Switch to file input first: leaving CAD saves its options before the file's
  // are seeded over the shared instance.
  workspaceModeStore.setImportedInput('file');
  // A new file has no saved setup: open with a one-way copy of the parametric
  // options (ground plane off, strict validation). A restore overwrites this
  // with the saved setup's own options.
  seedImportedSolveOptionsForFile();
  workspaceModeStore.setMode('cad');
  return request;
}

/** Invalidating registration leaves the preview visible but never solvable. */
export function editMeshImport(patch: Partial<Pick<MeshImportState, 'units' | 'forward' | 'up' | 'selected'>>) {
  useMeshImportStore.setState({ ...patch, registered: null, error: null });
}

export async function loadMeshFile(filename: string, text: string, fetcher: typeof fetch = fetch, generation = importedMeshStore.beginIntent()): Promise<void> {
  if (!importedMeshStore.isCurrentGeneration(generation)) return;
  beginMeshFileImport(filename);
  const request = ++requestNumber;
  useMeshImportStore.setState({ ...initial, filename, original: text, busy: true, request });
  try {
    const summary = await inspectMesh(filename, text, fetcher);
    if (!importedMeshStore.isCurrentGeneration(generation)) return;
    const scene = createImportedMeshScene(filename, parseMSH(text));
    if (!importedMeshStore.setFile(scene, generation)) return;
    useMeshImportStore.setState({ summary, selected: summary.groups.filter(group => /^(LF|MF|HF|source|drive)$/i.test(group.name)).map(group => group.tag), busy: false });
  } catch (error) {
    if (importedMeshStore.isCurrentGeneration(generation)) {
      useMeshImportStore.setState({ busy: false, error: String(error) });
    }
    throw error;
  } finally {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false });
  }
}

export async function prepareMeshFile(fetcher: typeof fetch = fetch): Promise<void> {
  const state = useMeshImportStore.getState();
  if (state.busy || !state.original) return;
  const generation = importedMeshStore.beginIntent();
  const request = ++requestNumber;
  useMeshImportStore.setState({ busy: true, error: null, request });
  try {
    const record = await registerMesh({ filename: state.filename, msh_text: state.original,
      units: state.units, forward: state.forward, up: state.up, source_tags: state.selected }, fetcher);
    if (!importedMeshStore.isCurrentGeneration(generation)) return;
    // Form edits during the request invalidate its result even when the viewport
    // has not changed. A submitted snapshot cannot acquire newer assignments.
    const current = useMeshImportStore.getState();
    if (current.original !== state.original || current.units !== state.units || current.forward !== state.forward
      || current.up !== state.up || JSON.stringify(current.selected) !== JSON.stringify(state.selected)) return;
    const scene = preparedFileScene(record);
    if (importedMeshStore.setFile(scene, generation)) useMeshImportStore.setState({ registered: record });
  } catch (error) {
    if (importedMeshStore.isCurrentGeneration(generation)) useMeshImportStore.setState({ error: error instanceof Error ? error.message : String(error) });
  } finally {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false });
  }
}

export function fileMeshSubmission(): ImportedSolveSubmission {
  const state = useMeshImportStore.getState();
  const record = state.registered;
  const viewport = importedMeshStore.getSnapshot();
  const restoring = importedRunRestoreBlocker('file');
  if (restoring) throw new Error(restoring);
  if (state.busy) throw new Error('Wait for mesh preparation to finish.');
  if (!record || viewport.showing !== 'file' || viewport.file?.ingestId !== record.ingest_id) {
    throw new Error('Choose units and driven groups, then prepare this mesh before solving.');
  }
  if (state.exactSubmission) return structuredClone(state.exactSubmission);
  // Every option, the sweep range and count included, is the imported
  // instance's; this view only adds the mesh's geometry and source motions.
  const selected = useImportedSolveOptionsStore.getState();
  const options = selected.options() as ImportedSolveSubmission['options'];
  if (selected.frequencyMode === 'range') {
    if (!(selected.sweepStartHz > 0) || !(selected.sweepEndHz > selected.sweepStartHz)
      || !Number.isInteger(selected.sweepCount) || selected.sweepCount < 2 || selected.sweepCount > 401) {
      throw new Error('Enter an increasing frequency range and 2–401 frequencies.');
    }
    options.frequency_range = [selected.sweepStartHz, selected.sweepEndHz];
    options.num_frequencies = selected.sweepCount;
  }
  // The file rail has no ground plane control, so none is ever sent.
  delete options.ground_plane;
  options.symmetry = 'full';
  options.solver_mode = 'full_3d';
  return bindImportedSetup(record, {
    geometry: { skipped_source_ids: [],
      drive_channels: Object.keys(record.source_tags).map(id => ({ id, source_ids: [id], motion: state.motions[id] ?? 'normal' })) },
    options,
  });
}

// Preserve existing file callers and recovery storage while sharing press identity.
export { importedSolvePress as fileSolvePress, finishImportedSolvePress as finishFileSolvePress } from '../jobs/importedSolvePress';

function preparedFileScene(record: RegisteredMesh) {
  return createImportedMeshScene(record.source_file.filename, parseMSH(record.msh_text), 'file', record.ingest_id,
    [], { drivenTags: Object.values(record.source_tags) });
}

type FileOptions = Partial<SolveOptions> & {
  frequencies_hz?: number[] | null;
  frequency_range?: number[] | null;
  num_frequencies?: number | null;
};

async function restoreRetainedFile(id: string, options: FileOptions,
  channels: Array<{ id: string; motion?: 'normal' | 'axial' }>, fetcher: typeof fetch,
  generation = importedMeshStore.beginIntent(),
  exactSubmission?: (record: RegisteredMesh) => ImportedSolveSubmission,
  current: () => boolean = () => true): Promise<boolean> {
  if (!importedMeshStore.isCurrentGeneration(generation)) return false;
  const record = await readMeshImport(id, fetcher);
  if (!importedMeshStore.isCurrentGeneration(generation) || !current()) return false;
  if (record.ingest_id !== id) throw new Error('The retained mesh response names a different import. Select the run again.');
  const submission = exactSubmission?.(record);
  if (submission) assertFileRequest(record, submission);
  return restoreFile(record, options, channels, generation, submission);
}

function assertFileRequest(record: RegisteredMesh, submission: ImportedSolveSubmission): void {
  assertRestoredIdentity(record, submission);
  if (record.producer !== 'mesh-file' || submission?.geometry?.type !== 'imported'
    || !submission.options || !Array.isArray(submission.geometry.drive_channels)) {
    throw new Error('The mesh replay response does not match its retained mesh.');
  }
}

function restoreFile(record: RegisteredMesh, options: FileOptions,
  channels: Array<{ id: string; motion?: 'normal' | 'axial' }>, generation: number,
  exactSubmission?: ImportedSolveSubmission): boolean {
  if (!importedMeshStore.isCurrentGeneration(generation)) return false;
  if (!importedMeshStore.setFile(preparedFileScene(record), generation)) return false;
  workspaceModeStore.setImportedInput('file');
  workspaceModeStore.setMode('cad');
  // The saved setup's own options seed the imported instance, over a copy of
  // the parametric options for whatever the record does not state.
  seedImportedSolveOptionsForFile();
  applyWireOptionsToImportedSolveOptions(options as Partial<SolveOptions>);
  useImportedSolveOptionsStore.setState({ groundPlane: { enabled: false, axis: 'y', height_m: 0 } });
  useMeshImportStore.setState({ ...initial, filename: record.source_file.filename,
    summary: record.summary, registered: record, ...record.source_file.transform, selected: record.source_file.source_tags,
    motions: Object.fromEntries(channels.map(channel => [channel.id, channel.motion ?? 'normal'])),
    exactSubmission: exactSubmission ? structuredClone(exactSubmission) : null,
  });
  return true;
}

/** A replay's exact request remains authoritative until a setup control changes. */
export async function loadMeshReplay(text: string, fetcher: typeof fetch = fetch,
  generation = importedMeshStore.beginIntent()): Promise<boolean> {
  if (!importedMeshStore.isCurrentGeneration(generation)) return false;
  beginMeshFileImport('Opening mesh replay…');
  const requestNumberAtStart = ++requestNumber;
  useMeshImportStore.setState({ busy: true, error: null, request: requestNumberAtStart });
  try {
    const opened = await openMeshReplay(text, fetcher);
    if (!importedMeshStore.isCurrentGeneration(generation)) return false;
    assertFileRequest(opened.record, opened.request);
    const restored = restoreFile(opened.record, opened.request.options,
      opened.request.geometry.drive_channels, generation, opened.request);
    if (restored) useMeshImportStore.setState({ notice: 'Mesh replay opened. Solve preserves its exact settings. Editing a setup or solve control starts a new setup.' });
    return restored;
  } catch (error) {
    if (importedMeshStore.isCurrentGeneration(generation)) useMeshImportStore.setState({ error: error instanceof Error ? error.message : String(error) });
    throw error;
  } finally {
    if (useMeshImportStore.getState().request === requestNumberAtStart) useMeshImportStore.setState({ busy: false });
  }
}

/** Explicitly save before Solve. CAD project edits use this same setup schema. */
export async function saveMeshSetup(fetcher: typeof fetch = fetch): Promise<void> {
  const submission = fileMeshSubmission();
  const fingerprint = setupFingerprint(useMeshImportStore.getState());
  const { type: _type, ingest_id, manifest_sha256: _manifest, artifact_sha256: _artifact,
    acknowledged_findings: _findings, ...geometry } = submission.geometry;
  await saveImportedSetup(ingest_id, { geometry, options: submission.options }, fetcher);
  if (useMeshImportStore.getState().registered?.ingest_id === ingest_id) {
    savedSetupStorage.setItem('wg-saved-mesh-setup', ingest_id);
    useMeshImportStore.setState({ notice: fingerprint === setupFingerprint(useMeshImportStore.getState())
      ? 'Mesh setup saved. It can be resumed after restart.'
      : 'Earlier mesh settings were saved. Your current edits are not saved.' });
  }
}

export async function restoreSavedMeshSetup(fetcher: typeof fetch = fetch): Promise<boolean> {
  const id = savedSetupStorage.getItem('wg-saved-mesh-setup');
  if (!id) return false;
  const generation = importedMeshStore.beginIntent();
  beginMeshFileImport('Restoring mesh…');
  const request = ++requestNumber;
  useMeshImportStore.setState({ request });
  try {
    const { setup } = await readImportedSetup(id, fetcher);
    if (!setup || !importedMeshStore.isCurrentGeneration(generation)) return false;
    return await restoreRetainedFile(id, setup.options, setup.geometry.drive_channels, fetcher, generation, record => bindImportedSetup(record, setup));
  } finally {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false });
  }
}

/** An unanswered request restores its SAME retained mesh and setup, so a new
 * press replays its durable ID rather than registering a new ingest after reload. */
export async function restorePendingMeshPress(fetcher: typeof fetch = fetch): Promise<boolean> {
  let saved: { id?: string; body?: string } | null = null;
  try { saved = JSON.parse(sessionStorage.getItem('wg-mesh-solve-press') ?? 'null'); }
  catch { return false; }
  if (!saved?.id?.startsWith('mesh-') || !saved.body) return false;
  const body = JSON.parse(saved.body) as ImportedSolveSubmission;
  if (body.geometry?.type !== 'imported' || !body.geometry.ingest_id || !body.options) return false;
  const generation = importedMeshStore.beginIntent();
  beginMeshFileImport('Restoring mesh…');
  const request = ++requestNumber;
  useMeshImportStore.setState({ request });
  try {
    const restored = await restoreRetainedFile(body.geometry.ingest_id, body.options, body.geometry.drive_channels, fetcher, generation, () => body);
    if (restored) useMeshImportStore.setState({ notice: 'An unanswered mesh solve was restored. Press Solve to recover that request before starting another run.' });
    return restored;
  } finally {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false });
  }
}

/** Restore one retained file mesh with File selected in Imported mesh. */
export async function restoreMeshImport(job: JobItem, fetcher: typeof fetch = fetch): Promise<boolean> {
  let request = 0;
  try {
    return await restoreImportedRun(job, {
      input: 'file',
      invalidate: () => {
        beginMeshFileImport('Restoring mesh…');
        request = useMeshImportStore.getState().request;
      },
      restore: async (submission, generation, current) => restoreRetainedFile(submission.geometry.ingest_id,
        submission.options, submission.geometry.drive_channels, fetcher, generation, () => submission, current),
    }, fetcher);
  } catch (error) {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({
      error: `Could not restore this run’s mesh setup: ${error instanceof Error ? error.message : String(error)}`,
    });
    throw error;
  } finally {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false });
  }
}
