import { create } from 'zustand';
import {
  inspectMesh, prepareMeshModel, registerMesh, type MeshSummary, type RegisteredMesh,
} from '../api/meshImport';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { workspaceModeStore } from './workspaceMode';
import {
  applyWireOptionsToImportedSolveOptions, seedImportedSolveOptionsForFile,
  useImportedSolveOptionsStore, type SolveOptions,
} from './solveOptions';
import type { ImportedSolveSubmission } from '../jobs/actions';
import { noteSolveSettingsEdit, subscribeSolveSettingsEdits } from './solveSettingsEdits';
import { openMeshReplay } from '../api/meshReplay';
import { bindImportedSetup, importedSetupOf, type ImportedSetup } from '../jobs/importedSetup';
import { saveImportedSetup } from '../api/importedModels';
import {
  editFileDraft, importedModelBlocker, isCurrentOpen, openImported, registerImportedView, useImportedModelStore,
  type ImportedView,
} from './importedModel';

/**
 * The file rail: a view of the imported-model store for a file mesh.
 *
 * The model, its prepared mesh, how it is prepared (`fileDraft`) and the
 * setup it was opened with live in `stores/importedModel.ts`. This holds what
 * only the rail needs: the file being read, its groups, the source motions on
 * screen and the rail's own words.
 */
interface MeshImportState {
  filename: string;
  /** The original's text, while this browser read it; a restored file has none (the server keeps it). */
  original: string | null;
  summary: MeshSummary | null;
  motions: Record<string, 'normal' | 'axial'>;
  busy: boolean;
  error: string | null;
  notice: string | null;
  request: number;
}
const initial: MeshImportState = {
  filename: '', original: null, summary: null, motions: {}, busy: false, error: null, notice: null, request: 0,
};
let requestNumber = 0;
export const useMeshImportStore = create<MeshImportState>(() => ({ ...initial }));

// A setting changed after a save: the saved words no longer describe the screen.
subscribeSolveSettingsEdits((edit) => {
  if (edit?.source !== 'solveOptions' && useMeshImportStore.getState().notice) useMeshImportStore.setState({ notice: null });
});

/** The prepared mesh the file rail shows, or null. */
export function registeredMesh(): RegisteredMesh | null {
  const state = useImportedModelStore.getState();
  return state.input === 'file' ? state.prepared as RegisteredMesh | null : null;
}

/** Establish file ownership before reading bytes, including failed replacements. */
export function beginMeshFileImport(filename: string): number {
  const request = ++requestNumber;
  useMeshImportStore.setState({ ...initial, filename, busy: true, request });
  // Switch to file input first: leaving CAD parks its model and options before
  // the file's are seeded over the shared instance.
  workspaceModeStore.setImportedInput('file');
  // A new file has no saved setup: open with a one-way copy of the parametric
  // options (ground plane off, strict validation).
  seedImportedSolveOptionsForFile();
  void openImported({ kind: 'file', prepared: null });
  workspaceModeStore.setMode('cad');
  return request;
}

/** Changing how the file is prepared leaves the preview visible but never solvable. */
export function editMeshImport(patch: Partial<{ units: string; forward: string; up: string; selected: number[] }>) {
  editFileDraft(patch);
  useMeshImportStore.setState({ error: null, notice: null });
}

/** A source's motion is part of the setup: changing it ends the setup the file was opened with. */
export function setMeshMotion(sourceId: string, motion: 'normal' | 'axial'): void {
  useMeshImportStore.setState((state) => ({ motions: { ...state.motions, [sourceId]: motion } }));
  noteSolveSettingsEdit({ source: 'meshImport', control: 'motion' });
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
    useMeshImportStore.setState({ summary, busy: false });
    editFileDraft({ selected: summary.groups.filter(group => /^(LF|MF|HF|source|drive)$/i.test(group.name)).map(group => group.tag) });
  } catch (error) {
    if (importedMeshStore.isCurrentGeneration(generation)) {
      useMeshImportStore.setState({ busy: false, error: String(error) });
    }
    throw error;
  } finally {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false });
  }
}

/** Prepare the file as the rail is set: from the original in hand, or the one the server retained. */
export async function prepareMeshFile(fetcher: typeof fetch = fetch): Promise<void> {
  const state = useMeshImportStore.getState();
  const model = useImportedModelStore.getState();
  const draft = model.fileDraft;
  const modelId = model.input === 'file' ? model.modelId : null;
  if (state.busy || (!state.original && !(modelId && model.originalAvailable))) return;
  const generation = importedMeshStore.beginIntent();
  const request = ++requestNumber;
  useMeshImportStore.setState({ busy: true, error: null, request });
  try {
    const preparation = { units: draft.units, forward: draft.forward, up: draft.up, source_tags: draft.selected };
    // The same file prepared the same way is the same retained mesh. Without
    // the original in hand (a restored file), the server prepares its own copy.
    const record = state.original
      ? await registerMesh({ filename: state.filename, msh_text: state.original, ...preparation }, fetcher)
      : await prepareMeshModel(modelId!, preparation, fetcher);
    if (!importedMeshStore.isCurrentGeneration(generation)) return;
    // Form edits during the request invalidate its result even when the viewport
    // has not changed. A submitted snapshot cannot acquire newer assignments.
    const current = useImportedModelStore.getState().fileDraft;
    if (useMeshImportStore.getState().original !== state.original || JSON.stringify(current) !== JSON.stringify(draft)) return;
    if (importedMeshStore.setFile(preparedFileScene(record), generation)) {
      useMeshImportStore.setState({ summary: state.summary ?? record.summary });
      await openImported({ kind: 'file', prepared: record });
    }
  } catch (error) {
    if (importedMeshStore.isCurrentGeneration(generation)) useMeshImportStore.setState({ error: error instanceof Error ? error.message : String(error) });
  } finally {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false });
  }
}

