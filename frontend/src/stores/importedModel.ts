import { create } from 'zustand';
import type { CadReturnIngestRecord } from '../api/cadlink';
import type { CadSolveSetup } from '../api/cadOperations';
import type { JobItem } from '../api/jobsSocket';
import {
  ImportedModelApiError, isModelGone, readImportedModel, readImportedSetup, readRunWorkspace,
  type ImportedModelSummary, type PreparedSummary,
} from '../api/importedModels';
import type { ImportedSetup } from '../jobs/importedSetup';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { subscribeSolveSettingsEdits } from './solveSettingsEdits';
import {
  importedOptionsSnapshot, seedImportedSolveOptions, seedImportedSolveOptionsForFile,
  seedImportedSolveOptionsFromParametric,
  type ImportedOptionsSnapshot,
} from './solveOptions';
import { clearLastModel, migrateLastModel, noteLastModelFailure, readLastModel, rememberLastModel, type LastModel } from './lastModel';
import { workspaceModeStore } from './workspaceMode';

/**
 * The imported model on screen: the one store an imported rail is a view of.
 *
 * `openImported(source)` is the only thing that fills it -- a run reopened
 * through `GET /api/runs/{id}/workspace`, a model by its id, a file just
 * registered, or a CAD return that arrived -- so the model, its prepared mesh
 * and the setup it was opened with always describe one thing. A person's edit
 * ends the opened setup: until then an unedited Solve names the setup revision
 * it was opened with (`restoredRevisionId`), which the server solves exactly.
 */

export type ImportedInput = 'file' | 'cad';
export type ImportedPhase = 'empty' | 'loading' | 'ready' | 'failed';
/** How a file's original becomes its solver mesh; preparing it again makes a new prepared mesh. */
export interface FileDraft { units: string; forward: string; up: string; selected: number[] }
/** The prepared mesh on screen: a file's registered mesh, or a CAD ingestion record. */
export type ImportedPrepared = { ingest_id: string; manifest_sha256: string; artifact_sha256: string; model_id?: string | null };

export interface ImportedModelState {
  input: ImportedInput | null;
  model: ImportedModelSummary | null;
  /** The model a Solve names: the model's id, or `unfiled` for a mesh filed under none. */
  modelId: string | null;
  prepared: ImportedPrepared | null;
  /** The server still holds the model's original: a file can be prepared again from it. */
  originalAvailable: boolean;
  /** The setup the model was opened with, until a person edits it. */
  setup: ImportedSetup | null;
  /** The saved revision `setup` is; an unedited Solve sends it. */
  restoredRevisionId: string | null;
  fileDraft: FileDraft;
  generation: number;
  phase: ImportedPhase;
  error: string | null;
}

export const EMPTY_FILE_DRAFT: Readonly<FileDraft> = Object.freeze({ units: '', forward: '+z', up: '+y', selected: [] });

function emptyState(input: ImportedInput | null): Omit<ImportedModelState, 'generation'> {
  return {
    input, model: null, modelId: null, prepared: null, originalAvailable: false, setup: null,
    restoredRevisionId: null, fileDraft: { ...EMPTY_FILE_DRAFT, selected: [] }, phase: 'empty', error: null,
  };
}

export const useImportedModelStore = create<ImportedModelState>(() => ({
  ...emptyState(workspaceModeStore.getSnapshot().importedInput), generation: 0,
}));

/** What the server answered a model or run read with. */
export interface OpenedModel {
  input: ImportedInput;
  model: ImportedModelSummary | null;
  modelId: string;
  prepared: PreparedSummary;
  originalAvailable: boolean;
  setup: ImportedSetup | null;
  revisionId: string | null;
  /** False when the setup may solve differently from the run: the rail shows it, Solve does not replay it. */
  exact: boolean;
  /** Why it is not exact, for the rail to say. */
  note: string | null;
  /** The run being reopened, for the rail's words. */
  job?: JobItem;
}

