import { fileSimulationSetup, importedDriverLabel, reconcileFileChannels } from './importedSimulationSetup';
import { savedChannelDrivers } from './cadSetupRestore';
import type { CadSetup } from '../api/jobsSocket';
import type { SimulationData } from './cadReturn';
import { exteriorModeBlocker, importedSweepRangeInvalid, incompleteDriverBlocker, simulationDriverGeometry, simulationDriverReferences, simulationEffectsGeometry } from '../jobs/importedSubmission';
import { create } from 'zustand';
import {
  inspectMesh, inspectMeshModel, prepareMeshModel, registerMesh, type MeshSummary, type RegisteredMesh,
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
import { subscribeSolveSettingsEdits } from './solveSettingsEdits';
import { openMeshReplay } from '../api/meshReplay';
import { bindImportedSetup, importedSetupOf, type ImportedSetup } from '../jobs/importedSetup';
import { ImportedModelApiError } from '../api/importedModels';
import {
  editFileDraft, effectiveImportedSetup, flushImportedSetupWrites, importedModelBlocker, isCurrentOpen, openImported,
  registerImportedSetupControls, registerImportedView, useImportedModelStore,
  type ImportedView, type FileDraft,
} from './importedModel';

/**
 * The file rail: a view of the imported-model store for a file mesh.
 *
 * The model, its prepared mesh, how it is prepared (`fileDraft`) and the
 * setup it was opened with live in `stores/importedModel.ts`. This holds what
 * only the rail needs: the file being read, its groups and the rail's own words.
 */
interface MeshImportState {
  filename: string;
  /** The original's text, while this browser read it; a restored file has none (the server keeps it). */
  original: string | null;
  summary: MeshSummary | null;
  busy: boolean;
  error: string | null;
  notice: string | null;
  request: number;
}
const initial: MeshImportState = {
  filename: '', original: null, summary: null, busy: false, error: null, notice: null, request: 0,
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
  // Flush the leaving model before the reading state replaces its rail.
  void flushImportedSetupWrites().catch(() => undefined);
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
export async function editMeshImport(patch: Partial<FileDraft>, fetcher: typeof fetch = fetch): Promise<void> {
  editFileDraft(patch);
  useMeshImportStore.setState({ error: null, notice: null });
  await prepareMeshFile(fetcher, patch.forward !== undefined || patch.up !== undefined);
}

/** A missing guess waits at its field instead of sending an incomplete preparation. */
export function meshImportChoice(draft: FileDraft): string | null {
  if (!draft.units) return "Choose the file's units.";
  if (Object.values(draft.roles ?? {}).some(role => role === null) || !draft.selected.length) {
    return 'Choose which surface is a driver.';
  }
  return null;
}

export async function loadMeshFile(filename: string, text: string, fetcher: typeof fetch = fetch, generation = importedMeshStore.beginIntent()): Promise<void> {
  if (!importedMeshStore.isCurrentGeneration(generation)) return;
  beginMeshFileImport(filename);
  const request = ++requestNumber;
  useMeshImportStore.setState({ ...initial, filename, original: text, busy: true, request });
  try {
    await flushImportedSetupWrites();
    if (!importedMeshStore.isCurrentGeneration(generation)) return;
    let summary = await inspectMesh(filename, text, fetcher);
    if (!importedMeshStore.isCurrentGeneration(generation)) return;
    if (summary.known_model) {
      const known = summary.known_model;
      try {
        if (await openImported({ kind: 'model', modelId: known.model_id, preparedId: known.latest_prepared_id }, fetcher)) {
          useMeshImportStore.setState({ notice: 'Ready to solve · settings from your last import' });
        }
        return;
      } catch (error) {
        // The user already supplied the remedy for missing retained artifacts:
        // rebuild from these bytes through the normal registration below.
        if (!(error instanceof ImportedModelApiError) || error.code !== 'prepared_unavailable') throw error;
        generation = importedMeshStore.beginIntent();
        beginMeshFileImport(filename);
        useMeshImportStore.setState({ original: text, summary, busy: false });
      }
    }
    const previous = summary.previous_setup;
    if (previous && (previous.preparation.forward !== '+z' || previous.preparation.up !== '+y')) {
      const { units, forward, up } = previous.preparation;
      summary = await inspectMesh(filename, text, fetcher, { units, forward, up });
      if (!importedMeshStore.isCurrentGeneration(generation)) return;
    }
    const roles = Object.fromEntries(summary.groups.map(group => [group.tag,
      previous?.preparation.roles?.[group.tag] ?? summary.previous_roles?.[group.tag]
      ?? (group.needs_role_choice ? null : group.suggested_role ?? 'RIGID')]));
    const scene = createImportedMeshScene(filename, parseMSH(text), 'file', null, [], { rolesByTag: roles });
    if (!importedMeshStore.setFile(scene, generation)) return;
    useMeshImportStore.setState({ summary, busy: false });
    const preparation = previous?.preparation;
    editFileDraft({ roles, units: preparation?.units ?? summary.suggested_units ?? '',
      forward: preparation?.forward ?? '+z', up: preparation?.up ?? '+y',
      domainPlanes: preparation?.domain_planes ?? summary.detected_planes ?? [],
      reflectPlanes: preparation?.reflect_planes ?? null, removeLidPlanes: preparation?.remove_lid_planes ?? [] });
    if (previous?.setup) {
      seedFileOptions(previous.setup);
      useImportedModelStore.setState({ setup: structuredClone(previous.setup) });
    }
    if (previous) {
      const time = new Date(previous.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      useMeshImportStore.setState({ notice: `Using the settings from ${previous.filename} (${time})` });
    }
    await prepareMeshFile(fetcher);
  } catch (error) {
    if (importedMeshStore.isCurrentGeneration(generation)) {
      useMeshImportStore.setState({ busy: false, error: error instanceof Error ? error.message : String(error) });
    }
    throw error;
  } finally {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false });
  }
}

/** Prepare the file as the rail is set: from the original in hand, or the one the server retained. */
export async function prepareMeshFile(fetcher: typeof fetch = fetch, inspectFrame = false): Promise<void> {
  const state = useMeshImportStore.getState();
  const model = useImportedModelStore.getState();
  let draft = model.fileDraft;
  const modelId = model.input === 'file' ? model.modelId : null;
  if (model.input !== 'file' || meshImportChoice(draft) || (!state.original && !(modelId && model.originalAvailable))) return;
  const generation = importedMeshStore.beginIntent();
  const request = ++requestNumber;
  useMeshImportStore.setState({ busy: true, error: null, request });
  try {
    // Detection follows the chosen axes; registration still owns all verification.
    if (inspectFrame || (!state.original && draft.domainPlanes?.length && !state.summary?.lids)) {
      const frame = { units: draft.units, forward: draft.forward, up: draft.up };
      const summary = state.original ? await inspectMesh(state.filename, state.original, fetcher, frame)
        : await inspectMeshModel(modelId!, frame, fetcher);
      if (!importedMeshStore.isCurrentGeneration(generation)
        || JSON.stringify(useImportedModelStore.getState().fileDraft) !== JSON.stringify(draft)) return;
      useMeshImportStore.setState({ summary });
      if (inspectFrame) editFileDraft({ domainPlanes: summary.detected_planes ?? [], reflectPlanes: null, removeLidPlanes: [] });
      draft = useImportedModelStore.getState().fileDraft;
    }
    const preparation = { units: draft.units, forward: draft.forward, up: draft.up,
      roles: Object.fromEntries(Object.entries(draft.roles ?? Object.fromEntries(draft.selected.map(tag => [tag, 'HF'])))
        .map(([tag, role]) => [tag, role ?? 'RIGID'])),
      domain_planes: draft.domainPlanes ?? [], reflect_planes: draft.reflectPlanes ?? null,
      remove_lid_planes: draft.removeLidPlanes ?? [] };

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
      useMeshImportStore.setState({ summary: useMeshImportStore.getState().summary ?? record.summary });
      const latest = useImportedModelStore.getState();
      const retained = latest.setup ?? latest.setupEditBase?.retained;
      await openImported({ kind: 'file', prepared: record, setup: retained ? setupForPreparedFile(retained, record, latest.fileSimulation) : null });
    }
  } catch (error) {
    if (importedMeshStore.isCurrentGeneration(generation)
      && JSON.stringify(useImportedModelStore.getState().fileDraft) === JSON.stringify(draft)) {
      useMeshImportStore.setState({ error: error instanceof Error ? error.message : String(error) });
    }
  } finally {
    if (useMeshImportStore.getState().request === request) useMeshImportStore.setState({ busy: false });
  }
}

