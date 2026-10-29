import { fileSimulationSetup, importedDriverLabel, reconcileFileChannels, seedFileSimulation } from './importedSimulationSetup';
import { driverGroupsFromWire, driverGroupsToWire } from './importedDriverGroups';
import { combineWire } from './cadReturn';
import { savedChannelDrivers } from './cadSetupRestore';
import type { CadSetup } from '../api/jobsSocket';
import type { SimulationData } from './cadReturn';
import { crossoverBandBlocker, exteriorModeBlocker, importedSweepRangeInvalid, incompleteDriverBlocker, simulationGeometry, simulationDriverReferences } from '../jobs/importedSubmission';
import { create } from 'zustand';
import {
  inspectMesh, inspectMeshModel, prepareMeshModel, registerMesh, type MeshSummary, type RegisteredMesh,
} from '../api/meshImport';
import { fullTime } from '../shell/cadTime';
import { createImportedMeshScene, previewImportedMeshRoles } from '../viewport/importedMesh';
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
  registerImportedPreparationFlush, registerImportedSetupControls, registerImportedView, useImportedModelStore,
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
  frameDirty: boolean;
}
const initial: MeshImportState = {
  filename: '', original: null, summary: null, busy: false, error: null, notice: null, request: 0, frameDirty: false,
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
  const state = useMeshImportStore.getState();
  useMeshImportStore.setState({ error: null, notice: null,
    frameDirty: state.frameDirty || patch.forward !== undefined || patch.up !== undefined });
  if (patch.roles || patch.selected) {
    const scene = importedMeshStore.getSnapshot().file;
    const draft = useImportedModelStore.getState().fileDraft;
    if (scene) importedMeshStore.setFile(previewImportedMeshRoles(scene,
      draft.roles ?? Object.fromEntries(draft.selected.map(tag => [tag, 'HF']))), importedMeshStore.captureGeneration());
  }
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
          useMeshImportStore.setState(state => ({ notice: [state.notice, 'Settings from your last import'].filter(Boolean).join(' · ') }));
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
      const time = fullTime(previous.updated_at);
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

type PreparedFileSetup = { modelId: string; preparedId: string; setup: ImportedSetup };
const preparationSaves = new Set<(setup: PreparedFileSetup) => void>();
/** The existing setup publisher records edits belonging to a rail that has left. */
export function subscribePreparedFileSetup(listener: (setup: PreparedFileSetup) => void): () => void {
  preparationSaves.add(listener);
  return () => { preparationSaves.delete(listener); };
}
let preparationPromise: Promise<void> | null = null;
let preparationIntent = 0;
registerImportedPreparationFlush(async () => { await preparationPromise; });

/** Prepare the file as the rail is set: from the original in hand, or the one the server retained. */
export function prepareMeshFile(fetcher: typeof fetch = fetch, inspectFrame = false): Promise<void> {
  const preparing = prepareMeshFileIntent(fetcher, inspectFrame, ++preparationIntent);
  preparationPromise = preparing;
  const settled = () => { if (preparationPromise === preparing) preparationPromise = null; };
  void preparing.then(settled, settled);
  return preparing;
}

async function prepareMeshFileIntent(fetcher: typeof fetch, inspectFrame: boolean, intent: number): Promise<void> {
  const state = useMeshImportStore.getState();
  const model = useImportedModelStore.getState();
  let draft = model.fileDraft;
  const modelId = model.input === 'file' ? model.modelId : null;
  if (model.input !== 'file' || meshImportChoice(draft) || (!state.original && !(modelId && model.originalAvailable))) return;
  const generation = importedMeshStore.beginIntent();
  const request = ++requestNumber;
  useMeshImportStore.setState({ busy: true, error: null, request });
  let retained: ImportedSetup | null = null;
  let simulation: SimulationData | null = null;
  const capture = () => {
    if (useMeshImportStore.getState().request !== request || useImportedModelStore.getState().input !== 'file') return;
    const latest = useImportedModelStore.getState();
    try {
      const controls = fileSetupControls();
      retained = structuredClone(latest.setup ?? latest.setupEditBase?.retained ?? controls);
      simulation = latest.fileSimulation ? structuredClone(latest.fileSimulation) : null;
    } catch { retained = null; } // Incomplete edits never fall back to an older valid setup.
  };
  capture();
  const unsubscribe = subscribeSolveSettingsEdits(edit => {
    if (edit?.source === 'meshImport' || edit?.source === 'importedSolveOptions') capture();
  });
  try {
    // Detection follows the chosen axes; registration still owns all verification.
    if (inspectFrame || state.frameDirty || (!state.original && draft.domainPlanes?.length && !state.summary?.lids)) {
      const frame = { units: draft.units, forward: draft.forward, up: draft.up };
      const summary = state.original ? await inspectMesh(state.filename, state.original, fetcher, frame)
        : await inspectMeshModel(modelId!, frame, fetcher);
      if (!importedMeshStore.isCurrentGeneration(generation)
        || JSON.stringify(useImportedModelStore.getState().fileDraft) !== JSON.stringify(draft)) return;
      useMeshImportStore.setState({ summary, frameDirty: false });
      if (inspectFrame || state.frameDirty) editFileDraft({ domainPlanes: summary.detected_planes ?? [], reflectPlanes: null, removeLidPlanes: [] });
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
    if (!importedMeshStore.isCurrentGeneration(generation)) {
      if (intent === preparationIntent && retained) {
        const setup = setupForPreparedFile(retained, record, simulation);
        preparationSaves.forEach(save => save({ modelId: record.model_id, preparedId: record.ingest_id, setup }));
      }
      return;
    }
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
    unsubscribe();
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
  const storedGroups = setup.geometry.driver_groups && driverGroupsFromWire(setup.geometry.driver_groups);
  // Only carry live choices from this setup. A freshly opened file may still
  // have an empty simulation while its saved setup is being prepared.
  const previousGroups = previous?.driveChannels.some(channel => setup.geometry.drive_channels.some(saved => saved.id === channel.id))
    ? previous.driverGroups : undefined;
  const extended = reconcileFileChannels(record, setup.geometry.drive_channels, forms, setup.geometry.skipped_source_ids,
    storedGroups ?? previousGroups);
  setup.geometry.drive_channels = extended.channels;
  const oldIds = new Set(retained.geometry.drive_channels.map(channel => channel.id));
  const addedToSharedGroup = extended.groups.some(group => group.channelIds.length > 1 && group.channelIds.some(id => !oldIds.has(id)));
  if (storedGroups || addedToSharedGroup) {
    setup.geometry.driver_groups = driverGroupsToWire(extended.groups);
  }
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
  const branches = new Set([...setup.geometry.drive_channels.map(channel => channel.id), ...(setup.geometry.driver_groups ?? []).map(group => group.id)]);
  if (setup.geometry.combine?.members.some(id => !branches.has(id))) {
    delete setup.geometry.combine;
  }
  // A changed role can split a persisted branch. Reuse the crossover chain's
  // own defaulting when its member set changes, rather than submitting a
  // retained chain that silently omits the new branch.
  if (setup.geometry.combine && storedGroups && JSON.stringify(storedGroups) !== JSON.stringify(extended.groups)) {
    const simulation = seedFileSimulation(record, setup);
    const frequencies = setup.options.frequencies_hz ?? setup.options.frequency_range ?? [200, 20_000];
    const sources = record.sources ?? Object.keys(record.source_tags).map(id => ({ id, role: 'HF' }));
    const combine = combineWire({ ...simulation, input: 'file', sources,
      frequencyStartHz: frequencies[0], frequencyEndHz: frequencies[frequencies.length - 1] });
    if (combine) setup.geometry.combine = combine; else delete setup.geometry.combine;
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
  let controls: ImportedSetup | undefined;
  const setup = effectiveImportedSetup('file', record.ingest_id, () => (controls = fileSetupControls()));
  const model = useImportedModelStore.getState();
  if (!model.setup && model.fileSimulation?.persistDriverGroups && controls) {
    // A role edit may split a group before the next control edit. The edit
    // baseline already sees that split, while the retained setup does not.
    const groups = driverGroupsToWire(fileSimulationSetup.get().driverGroups);
    if (JSON.stringify(groups) !== JSON.stringify(setup.geometry.driver_groups)) {
      setup.geometry.driver_groups = groups;
    }
    const combine = controls.geometry.combine;
    if (JSON.stringify(combine?.members) !== JSON.stringify(setup.geometry.combine?.members)) {
      if (combine) setup.geometry.combine = combine; else delete setup.geometry.combine;
    }
  }
  return setup;
}

export function fileSetupControls(): ImportedSetup {
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
  const crossoverError = crossoverBandBlocker(simulation, selected);
  if (crossoverError) throw new Error(crossoverError);
  const driverError = incompleteDriverBlocker(simulation, label);
  if (driverError) throw new Error(driverError);
  const driver_references = simulationDriverReferences(simulation);
  const model = useImportedModelStore.getState();
  const retained = model.setup ?? model.setupEditBase?.retained;
  if (simulation.persistDriverGroups && (retained?.geometry.rg_ohm ?? 0) !== 0
    && simulation.driverGroups.some(group => group.channelIds.length > 1)) {
    throw new Error('Set generator resistance to 0 ohms or drive the surfaces separately.');
  }
  // Motion edits of older file setups preserve their voltage fields.
  const { drivers, effects } = simulationGeometry(simulation, simulation.sources, simulation.passiveTopology, retained?.geometry);
  return {
    geometry: { skipped_source_ids: [], ...drivers,
      ...(simulation.persistDriverGroups ? { driver_groups: driverGroupsToWire(simulation.driverGroups) } : {}), ...effects },
    ...(Object.keys(driver_references).length ? { driver_references } : {}),
    options,
  };
}

/** Upgrade old membership only when a setup is saved; exact replay stays exact. */
export function fileSetupForSave(setup: ImportedSetup): ImportedSetup {
  if (setup.geometry.driver_groups) return setup;
  const simulation = fileSimulationSetup.get();
  const ids = new Set(setup.geometry.drive_channels.map(channel => channel.id));
  const groups = simulation.driverGroups.map(group => ({ ...group, channelIds: group.channelIds.filter(id => ids.has(id)) }))
    .filter(group => group.channelIds.length);
  if (groups.flatMap(group => group.channelIds).length !== ids.size) return setup;
  return { ...setup, geometry: { ...setup.geometry, driver_groups: driverGroupsToWire(groups) } };
}
registerImportedSetupControls('file', fileSetupControls);

function preparedFileScene(record: RegisteredMesh) {
  return createImportedMeshScene(record.source_file.filename, parseMSH(record.msh_text), 'file', record.ingest_id,
    record.symmetry?.domain_planes ?? [], { originalTagsByTag: Object.fromEntries([
      ...(record.summary.groups ?? []).map(group => [group.solve_tag ?? group.tag, group.tag]),
      ...Object.entries(record.source_tags).map(([id, tag]) => [tag, Number(id.replace('group_', ''))]),
    ]), rolesByTag: {
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