/** How a caller shows a reopened model: its status words and its viewport. */
export interface ImportedDisplay {
  /** An automatic caller relinquishes the open when the user moves on. */
  isCurrent?(): boolean;
  enterCadWorkspace?(): void;
  reportStatus?(message: string): void;
  reportViewportNotice?(message: string | null): void;
  showIngestedMesh?(record: CadReturnIngestRecord, name: string, onNotice: ((notice: string) => void) | undefined,
    fetcher: typeof fetch, generation: number): Promise<void>;
}

export type ImportedSource =
  | { kind: 'run'; job: JobItem; display?: ImportedDisplay }
  | { kind: 'model'; modelId: string; preparedId?: string | null; display?: ImportedDisplay }
  /** A file just registered or prepared (null: a new file not yet prepared), with the setup a replay carries. */
  | { kind: 'file'; prepared: (ImportedPrepared & { source_file?: { filename?: string; transform: { units: string; forward: string; up: string }; source_tags: number[] } }) | null;
    setup?: ImportedSetup | null }
  | { kind: 'cad'; record: CadReturnIngestRecord };

/** One open of a rail: it owns the rail from `begin` until it fills or fails. */
export interface ImportedViewOpen {
  /** The rail still belongs to this open (no newer selection of its own). */
  current(): boolean;
  /** Fill the rail and the viewport; the prepared record it shows, or null when it could not. */
  fill(opened: OpenedModel): Promise<ImportedPrepared | null>;
  fail(message: string): void;
}
export interface ImportedView {
  /** An unsolved model can still have an editable durable draft. */
  hasDraft?(modelId: string): boolean;
  begin(context: { generation: number; source: ImportedSource; display: ImportedDisplay; fetcher: typeof fetch }): ImportedViewOpen;
}

const views: Partial<Record<ImportedInput, ImportedView>> = {};

/** A rail registers itself as the view of its input (stores/meshImport.ts, shell/cadlink/restores.ts). */
export function registerImportedView(input: ImportedInput, view: ImportedView): void {
  views[input] = view;
}

function fileDraftOf(prepared: Extract<ImportedSource, { kind: 'file' }>['prepared'] | PreparedSummary): FileDraft {
  const file = (prepared as { source_file?: { transform?: Partial<FileDraft>; source_tags?: number[] } } | null)?.source_file;
  return {
    units: file?.transform?.units ?? '', forward: file?.transform?.forward ?? '+z', up: file?.transform?.up ?? '+y',
    selected: [...(file?.source_tags ?? [])],
  };
}

function inputOf(producer: string | null | undefined): ImportedInput {
  return producer === 'mesh-file' ? 'file' : 'cad';
}

/** A synchronous source: the store describes it at once. */
function openKnown(source: Extract<ImportedSource, { kind: 'file' | 'cad' }>): void {
  const input: ImportedInput = source.kind === 'file' ? 'file' : 'cad';
  const prepared = source.kind === 'file' ? source.prepared : source.record;
  const modelId = prepared ? (prepared.model_id || 'unfiled') : null;
  const filename = source.kind === 'file' ? source.prepared?.source_file?.filename : source.record.project?.document_name ?? undefined;
  const held = workspaceModeStore.getSnapshot().importedInput === input ? useImportedModelStore.getState() : parked[input]?.state;
  // The same CAD snapshot arriving again (a refresh of the record on screen)
  // is the model already open: an unedited opened setup stays.
  const same = source.kind === 'cad' && held?.input === 'cad' && held.phase === 'ready'
    && held.prepared?.ingest_id === source.record.ingest_id;
  const next: Omit<ImportedModelState, 'generation'> = {
    ...emptyState(input),
    model: modelId && modelId !== 'unfiled'
      ? { model_id: modelId, producer: source.kind === 'file' ? 'mesh-file' : 'cad', display_name: filename ?? '' } : null,
    modelId,
    prepared,
    originalAvailable: source.kind === 'file' && prepared !== null,
    setup: source.kind === 'file' ? source.setup ?? null : same ? held!.setup : null,
    restoredRevisionId: same ? held!.restoredRevisionId : null,
    ...(same ? { model: held!.model, modelId: held!.modelId, originalAvailable: held!.originalAvailable } : {}),
    fileDraft: source.kind === 'file' && source.prepared ? fileDraftOf(source.prepared)
      : source.kind === 'file' ? { ...EMPTY_FILE_DRAFT, selected: [] } : useImportedModelStore.getState().fileDraft,
    phase: prepared ? 'ready' : 'empty',
  };
  if (workspaceModeStore.getSnapshot().importedInput !== input) {
    // A return arriving while a file is on screen is the CAD input's model,
    // kept for when CAD comes back on screen.
    parked[input] = { state: next, options: parked[input]?.options ?? null };
    return;
  }
  useImportedModelStore.setState(next);
}