/** Preparation edits only the source assignments that no longer exist. */
function setupForPreparedFile(retained: ImportedSetup, record: RegisteredMesh, previous: SimulationData | null): ImportedSetup {
  const setup = structuredClone(retained);
  const available = new Set(Object.keys(record.source_tags));
  setup.geometry.skipped_source_ids = setup.geometry.skipped_source_ids.filter(id => available.has(id));
  const forms = { ...savedChannelDrivers(setup.geometry as unknown as CadSetup, setup.geometry.drive_channels, setup.driver_references),
    ...previous?.channelDrivers };
  const extended = reconcileFileChannels(record, setup.geometry.drive_channels, forms, setup.geometry.skipped_source_ids);
  setup.geometry.drive_channels = extended.channels;
  const driven = new Set(setup.geometry.drive_channels.flatMap(channel => channel.source_ids));
  if (setup.geometry.mesh) {
    setup.geometry.mesh.source_size_mm = Object.fromEntries(Object.entries(setup.geometry.mesh.source_size_mm)
      .filter(([id]) => driven.has(id)));
    // Fresh file channels have no generation sizes. Do not invent a size for a new source.
    if ([...driven].some(id => setup.geometry.mesh!.source_size_mm[id] === undefined)) delete setup.geometry.mesh;
  }
  if (setup.driver_references) {
    const references = setup.driver_references;
    setup.driver_references = Object.fromEntries(setup.geometry.drive_channels.flatMap(channel => {
      const reference = references[extended.from[channel.id] ?? channel.id];
      return reference ? [[channel.id, reference]] : [];
    }));
  }
  if (setup.geometry.combine && Object.keys(extended.renamed).length) {
    const combine = setup.geometry.combine;
    combine.members = combine.members.map(id => extended.renamed[id] ?? id);
    if (combine.reference) combine.reference = extended.renamed[combine.reference] ?? combine.reference;
    if (combine.channels) combine.channels = Object.fromEntries(Object.entries(combine.channels)
      .map(([id, value]) => [extended.renamed[id] ?? id, value]));
  }
  if (setup.geometry.combine?.members.some(id => !setup.geometry.drive_channels.some(channel => channel.id === id))) {
    delete setup.geometry.combine;
  }
  return setup;
}