export function fileMeshSubmission(): ImportedSolveSubmission {
  const state = useMeshImportStore.getState();
  const record = registeredMesh();
  const viewport = importedMeshStore.getSnapshot();
  const opening = importedModelBlocker('file');
  if (opening) throw new Error(opening);
  if (state.busy) throw new Error('Wait for mesh preparation to finish.');
  if (!record || viewport.showing !== 'file' || viewport.file?.ingestId !== record.ingest_id) {
    throw new Error('Choose units and driven groups, then prepare this mesh before solving.');
  }
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

function preparedFileScene(record: RegisteredMesh) {
  return createImportedMeshScene(record.source_file.filename, parseMSH(record.msh_text), 'file', record.ingest_id,
    [], { drivenTags: Object.values(record.source_tags) });
}

function assertFileRecord(record: RegisteredMesh, setup: ImportedSetup | null, preparedId: string): void {
  if (record.ingest_id !== preparedId || record.producer !== 'mesh-file' || typeof record.msh_text !== 'string'
    || (setup && !Array.isArray(setup.geometry?.drive_channels))) {
    throw new Error('The retained mesh response does not match its run. Select the run again.');
  }
}

/** Show one prepared file mesh with the setup it opens with (none: the options seeded for a file). */
function fillFileRail(record: RegisteredMesh, setup: ImportedSetup | null, generation: number): boolean {
  if (!importedMeshStore.isCurrentGeneration(generation)) return false;
  if (!importedMeshStore.setFile(preparedFileScene(record), generation)) return false;
  workspaceModeStore.setMode('cad');
  // The setup's own options seed the imported instance, over a copy of the
  // parametric options for whatever the setup does not state.
  seedImportedSolveOptionsForFile();
  if (setup) applyWireOptionsToImportedSolveOptions(setup.options as Partial<SolveOptions>);
  useImportedSolveOptionsStore.setState({ groundPlane: { enabled: false, axis: 'y', height_m: 0 } });
  useMeshImportStore.setState({ ...initial, filename: record.source_file.filename, summary: record.summary,
    motions: Object.fromEntries((setup?.geometry.drive_channels ?? []).map(channel => [channel.id, channel.motion ?? 'normal'])),
    request: useMeshImportStore.getState().request,
  });
  return true;
}

/** The file rail as the view a reopened file model (a run, or the saved setup) is shown in. */
export const fileImportedView: ImportedView = {
  begin({ generation }) {
    const request = ++requestNumber;
    useMeshImportStore.setState({ ...initial, filename: 'Restoring mesh…', busy: true, request });
    workspaceModeStore.setMode('cad');
    seedImportedSolveOptionsForFile();
    const current = () => isCurrentOpen(generation) && useMeshImportStore.getState().request === request;
    return {
      current,
      async fill(opened) {
        const record = opened.prepared as unknown as RegisteredMesh;
        assertFileRecord(record, opened.setup, opened.prepared.ingest_id);
        if (!current()) return null;
        if (!fillFileRail(record, opened.setup, generation)) return null;
        if (opened.note) useMeshImportStore.setState({ notice: opened.note });
        return record;
      },
      fail(message) {
        if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false, error: `Could not restore this mesh setup: ${message}` });
      },
    };
  },
};
registerImportedView('file', fileImportedView);

/** A replay's exact setup is what an unedited Solve sends, until a setup control changes. */
export async function loadMeshReplay(text: string, fetcher: typeof fetch = fetch,
  generation = importedMeshStore.beginIntent()): Promise<boolean> {
  if (!importedMeshStore.isCurrentGeneration(generation)) return false;
  beginMeshFileImport('Opening mesh replay…');
  const requestNumberAtStart = ++requestNumber;
  useMeshImportStore.setState({ busy: true, error: null, request: requestNumberAtStart });
  try {
    const opened = await openMeshReplay(text, fetcher);
    if (!importedMeshStore.isCurrentGeneration(generation)) return false;
    const setup = importedSetupOf(opened.request);
    assertFileRecord(opened.record, setup, opened.request.geometry.ingest_id);
    if (opened.request.geometry.manifest_sha256 !== opened.record.manifest_sha256
      || opened.request.geometry.artifact_sha256 !== opened.record.artifact_sha256) {
      throw new Error('The mesh replay response does not match its retained mesh.');
    }
    if (!fillFileRail(opened.record, setup, generation)) return false;
    await openImported({ kind: 'file', prepared: opened.record, setup });
    useMeshImportStore.setState({ notice: 'Mesh replay opened. Solve preserves its exact settings. Editing a setup or solve control starts a new setup.' });
    return true;
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
  const ingest_id = submission.geometry.ingest_id;
  const modelId = registeredMesh()!.model_id;
  await saveImportedSetup(modelId, ingest_id, importedSetupOf(submission), fetcher);
  if (registeredMesh()?.ingest_id !== ingest_id) return;
  let unchanged = false;
  try { unchanged = JSON.stringify(fileMeshSubmission()) === JSON.stringify(submission); } catch { unchanged = false; }
  useMeshImportStore.setState({ notice: unchanged
    ? 'Mesh setup saved with this model. Reopening the model, or importing the same file again, restores it.'
    : 'Earlier mesh settings were saved. Your current edits are not saved.' });
}