async function readOpened(source: Extract<ImportedSource, { kind: 'run' | 'model' }>, input: ImportedInput,
  fetcher: typeof fetch): Promise<OpenedModel> {
  if (source.kind === 'run') {
    const workspace = await readRunWorkspace(source.job.id, fetcher);
    if (!workspace.prepared) throw new Error(workspace.refusal?.message ?? 'This run has no retained mesh. Import the geometry again.');
    if (inputOf(workspace.prepared.producer) !== input) throw new Error('This run’s retained mesh is of another kind. Select the run again.');
    return {
      input, model: workspace.model ?? null, modelId: workspace.prepared.model_id || 'unfiled',
      prepared: workspace.prepared, originalAvailable: workspace.original_available,
      // The revision's whole content: its preparation and driver references too.
      setup: workspace.setup ? structuredClone(workspace.setup.content) as ImportedSetup : null,
      revisionId: workspace.setup?.revision_id ?? null, job: source.job,
      exact: workspace.setup?.exact !== false, note: workspace.setup?.note ?? null,
    };
  }
  const read = await readImportedModel(source.modelId, source.preparedId ?? null, fetcher);
  const preparedId = source.preparedId ?? read.latest_prepared?.ingest_id ?? null;
  const prepared = read.prepared ?? (preparedId && !source.preparedId && read.model.producer === 'mesh-file'
    ? (await readImportedModel(source.modelId, preparedId, fetcher)).prepared : read.latest_prepared);
  if (!prepared) throw new Error('This model has no retained preparation. Import it again.');
  const saved = await readImportedSetup(source.modelId, prepared.ingest_id, fetcher);
  return {
    input, model: read.model, modelId: read.model.model_id, prepared, originalAvailable: read.original_available,
    setup: saved.setup ?? null, revisionId: saved.setup ? saved.revision_id ?? null : null, exact: true, note: null,
  };
}

/** Wait words while a reopen is under way; the reason while it failed. */
const LOADING_BLOCKER = 'Wait for this run’s retained setup to finish loading.';

/**
 * Open an imported model: the only way the store is filled.
 *
 * A run or a model is read from the server, then shown by its input's rail
 * (the view) and the viewport. Resolves true when it is on screen, false when
 * a newer choice took over; rejects with the reason it cannot be opened.
 */