export function fileMeshSubmission(): ImportedSolveSubmission {
  const setup = fileMeshSetup();
  return bindImportedSetup(registeredMesh()!, setup);
}

/** The same effective setup Save records and Solve submits. */
export function fileMeshSetup(): ImportedSetup {
  const state = useMeshImportStore.getState();
  const record = registeredMesh();
  const viewport = importedMeshStore.getSnapshot();
  const opening = importedModelBlocker('file');
  if (opening) throw new Error(opening);
  if (state.busy) throw new Error('Wait for mesh preparation to finish.');
  if (!record || viewport.showing !== 'file' || viewport.file?.ingestId !== record.ingest_id) {
    throw new Error('Choose units and surface roles in Geometry before solving.');
  }
  return effectiveImportedSetup('file', record.ingest_id, fileSetupControls);
}

function fileSetupControls(): ImportedSetup {
  // Every option, the sweep range and count included, is the imported
  // instance's; this view only adds the mesh's geometry and source motions.
  const selected = useImportedSolveOptionsStore.getState();
  const options = selected.options() as ImportedSolveSubmission['options'];
  const simulation = fileSimulationSetup.get();
  if (selected.frequencyMode === 'range') {
    if (importedSweepRangeInvalid(simulation, 2)) {
      throw new Error('Enter an increasing frequency range and 2–401 frequencies.');
    }
    options.frequency_range = [simulation.frequencyStartHz, simulation.frequencyEndHz];
    options.num_frequencies = simulation.frequencyCount;
  }
  // The file rail has no ground plane control, so none is ever sent.
  delete options.ground_plane;
  options.symmetry = 'auto';
  options.solver_mode = 'full_3d';
  const label = (id: string) => importedDriverLabel(simulation, id);
  const exteriorError = exteriorModeBlocker({ ...simulation, ingestRecord: null, exteriorOnly: true }, selected, label);
  if (exteriorError) throw new Error(exteriorError);
  const driverError = incompleteDriverBlocker(simulation, label);
  if (driverError) throw new Error(driverError);
  const driver_references = simulationDriverReferences(simulation);
  const model = useImportedModelStore.getState();
  const retained = model.setup ?? model.setupEditBase?.retained;
  // Motion edits of older file setups preserve their voltage fields.
  const geometry = simulationDriverGeometry(simulation, retained?.geometry);
  return {
    geometry: { skipped_source_ids: [], ...geometry, ...simulationEffectsGeometry(simulation, simulation.sources) },
    ...(Object.keys(driver_references).length ? { driver_references } : {}),
    options,
  };
}
registerImportedSetupControls('file', fileSetupControls);

