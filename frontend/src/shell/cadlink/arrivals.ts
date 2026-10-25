import { cadModelLabel } from '../../model/cadLabels';
import { waitForCadProjectSettings } from '../../stores/cadProjectSettings';
import { currentDocumentLoad, isCurrentDocumentLoad } from '../../stores/design';
import { returnOnshapeToWg } from '../../api/onshape';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { workspaceNavigation } from '../workspaceNavigation';
import { showIngestedMeshInViewport } from './artifactLoader';
import { cadCoordinationStore } from '../../api/cadCoordination';
import {
  CadLinkApiError,
  ingestReturn,
  type CadReturnIngestRecord,
  listReturns,
  type CadReturnBundle,
} from '../../api/cadlink';
import { preferencesStore } from '../../prefs/preferences';
import { useDocumentStore } from '../../stores/document';
import { cadPreparationKey, preparesAutomatically, useCadReturnStore } from '../../stores/cadReturn';
import { readLastModel } from '../../stores/lastModel';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { resolvePreparationTarget } from './preparationTarget';
import { meshCeilingChoice } from '../../stores/meshCeilingChoice';
import type { CadIngestFailure } from '../../stores/cadWorkspace';
import { reportCadAutomationError } from './caughtErrors';

export interface RefreshOptions {
  background?: boolean;
  autoOpenNew?: boolean;
  /** List for manual use, but keep new revisions eligible until Send owners are known. */
  deferNewArrivals?: boolean;
}

/** A step abandoned because newer user intent replaced what it was working on.
 * Its feedback is already on screen, so a composed action stops silently. */
export class SupersededError extends Error {}

/** Whether a return names a design other than the one that is open.
 *
 * A return that names a design belongs to that design even when the open
 * document has no CAD identity. Returns naming no design remain manually
 * adoptable CAD-authored models. */
export function returnBelongsToAnotherProject(
  bundle: CadReturnBundle,
  designId: string | null | undefined,
): boolean {
  const returned = bundle.designIds ?? [];
  if (returned.length === 0) return false;
  return !designId || !returned.includes(designId);
}

/** Whether a return is positively linked to the open registry project.
 * Unlinked returns remain available for manual adoption, but are never
 * guessed into a project merely because they name no other project. */
export function returnBelongsToProject(
  bundle: CadReturnBundle,
  designId: string | null | undefined,
): boolean {
  return Boolean(designId && (bundle.designIds ?? []).includes(designId));
}

// The directory timestamp can stay unchanged when an export replaces files
// inside it. The immutable manifest id distinguishes those return revisions.
function returnRevision(item: CadReturnBundle): string {
  return item.returnId ? `${item.modifiedAt}:${item.returnId}` : item.modifiedAt;
}

export function newestReturnArrival(
  items: CadReturnBundle[],
  previous: Map<string, string> | null,
  nowMs = Date.now(),
): CadReturnBundle | null {
  const recentThreshold = nowMs - 60_000;
  return items.find((item) => item.readable && (
    previous
      ? previous.get(item.bundlePath) !== returnRevision(item)
      : Date.parse(item.modifiedAt) >= recentThreshold
  )) ?? null;
}

type Ref<T> = { current: T };
export interface CadReturnArrivalReaderIntegration {
  autoIngestSelected(): void;
  cadFolderConfigured: Ref<boolean | null>;
  enterCadWorkspace(): void;
  manualSelectionAt: Ref<number | null>;
  noteCadActivity(): void;
  projectOpenPending: Ref<boolean>;
  refreshChannelDriverBases(): Promise<unknown>;
  refusedForeignReturn: Ref<boolean>;
  returnListRequest: Ref<number>;
  seenReturnRevisions: Ref<Map<string, string> | null>;
  /** Initial discovery can be deferred across listings while a receipt owns display. */
  initialChoiceOwed: Ref<boolean>;
  deferredArrivalRevisions: Ref<Map<string, string>>;
  /** The explicit waiter owns selection, including after its poll settles. */
  awaitingReturn(): boolean;
  /** Receipt display chooses the Send before discovery may choose an initial model. */
  awaitingReceiptDisplay(): boolean;
  /** Only an active restore may defer discovery; a stale durable pointer cannot. */
  awaitingLastModel(): boolean;
  /** Recheck after the listing await: a push may have invalidated the feed meanwhile. */
  sendOwnersKnown(): boolean;
  ownsReturn(bundle: CadReturnBundle): boolean;
  /** Fusion Solve owns initial display too; a completed Send may be restored. */
  ownsSolveReturn?(bundle: CadReturnBundle): boolean;
  setError(value: string | null): void;
  setStatus(value: string | null): void;
  setBundles(items: CadReturnBundle[]): void;
  setLoading(value: boolean): void;
}