export async function openImported(source: ImportedSource, fetcher: typeof fetch = fetch): Promise<boolean> {
  if (source.kind === 'file' || source.kind === 'cad') { openKnown(source); return true; }
  // An unnumbered CAD preparation is not an archived run.
  if (source.kind === 'run' && (source.job.config_summary.geometry_type !== 'imported' || source.job.run_number === null)) return false;
  const input: ImportedInput = source.kind === 'run'
    ? (source.job.config_summary.import_origin === 'mesh-file' ? 'file' : 'cad')
    : (source.modelId.startsWith('mesh-file:') ? 'file' : 'cad');
  const view = views[input];
  if (!view) throw new Error('This model cannot be shown here.');
  const display = source.display ?? {};
  const generation = importedMeshStore.beginIntent();
  // Choosing the input first parks the other input's model and options.
  workspaceModeStore.setImportedInput(input);
  useImportedModelStore.setState({ ...emptyState(input), generation, phase: 'loading' });
  const rail = view.begin({ generation, source, display, fetcher });
  const current = () => {
    const state = useImportedModelStore.getState();
    return state.generation === generation && state.phase === 'loading' && state.input === input
      && importedMeshStore.isCurrentGeneration(generation) && rail.current() && display.isCurrent?.() !== false;
  };
  // Not current any more: a newer choice took over, or an edit failed this
  // open (the rail then shows why, and stops waiting).
  const stopped = () => {
    const state = useImportedModelStore.getState();
    if (state.generation === generation && state.phase === 'failed' && rail.current()) rail.fail(state.error ?? '');
    return false;
  };
  try {
    const opened = await readOpened(source, input, fetcher);
    if (!current()) return stopped();
    const prepared = await rail.fill(opened);
    if (!current()) return stopped();
    if (!prepared) throw new Error('This run’s retained mesh artifacts are no longer available. Import the geometry again.');
    useImportedModelStore.setState({
      input, model: opened.model, modelId: opened.modelId, prepared, originalAvailable: opened.originalAvailable,
      // A setup that may not be the run's exactly fills the rail, but is not replayed.
      setup: opened.setup && opened.exact ? structuredClone(opened.setup) : null,
      restoredRevisionId: opened.setup && opened.exact ? opened.revisionId : null,
      fileDraft: input === 'file' ? fileDraftOf(opened.prepared) : useImportedModelStore.getState().fileDraft,
      phase: 'ready', error: null,
    });
    return true;
  } catch (error) {
    if (!current()) return stopped();
    const message = error instanceof Error ? error.message : String(error);
    useImportedModelStore.setState({ phase: 'failed', error: message });
    rail.fail(message);
    throw error;
  }
}

// The model on screen is the one Imported mode reopens next start. A mesh filed
// under none has no id to reopen, and a model still loading may yet fail.
useImportedModelStore.subscribe((state) => {
  if (state.phase === 'ready' && state.input && state.modelId && state.modelId !== 'unfiled') {
    rememberLastModel({ model_id: state.modelId, input: state.input });
  }
});

export interface ReopenOptions {
  /** Asked once the pointer is known; false leaves it alone (a guard holds, or it was already tried). */
  shouldTry?(pointer: LastModel): boolean;
  /** Shows a CAD model that was never solved, so has no setup to open it by. Say why when it cannot be shown. */
  openUnsolved?(pointer: LastModel, prepared: PreparedSummary): Promise<boolean>;
}

/** A request that got no response at all (offline, refused, aborted): the pointer says nothing about the model. */
class NetworkFailure extends Error {
  constructor(readonly original: unknown) {
    super(original instanceof Error ? original.message : String(original));
    this.name = 'NetworkFailure';
  }
}

/** The server answered, and its answer will not change by trying again: the model is gone or its request is unusable. */
const isDefinitiveRefusal = (error: unknown): boolean =>
  isModelGone(error) || (error instanceof ImportedModelApiError && (error.status === 409 || error.status === 422));

/**
 * Reopen the last model of the input on screen, through `openImported`.
 *
 * It never changes the mode (Parametric opens nothing) nor the input the
 * person last chose: each input has its own pointer. It only fills an Imported
 * view nobody has opened into yet. A model the server no longer has (or keeps
 * no preparation of, or refuses with a 409/422) clears the pointer once and
 * leaves the empty view. Any other failure is thrown for the caller to show and
 * keeps the pointer, which may recover; after two starts in a row that fail it
 * is cleared, so a broken model cannot fail at every launch. A request that got
 * no response at all (offline) neither clears nor counts.
 */