function preparedFileScene(record: RegisteredMesh) {
  return createImportedMeshScene(record.source_file.filename, parseMSH(record.msh_text), 'file', record.ingest_id,
    record.symmetry?.domain_planes ?? [], { rolesByTag: {
      ...Object.fromEntries((record.summary.groups ?? []).map(group => [group.tag, 'RIGID'])),
      ...Object.fromEntries(Object.entries(record.source_tags).map(([id, tag]) =>
        [tag, record.sources?.find(source => source.id === id)?.role ?? 'HF'])),
    } });
}

function assertFileRecord(record: RegisteredMesh, setup: ImportedSetup | null, preparedId: string): void {
  if (record.ingest_id !== preparedId || record.producer !== 'mesh-file' || typeof record.msh_text !== 'string'
    || (setup && !Array.isArray(setup.geometry?.drive_channels))) {
    throw new Error('The retained mesh response does not match its run. Select the run again.');
  }
}

/** Restore file options from the same baseline for every open path. */
function seedFileOptions(setup: ImportedSetup | null): void {
  // The setup's own options seed the imported instance, over a copy of the
  // parametric options for whatever the setup does not state.
  seedImportedSolveOptionsForFile();
  if (setup) applyWireOptionsToImportedSolveOptions(setup.options as Partial<SolveOptions>);
  useImportedSolveOptionsStore.setState({ groundPlane: { enabled: false, axis: 'y', height_m: 0 } });
}

/** Show one prepared file mesh with the setup it opens with (none: the options seeded for a file). */
function fillFileRail(record: RegisteredMesh, setup: ImportedSetup | null, generation: number): boolean {
  if (!importedMeshStore.isCurrentGeneration(generation)) return false;
  if (!importedMeshStore.setFile(preparedFileScene(record), generation)) return false;
  workspaceModeStore.setMode('cad');
  seedFileOptions(setup);
  useMeshImportStore.setState({ ...initial, filename: record.source_file.filename,
    summary: { ...record.summary, kept_sides: { ...record.summary.kept_sides,
      ...Object.fromEntries((record.symmetry?.reflected_planes ?? []).map(plane => [plane, 'negative' as const])),
    } },
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
    useMeshImportStore.setState({ notice: 'Mesh replay opened. Solve keeps its settings. Edits keep the other settings.' });
    return true;
  } catch (error) {
    if (importedMeshStore.isCurrentGeneration(generation)) useMeshImportStore.setState({ error: error instanceof Error ? error.message : String(error) });
    throw error;
  } finally {
    if (useMeshImportStore.getState().request === requestNumberAtStart) useMeshImportStore.setState({ busy: false });
  }
}