/** A single listing read, with fencing even for fetch implementations that ignore abort. */
export async function refreshCadReturnArrivals(
  integration: CadReturnArrivalReaderIntegration,
  options: RefreshOptions = {},
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const {
    autoIngestSelected, cadFolderConfigured, enterCadWorkspace, manualSelectionAt, noteCadActivity,
    projectOpenPending, refreshChannelDriverBases, refusedForeignReturn, returnListRequest,
    seenReturnRevisions, initialChoiceOwed, deferredArrivalRevisions, awaitingReturn, awaitingReceiptDisplay, awaitingLastModel, sendOwnersKnown, ownsReturn, ownsSolveReturn,
    setError, setStatus, setBundles, setLoading,
  } = integration;
  const background = options.background === true;
  const request = ++returnListRequest.current;
  const selection = useCadReturnStore.getState().captureSelectionIntent();
  const documentLoad = currentDocumentLoad();
  if (preferencesStore.getSnapshot().cadApplication === 'onshape') {
    if (projectOpenPending.current) {
      projectOpenPending.current = false;
      setStatus('Project design loaded. Bring its geometry from Onshape to prepare it for simulation.');
    }
    setLoading(false);
    return;
  }
  if (!background) { setLoading(true); setError(null); }
  try {
    const response = await listReturns(fetcher);
    if (preferencesStore.getSnapshot().cadApplication !== 'fusion360'
      || request !== returnListRequest.current
      || !useCadReturnStore.getState().isCurrentSelectionIntent(selection)
      || !isCurrentDocumentLoad(documentLoad)) return;
    setBundles(response.items);
    const previous = seenReturnRevisions.current;
    const next = new Map(response.items.map((item) => [item.bundlePath, returnRevision(item)]));
    // The durable choice owns startup. This first listing is a baseline, not
    // an arrival that may overtake its asynchronous retained-model read.
    const remembered = !projectOpenPending.current && readLastModel('cad');
    const restoring = !projectOpenPending.current && awaitingLastModel();
    const discoveryPrevious = previous ?? (remembered ? next : null);
    const wasConfigured = cadFolderConfigured.current;
    cadFolderConfigured.current = response.cadFolderConfigured;
    cadCoordinationStore.set(response.coordination === 'on' ? 'on' : 'off');
    const listingChanged = previous === null
      || previous.size !== next.size
      || [...next].some(([path, modifiedAt]) => previous.get(path) !== modifiedAt);
    if (listingChanged || response.cadFolderConfigured !== wasConfigured) noteCadActivity();
    let arrivalPrevious = discoveryPrevious;
    const deferNewArrivals = options.deferNewArrivals || !sendOwnersKnown();
    if (deferNewArrivals) {
      deferredArrivalRevisions.current.forEach((_, path) => {
        if (!next.has(path)) deferredArrivalRevisions.current.delete(path);
      });
      response.items.forEach((item) => {
        if (newestReturnArrival([item], discoveryPrevious)) deferredArrivalRevisions.current.set(item.bundlePath, returnRevision(item));
      });
    } else if (deferredArrivalRevisions.current.size) {
      const deferredPrevious = new Map(discoveryPrevious);
      deferredArrivalRevisions.current.forEach((revision, path) => {
        if (next.get(path) === revision) deferredPrevious.delete(path);
      });
      arrivalPrevious = deferredPrevious;
      deferredArrivalRevisions.current.clear();
    }
    // An owed initial choice must let queued Sends finish first, even when a
    // background poll also notices a new revision in the folder.
    const arrived = options.autoOpenNew && !restoring && !deferNewArrivals && !awaitingReturn()
      && !(initialChoiceOwed.current && awaitingReceiptDisplay())
      ? newestReturnArrival(response.items.filter((item) => !ownsReturn(item)
        // Discovering the return already in the viewport is not a new model
        // choice. A re-export has a new return id, even at the same path.
        && !(item.returnId && item.returnId === useCadReturnStore.getState().ingestRecord?.return_id)), arrivalPrevious) : null;
    seenReturnRevisions.current = next;
    const currentDesignId = useDocumentStore.getState().identity?.designId;
    if (useCadReturnStore.getState().selectedBundle) initialChoiceOwed.current = false;
    const canChooseInitial = initialChoiceOwed.current && !restoring && !awaitingReturn() && !awaitingReceiptDisplay()
      && !useCadReturnStore.getState().selectedBundle;
    const initial = canChooseInitial
      ? response.items.find((item) => (
          item.readable && !ownsSolveReturn?.(item) && (
            currentDesignId
              ? returnBelongsToProject(item, currentDesignId)
              : (item.designIds ?? []).length === 0
          )
        )) ?? null
      : null;
    // Background CAD discovery cannot take a file import's viewport intent.
    if (workspaceModeStore.getSnapshot().importedInput === 'file') return;
    const opened = arrived ?? initial;
    const projectMismatch = Boolean(
      opened && returnBelongsToAnotherProject(opened, currentDesignId),
    );
    const destination = projectOpenPending.current
      ? useDocumentStore.getState().identity?.lineageId ?? null
      : undefined;
    const selected = useCadReturnStore.getState().selectedBundle;
    const arrivalRequestedAt = arrived ? Date.parse(arrived.modifiedAt) : null;
    const heldForManualPick = Boolean(
      arrived && !projectMismatch && selected && selected.bundlePath !== arrived.bundlePath
      && manualSelectionAt.current !== null
      && arrivalRequestedAt !== null && Number.isFinite(arrivalRequestedAt)
      && arrivalRequestedAt <= manualSelectionAt.current,
    );
    let continuity: 'initial' | 'carried' | 'reset' = 'initial';
    if (opened && !projectMismatch && !heldForManualPick) {
      continuity = arrived
        ? useCadReturnStore.getState().selectArrivedBundle(arrived, destination, fetcher)
        : (useCadReturnStore.getState().selectBundle(opened, destination, true, fetcher), 'initial');
      void waitForCadProjectSettings().then(ok => ok ? refreshChannelDriverBases() : undefined).catch(() => undefined);
      importedMeshStore.beginIntent();
      initialChoiceOwed.current = false;
    }
    // Opening a project is the user's request for its model: its latest return
    // is prepared, as a selection is. The mounted owner also prepares launch restores.
    const openedProject = projectOpenPending.current && canChooseInitial && !!initial
      && !projectMismatch && opened === initial;
    if (projectOpenPending.current && canChooseInitial) {
      projectOpenPending.current = false;
      setStatus(initial
        ? `Project design loaded. Selected the latest matching model from ${cadModelLabel(initial)}; preparing Simulation geometry.`
        : 'Project design loaded. No matching CAD model is available yet; send the project from Fusion or Onshape to prepare Simulation geometry.');
    } else if (projectOpenPending.current && useCadReturnStore.getState().selectedBundle) {
      // A receipt or another selection owner resolved the project choice.
      projectOpenPending.current = false;
    }
    if (arrived) {
      if (projectMismatch) {
        refusedForeignReturn.current = true;
        const reason = `Received ${cadModelLabel(arrived)}, but it belongs to another CAD-linked project. Open that project from File → CAD-linked designs.`;
        setError(reason);
        enterCadWorkspace();
        return;
      }
      const arrivedName = cadModelLabel(arrived);
      const keptName = selected ? cadModelLabel(selected) : null;
      setStatus(heldForManualPick
        ? `Received ${arrivedName} from Fusion 360. You selected ${keptName} after it was sent, so ${keptName} stays selected; select ${arrivedName} from the model list to use it.`
        : `Received ${arrivedName} from Fusion 360.${
          continuity === 'carried' ? ' Kept your mesh, channel, and solve settings.' : ''
        }`);
      enterCadWorkspace();
      if (!heldForManualPick) autoIngestSelected();
    } else if (initial) {
      if (openedProject) autoIngestSelected();
    } else {
      // Reconcile the restored choice. The mounted preparation owner observes
      // it and reuses the server's cached preparation for unchanged inputs.
      const currentSelected = useCadReturnStore.getState().selectedBundle;
      if (!currentSelected) return;
      // The request pointer and its eventual retained mesh belong to Solve,
      // not to a folder poll that happens to find the same exported bundle.
      if (ownsSolveReturn?.(currentSelected)) return;
      if (currentSelected.bundleOrigin === 'native') return;
      if (!response.cadFolderConfigured) return;
      const current = response.items.find((bundle) => bundle.bundlePath === currentSelected.bundlePath);
      useCadReturnStore.getState().refreshSelectedBundle(current ?? null);
    }
  } catch (reason) {
    reportCadAutomationError('return-list', reason);
    if (request === returnListRequest.current
      && useCadReturnStore.getState().isCurrentSelectionIntent(selection)
      && isCurrentDocumentLoad(documentLoad) && !background) {
      if (projectOpenPending.current) {
        projectOpenPending.current = false;
        setStatus('Project design loaded, but its CAD models could not be read. Refresh CAD Link to try again.');
      }
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  } finally {
    if (request === returnListRequest.current) setLoading(false);
  }
}

export function enterCadWorkspace(): void {
  workspaceModeStore.setMode('cad');
  workspaceNavigation.activate('geometry');
}

export interface CadPreparationOptions {
  meshLimitOverride?: boolean;
  /** Receipt display keeps its own ordering and verifies its accepted hash. */
  isCurrent?(): boolean;
  validate?(record: CadReturnIngestRecord): void;
  onStarted?(intent: number, viewport: number): void;
  onPrepared?(): void;
  fetcher?: typeof fetch;
}

export interface CadArrivalActionsIntegration {
  ingestRequest: Ref<number>;
  ingestAbortController: Ref<AbortController | null>;
  mounted: Ref<boolean>;
  manualSelectionAt: Ref<number | null>;
  refusedForeignReturn: Ref<boolean>;
  setIngesting(value: boolean): void;
  /** `failure`: how the server classified it, when it did. */
  setIngestError(value: string | null, failure?: CadIngestFailure | null): void;
  setError(value: string | null): void;
  setStatus(value: string | null): void;
  setViewportNotice(value: string | null): void;
  setBundles(value: CadReturnBundle[]): void;
  reportViewportNotice(value: string | null): void;
  enterCadWorkspace(): void;
  autoIngestSelected(): void;
  rereadDrivers(): void;
  /** A receipt, retained-model open or Fusion job owns preparation/display. */
  deferAutomaticPreparation(): boolean;
  onshapeInstanceId(): string | null;
  showIngestedMesh: typeof showIngestedMeshInViewport;
}

/** Selection actions are independent of React. Supply feedback and lifecycle adapters
 * for a mounted panel; omitted adapters permit background and archived-model use. */
export function createCadArrivalActions(
  integration: Partial<CadArrivalActionsIntegration> = {},
  fetcher: typeof fetch = fetch,
) {
  const noop = () => undefined;
  const defaults: CadArrivalActionsIntegration = {
    ingestRequest: { current: 0 }, ingestAbortController: { current: null }, mounted: { current: true },
    manualSelectionAt: { current: null },
    refusedForeignReturn: { current: false },
    setIngesting: noop, setIngestError: noop, setError: noop, setStatus: noop,
    setViewportNotice: noop, setBundles: noop, reportViewportNotice: noop,
    enterCadWorkspace, autoIngestSelected: noop, rereadDrivers: noop,
    deferAutomaticPreparation: () => false, onshapeInstanceId: () => null,
    showIngestedMesh: showIngestedMeshInViewport,
  };
  const adapter = { ...defaults, ...integration };
  const { ingestRequest, ingestAbortController, mounted, manualSelectionAt,
    refusedForeignReturn, setIngesting, setIngestError, setError, setStatus,
    setViewportNotice, setBundles, reportViewportNotice, enterCadWorkspace: activateWorkspace,
    autoIngestSelected, rereadDrivers,
    onshapeInstanceId, showIngestedMesh } = adapter;
  const performIngest = async (options: CadPreparationOptions = {}): Promise<CadReturnIngestRecord> => {
    const selection = useCadReturnStore.getState().captureSelectionIntent();
    const documentLoad = currentDocumentLoad();
    const settingsReady = !useCadReturnStore.getState().projectLineageId || await waitForCadProjectSettings();
    if (!mounted.current || options.isCurrent?.() === false
      || !useCadReturnStore.getState().isCurrentSelectionIntent(selection) || !isCurrentDocumentLoad(documentLoad)) {
      throw new SupersededError('A newer model selection superseded preparation.');
    }
    if (!settingsReady) {
      const message = 'Waveguide Generator could not load this model’s settings. Try again.';
      if (mounted.current) setIngestError(message);
      throw new Error(message);
    }
    const current = useCadReturnStore.getState();
    if (!current.selectedBundle) throw new Error('Select a CAD model before preparing a simulation.');
    if (current.selectedBundle.bundleOrigin === 'native') throw new Error('Edit the native source assembly and prepare it again.');
    // This intent covers the ingest record itself. The viewport has a separate
    // token because its follow-up artifact fetch can be superseded independently.
    const attempt = preparationKey();
    useCadReturnStore.setState({ attemptedPreparationKey: attempt });
    const ingestGeneration = current.beginIngestIntent();
    const request = ++ingestRequest.current;
    ingestAbortController.current?.abort();
    const abortController = new AbortController();
    ingestAbortController.current = abortController;
    // Intent starts before the network request. A later viewport choice must
    // win even when this ingest's mesh fetch eventually completes.
    const viewportGeneration = importedMeshStore.beginIntent();
    options.onStarted?.(ingestGeneration, viewportGeneration);
    setIngesting(true); setIngestError(null); setError(null); setStatus(null); setViewportNotice(null);
    meshCeilingChoice.clear();
    try {
      const skipped = new Set(current.skippedSourceIds);
      const record = await ingestReturn({
        bundlePath: current.selectedBundle.bundlePath,
        // A workspace listing item has no origin and is a WGLink return; an
        // Onshape return carries its own. The server resolves each against
        // its own root, so this is the only thing that has to be dispatched.
        bundleOrigin: current.selectedBundle.bundleOrigin ?? 'wglink',
        mesh: {
          rigidSizeMm: current.meshSizes.rigid_size_mm,
          transitionMm: current.meshSizes.transition_mm,
          sourceSizeMm: Object.fromEntries(Object.entries(current.meshSizes.source_size_mm).filter(([id]) => !skipped.has(id))),
        },
        skippedSourceIds: current.skippedSourceIds,
        areaDriftOverrides: current.areaDriftOverrides,
        expectedDesignId: useDocumentStore.getState().identity?.designId ?? null,
        expectedInstanceId: resolvePreparationTarget(current.selectedBundle),
        symmetryMode: useCadReturnStore.getState().symmetryMode,
        // The user chose Solve anyway over the triangle ceiling for this model.
        ...(options.meshLimitOverride ? { meshLimitOverride: true } : {}),
      }, options.fetcher ?? fetcher, abortController.signal);
      if (options.isCurrent?.() === false) throw new SupersededError('A newer model selection superseded preparation.');
      options.validate?.(record);
      if (!isCurrentDocumentLoad(documentLoad)
        || !useCadReturnStore.getState().applyIngest(record, ingestGeneration)) {
        const superseded = 'The model or design changed while its mesh was being prepared. Rebuild the mesh for the current model.';
        if (request === ingestRequest.current && mounted.current) setStatus(superseded);
        throw new SupersededError(superseded);
      }
      options.onPrepared?.();
      if (request === ingestRequest.current && mounted.current) {
        const origin = current.selectedBundle.bundleOrigin === 'onshape' ? 'Onshape' : 'Fusion';
        setStatus(`Received ${cadModelLabel(current.selectedBundle)} from ${origin}.`);
        // Before the display, so the viewport adopts the CAD slot rather than
        // loading it invisibly behind the parametric design.
        activateWorkspace();
        // Awaited rather than fired and forgotten: the solve gate refuses an
        // ingestion whose mesh is not the one on screen, so a composed
        // pull/ingest/solve that raced this fetch failed on every return after
        // the first with a viewport-mismatch the user could not act on.
        await showIngestedMesh(
          record,
          cadModelLabel(current.selectedBundle),
          reportViewportNotice,
          options.fetcher ?? fetcher,
          viewportGeneration,
        );
      }
      return record;
    } catch (reason) {
      if (reason instanceof SupersededError) {
        if (useCadReturnStore.getState().attemptedPreparationKey === attempt) useCadReturnStore.setState({ attemptedPreparationKey: null });
        throw reason;
      }
      if (!isCurrentDocumentLoad(documentLoad)
        || !useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) {
        if (useCadReturnStore.getState().attemptedPreparationKey === attempt) useCadReturnStore.setState({ attemptedPreparationKey: null });
        const superseded = 'The model or design changed while its mesh was being prepared. Rebuild the mesh for the current model.';
        if (request === ingestRequest.current && mounted.current) setStatus(superseded);
        throw new SupersededError(superseded);
      }
      const message = reason instanceof Error ? reason.message : String(reason);
      reportCadAutomationError('prepare', reason);
      if (reason instanceof CadLinkApiError && reason.code === 'mesh_too_large' && request === ingestRequest.current) {
        // A choice, not a failure: the same pop-up as a held Fusion request.
        meshCeilingChoice.ask({
          name: cadModelLabel(current.selectedBundle),
          nextStep: reason.nextStep ?? message,
          meshLimit: reason.meshLimit,
          bundlePath: current.selectedBundle.bundlePath,
        });
        throw reason;
      }
      if (reason instanceof CadLinkApiError && reason.failure) {
        // A failure the server classified: WG's sentence for it is the
        // model's status line (never a red notice); the raw text of WG's own
        // failure is for developer details only.
        if (request === ingestRequest.current && mounted.current) {
          setIngestError([message, reason.nextStep].filter(Boolean).join(' '), {
            recorded: reason.failure === 'internal', retry: reason.retry ?? true, diagnostic: reason.diagnostic,
          });
        }
        throw reason;
      }
      if (request === ingestRequest.current && mounted.current) {
        const structured = reason instanceof CadLinkApiError ? reason.areaDriftSources : [];
        structured.forEach(current.flagAreaDrift);
        if (!structured.length) {
          const drift = /source ['"]([^'"]+)['"] area drift/i.exec(message);
          if (drift) current.flagAreaDrift(drift[1]);
        }
        setError(message);
        setIngestError(message);
      }
      throw reason instanceof Error ? reason : new Error(message);
    } finally {
      if (ingestAbortController.current === abortController) ingestAbortController.current = null;
      if (request === ingestRequest.current && mounted.current) setIngesting(false);
    }
  };

  // Automatic work and explicit retries both use ingestSelected above. The
  // model-choice token survives React remounts; input changes make a new key.
  const preparationKey = () => cadPreparationKey(useCadReturnStore.getState(), useDocumentStore.getState().identity?.designId ?? null);
  const automaticPreparationPending = () => preparesAutomatically(useCadReturnStore.getState(),
    useDocumentStore.getState().identity?.designId ?? null, preferencesStore.getSnapshot().cadApplication);
  let inFlight: { key: string; override: boolean; promise: Promise<CadReturnIngestRecord> } | null = null;
  const ingestSelected = (options: CadPreparationOptions = {}): Promise<CadReturnIngestRecord> => {
    const key = preparationKey();
    const override = options.meshLimitOverride === true;
    if (inFlight?.key === key && inFlight.override === override) return inFlight.promise;
    const promise = performIngest(options);
    const entry = { key, override, promise };
    inFlight = entry;
    void promise.finally(() => { if (inFlight === entry) inFlight = null; }).catch(() => undefined);
    return promise;
  };
  let automaticTimer: ReturnType<typeof setTimeout> | null = null;
  let automaticQueued = false;
  let automaticRunning = false;
  const requestAutomaticPreparation = () => {
    const selected = useCadReturnStore.getState();
    if (!automaticPreparationPending()) return;
    // A fresh version takes priority over a stale model's edit debounce.
    if (automaticTimer !== null && !selected.ingestRecord) cancelAutomaticPreparation();
    if (automaticQueued || !mounted.current) return;
    automaticQueued = true;
    const evaluate = () => {
      automaticQueued = false;
      automaticTimer = null;
      if (!mounted.current || automaticRunning || ingestAbortController.current || adapter.deferAutomaticPreparation()
        || !automaticPreparationPending()) return;
      automaticRunning = true;
      useCadReturnStore.setState({ attemptedPreparationKey: preparationKey() });
      void ingestSelected().catch(() => undefined).finally(() => {
        automaticRunning = false;
        requestAutomaticPreparation();
      });
    };
    if (useCadReturnStore.getState().ingestRecord) automaticTimer = setTimeout(evaluate, 400);
    else queueMicrotask(evaluate);
  };
  const cancelAutomaticPreparation = () => {
    if (automaticTimer !== null) clearTimeout(automaticTimer);
    automaticTimer = null;
    automaticQueued = false;
  };
  const supersedePreparation = () => {
    useCadReturnStore.getState().beginIngestIntent();
    importedMeshStore.beginIntent();
    ingestRequest.current += 1;
    ingestAbortController.current?.abort();
    setIngesting(false);
  };

  const selectBundleAs = (
    bundle: CadReturnBundle,
    projectLineageId: string | null | undefined = undefined,
    automatic = false,
  ) => {
    if (returnBelongsToAnotherProject(bundle, useDocumentStore.getState().identity?.designId)) {
      refusedForeignReturn.current = true;
      setError(`That model belongs to another CAD-linked project. Open it from File → CAD-linked designs first.`);
      activateWorkspace();
      return;
    }
    // A selection the user made themselves is the acknowledgement a standing
    // refusal was waiting for, and newer intent than any return already sent;
    // displaying a Send is neither.
    if (!automatic) {
      refusedForeignReturn.current = false;
      manualSelectionAt.current = Date.now();
      workspaceModeStore.setImportedInput('cad');
    }
    useCadReturnStore.getState().selectBundle(bundle, projectLineageId, automatic, fetcher);
    void waitForCadProjectSettings().then(ok => { if (ok) rereadDrivers(); });
    importedMeshStore.beginIntent();
    activateWorkspace();
    autoIngestSelected();
  };

  const returnFromOnshape = async () => {
    workspaceModeStore.setImportedInput('cad');
    const identity = useDocumentStore.getState().identity;
    if (!identity?.designId) throw new Error('Send this design to Onshape before returning it.');
    useCadReturnStore.getState().beginSelectionIntent();
    let ingestGeneration = useCadReturnStore.getState().beginIngestIntent();
    const documentLoad = currentDocumentLoad();
    const instanceId = onshapeInstanceId();
    const request = ++ingestRequest.current;
    ingestAbortController.current?.abort();
    const viewportGeneration = importedMeshStore.beginIntent();
    setIngesting(true); setError(null); setStatus(null); setViewportNotice(null);
    try {
      const result = await returnOnshapeToWg(
        identity.designId, fetcher, instanceId,
      );
      // Translation may finish after a project/model change or a newer return.
      // Check the original intent before selection creates a fresh generation.
      if (!mounted.current || request !== ingestRequest.current || !isCurrentDocumentLoad(documentLoad)
        || onshapeInstanceId() !== instanceId
        || useDocumentStore.getState().identity?.designId !== identity.designId
        || !useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) return;
      const sources = result.ingest.sources.map((source) => ({
        id: source.id,
        role: source.role,
        required: source.required,
        suggestedResolutionMm: source.suggested_resolution_mm,
        defaultDriveChannelId: source.default_drive_channel_id,
      }));
      const bundle: CadReturnBundle = {
        name: result.bundle.name,
        // Both halves travel together, and neither is a filesystem location:
        // the path is relative and the origin says which permitted area it is
        // relative to. Keeping the origin here is what lets Rebuild mesh
        // re-ingest this return -- the local mesh controls are displayed for
        // it, and without the origin the request is refused as a WGLink path.
        bundlePath: result.bundle.bundlePath,
        bundleOrigin: result.bundle.bundleOrigin ?? 'onshape',
        modifiedAt: result.ingest.created_at,
        readable: true,
        documentName: result.bundle.documentName,
        requestId: null,
        sourceCount: result.bundle.sourceCount,
        instanceCount: result.bundle.instanceCount,
        designIds: [identity.designId],
        sources,
      };
      const state = useCadReturnStore.getState();
      // Same-inventory Onshape iterations keep the user's solve setup too.
      state.selectArrivedBundle(bundle, result.ingest.project?.lineage_id, fetcher);
      ingestGeneration = state.beginIngestIntent();
      if (!useCadReturnStore.getState().applyIngest(result.ingest, ingestGeneration)) {
        setStatus('Kept the selected design because it changed while Onshape was sending the model.');
        return;
      }
      setBundles([bundle]);
      setStatus(`Received and prepared ${cadModelLabel(result.bundle)} from Onshape.`);
      // The CAD Link panel only exists inside the CAD workspace, and the
      // ingested return is now the solve truth — enter the mode that owns it.
      activateWorkspace();
      await showIngestedMesh(
        result.ingest,
        cadModelLabel(result.bundle),
        reportViewportNotice,
        fetcher,
        viewportGeneration,
      );
    } catch (reason) {
      reportCadAutomationError('onshape-return', reason);
      if (mounted.current && request === ingestRequest.current && isCurrentDocumentLoad(documentLoad)
        && onshapeInstanceId() === instanceId
        && useDocumentStore.getState().identity?.designId === identity.designId
        && useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      if (request === ingestRequest.current && mounted.current) setIngesting(false);
    }
  };

  return { ingestSelected, selectBundleAs, returnFromOnshape, supersedePreparation, requestAutomaticPreparation, cancelAutomaticPreparation };
}

/** Create a listing reader with its own revision cursor and request fence.
 * Its scheduler is supplied by the application; Onshape mode performs no folder read. */
export function createCadReturnArrivalReader(
  overrides: Partial<CadReturnArrivalReaderIntegration> = {},
  fetcher: typeof fetch = fetch,
) {
  const noop = () => undefined;
  const integration: CadReturnArrivalReaderIntegration = {
    autoIngestSelected: noop, cadFolderConfigured: { current: null }, enterCadWorkspace,
    manualSelectionAt: { current: null }, noteCadActivity: noop,
    projectOpenPending: { current: false }, refreshChannelDriverBases: async () => undefined,
    refusedForeignReturn: { current: false }, returnListRequest: { current: 0 },
    seenReturnRevisions: { current: null }, initialChoiceOwed: { current: true }, deferredArrivalRevisions: { current: new Map() },
    awaitingReturn: () => false, awaitingReceiptDisplay: () => false, awaitingLastModel: () => false,
    sendOwnersKnown: () => true, ownsReturn: () => false,
    setError: noop, setStatus: noop, setBundles: noop, setLoading: noop,
    ...overrides,
  };
  return {
    refresh: (options?: RefreshOptions) => refreshCadReturnArrivals(integration, options, fetcher),
    cancel: () => { integration.returnListRequest.current += 1; },
  };
}