export async function reopenLastModel(fetcher: typeof fetch = fetch, options: ReopenOptions = {}): Promise<boolean> {
  if (workspaceModeStore.getSnapshot().mode !== 'cad') return false;
  const generation = importedMeshStore.captureGeneration();
  const stillEmpty = () => workspaceModeStore.getSnapshot().mode === 'cad'
    && useImportedModelStore.getState().phase === 'empty' && importedMeshStore.isCurrentGeneration(generation);
  await migrateLastModel(fetcher);
  const pointer = readLastModel(workspaceModeStore.getSnapshot().importedInput);
  if (!pointer || !stillEmpty() || options.shouldTry?.(pointer) === false) return false;
  const reachable: typeof fetch = async (...args) => {
    try { return await fetcher(...args); } catch (error) { throw new NetworkFailure(error); }
  };
  try {
    const read = await readImportedModel(pointer.model_id, null, reachable);
    if (!read.latest_prepared) { clearLastModel(pointer); return false; }
    // The CAD view shows a model by its stored setup; one never solved has none.
    if (pointer.input === 'cad' && !(await readImportedSetup(pointer.model_id, read.latest_prepared.ingest_id, reachable)).setup
      && !views.cad?.hasDraft?.(pointer.model_id)) {
      if (!stillEmpty() || !options.openUnsolved) return false;
      const shown = await options.openUnsolved(pointer, read.latest_prepared);
      if (shown) rememberLastModel(pointer);
      return shown;
    }
    if (!stillEmpty()) return false;
    return await openImported({ kind: 'model', modelId: pointer.model_id }, reachable);
  } catch (error) {
    if (isDefinitiveRefusal(error)) { clearLastModel(pointer); return false; }
    if (error instanceof NetworkFailure) throw error.original;
    noteLastModelFailure(pointer);
    throw error;
  }
}

/** This open (by its generation) still owns the store and the viewport: no newer open of either input. */
export function isCurrentOpen(generation: number): boolean {
  return useImportedModelStore.getState().generation === generation && importedMeshStore.isCurrentGeneration(generation);
}

/** Change how a file is prepared: its prepared mesh no longer matches, and the opened setup ends. */
export function editFileDraft(patch: Partial<FileDraft>): void {
  useImportedModelStore.setState((state) => ({
    fileDraft: { ...state.fileDraft, ...patch, selected: [...(patch.selected ?? state.fileDraft.selected)] },
    prepared: null, setup: null, restoredRevisionId: null,
  }));
}

/** Why Solve must wait for this input's model: it is loading, or failed to open. */
export function importedModelBlocker(input: ImportedInput): string | null {
  const state = useImportedModelStore.getState();
  if (state.input !== input || !importedMeshStore.isCurrentGeneration(state.generation)) return null;
  return state.phase === 'loading' ? LOADING_BLOCKER : state.phase === 'failed' ? state.error : null;
}

/**
 * What an unedited Solve of this prepared mesh sends: the revision it was
 * opened with, or the opened setup itself (a replay that names no revision).
 * Null once a person edited it, or for another mesh or input.
 */
export function unchangedSetup(input: ImportedInput, preparedId: string | undefined):
  { setup_revision_id: string } | { setup: CadSolveSetup } | null {
  const state = useImportedModelStore.getState();
  if (state.input !== input || state.phase !== 'ready' || !preparedId || state.prepared?.ingest_id !== preparedId
    || !state.setup) return null;
  return state.restoredRevisionId
    ? { setup_revision_id: state.restoredRevisionId }
    : { setup: { schema_version: 1, ...structuredClone(state.setup) } as unknown as CadSolveSetup };
}

// A person's edit of an imported setting ends the opened setup. Parametric
// edits are not this model's; an edit while it loads makes the open fail, so
// the edit is not silently replaced by what arrives.
/** Which input's setup an edit changed: the file rail's, the CAD rail's, or the options on screen. */
function editedInput(source: string | undefined): ImportedInput {
  if (source === 'meshImport') return 'file';
  if (source === 'importedSolveOptions') return workspaceModeStore.getSnapshot().importedInput;
  // cadReturn and cadPreparation announce their setters without a source.
  return 'cad';
}

subscribeSolveSettingsEdits((edit) => {
  if (edit?.source === 'solveOptions') return;
  const input = editedInput(edit?.source);
  if (input !== workspaceModeStore.getSnapshot().importedInput) {
    // An edit of the input that is not on screen ends its parked setup.
    const held = parked[input]?.state;
    if (held && (held.setup || held.restoredRevisionId)) parked[input] = { ...parked[input]!, state: { ...held, setup: null, restoredRevisionId: null } };
    return;
  }
  const state = useImportedModelStore.getState();
  if (state.phase === 'loading') {
    useImportedModelStore.setState({ phase: 'failed',
      error: 'Settings changed while this run was loading. Select the run again before solving.' });
    return;
  }
  if (state.setup || state.restoredRevisionId) useImportedModelStore.setState({ setup: null, restoredRevisionId: null });
});

/*
 * File and CAD input share one imported options instance and this one store,
 * so each input keeps its open model -- the store's state and its options --
 * while the other is on screen. Switching input parks the leaving model and
 * reopens the arriving one, seeding fresh options when it has none.
 */
type ParkedState = Omit<ImportedModelState, 'generation'>;
const parked: Partial<Record<ImportedInput, { state: ParkedState | null; options: ImportedOptionsSnapshot | null }>> = {};
let activeInput: ImportedInput = workspaceModeStore.getSnapshot().importedInput;

function parkedState(): ParkedState {
  const { generation: _generation, ...state } = useImportedModelStore.getState();
  return structuredClone(state);
}

workspaceModeStore.subscribe(() => {
  const next = workspaceModeStore.getSnapshot().importedInput;
  if (next === activeInput) return;
  parked[activeInput] = { state: parkedState(), options: importedOptionsSnapshot() };
  activeInput = next;
  const saved = parked[next];
  if (saved?.options) seedImportedSolveOptions(saved.options);
  else if (next === 'file') seedImportedSolveOptionsForFile();
  else seedImportedSolveOptionsFromParametric();
  const state = saved?.state;
  useImportedModelStore.setState(state && state.phase !== 'loading'
    ? { ...state, phase: state.phase === 'failed' ? 'empty' : state.phase, error: null }
    : emptyState(next));
});

/** All CAD options, including while a file occupies the shared rail. */
export function cadOptionsSnapshot(): ImportedOptionsSnapshot | null {
  return workspaceModeStore.getSnapshot().importedInput === 'cad'
    ? importedOptionsSnapshot() : parked.cad?.options ?? null;
}

export function applyCadOptions(options: ImportedOptionsSnapshot): void {
  if (workspaceModeStore.getSnapshot().importedInput === 'cad') seedImportedSolveOptions(options);
  else parked.cad = { state: parked.cad?.state ?? null, options: structuredClone(options) };
}

/**
 * Another CAD selection was made while a file is on screen: CAD's parked model
 * and options belong to the previous one, so they go. Switching back then
 * seeds a fresh copy, the new project's setup applies on top, and an
 * arrival of the new selection fills the CAD model again.
 */
export function discardParkedCadModel(): void {
  if (workspaceModeStore.getSnapshot().importedInput !== 'cad') delete parked.cad;
}

/** The imported options were reset: neither input keeps parked options. */
export function resetParkedImportedOptions(): void {
  for (const input of ['cad', 'file'] as const) if (parked[input]) parked[input] = { ...parked[input]!, options: null };
  activeInput = workspaceModeStore.getSnapshot().importedInput;
}

/** Something other than an edit replaced this input's options (a new selection's setup): the opened setup ends. */
export function endOpenedSetup(input: ImportedInput): void {
  if (workspaceModeStore.getSnapshot().importedInput === input) {
    const state = useImportedModelStore.getState();
    if (state.setup || state.restoredRevisionId) useImportedModelStore.setState({ setup: null, restoredRevisionId: null });
    return;
  }
  const held = parked[input]?.state;
  if (held) parked[input] = { ...parked[input]!, state: { ...held, setup: null, restoredRevisionId: null } };
}

/** Tests and a full reset: nothing open, nothing parked. */
export function resetImportedModelStore(): void {
  delete parked.cad;
  delete parked.file;
  activeInput = workspaceModeStore.getSnapshot().importedInput;
  useImportedModelStore.setState({ ...emptyState(activeInput), generation: 0 });
}
