import { currentDocumentLoad, isCurrentDocumentLoad } from '../../stores/design';
import { returnOnshapeToWg } from '../../api/onshape';
import { useCadPreparationStore } from '../../stores/cadPreparation';
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
import { useCadReturnStore } from '../../stores/cadReturn';
import { importedMeshStore } from '../../viewport/importedMeshStore';

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

export function newestReturnArrival(
  items: CadReturnBundle[],
  previous: Map<string, string> | null,
  nowMs = Date.now(),
): CadReturnBundle | null {
  const recentThreshold = nowMs - 60_000;
  return items.find((item) => item.readable && (
    previous
      ? previous.get(item.bundlePath) !== item.modifiedAt
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
  /** Recheck after the listing await: a push may have invalidated the feed meanwhile. */
  sendOwnersKnown(): boolean;
  ownsReturn(bundle: CadReturnBundle): boolean;
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
    seenReturnRevisions, initialChoiceOwed, deferredArrivalRevisions, awaitingReturn, awaitingReceiptDisplay, sendOwnersKnown, ownsReturn,
    setError, setStatus, setBundles, setLoading,
  } = integration;
  const background = options.background === true;
  const request = ++returnListRequest.current;
  const selection = useCadReturnStore.getState().captureSelectionIntent();
  const documentLoad = currentDocumentLoad();
  if (preferencesStore.getSnapshot().cadApplication === 'onshape') {
    if (projectOpenPending.current) {
      projectOpenPending.current = false;
      setStatus('Project design loaded. Return it from Onshape to prepare Simulation geometry.');
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
    const next = new Map(response.items.map((item) => [item.bundlePath, item.modifiedAt]));
    const wasConfigured = cadFolderConfigured.current;
    cadFolderConfigured.current = response.cadFolderConfigured;
    cadCoordinationStore.set(response.coordination === 'on' ? 'on' : 'off');
    const listingChanged = previous === null
      || previous.size !== next.size
      || [...next].some(([path, modifiedAt]) => previous.get(path) !== modifiedAt);
    if (listingChanged || response.cadFolderConfigured !== wasConfigured) noteCadActivity();
    let arrivalPrevious = previous;
    const deferNewArrivals = options.deferNewArrivals || !sendOwnersKnown();
    if (deferNewArrivals) {
      deferredArrivalRevisions.current.forEach((_, path) => {
        if (!next.has(path)) deferredArrivalRevisions.current.delete(path);
      });
      response.items.forEach((item) => {
        if (newestReturnArrival([item], previous)) deferredArrivalRevisions.current.set(item.bundlePath, item.modifiedAt);
      });
    } else if (deferredArrivalRevisions.current.size) {
      const deferredPrevious = new Map(previous);
      deferredArrivalRevisions.current.forEach((revision, path) => {
        if (next.get(path) === revision) deferredPrevious.delete(path);
      });
      arrivalPrevious = deferredPrevious;
      deferredArrivalRevisions.current.clear();
    }
    // An owed initial choice must let queued Sends finish first, even when a
    // background poll also notices a new revision in the folder.
    const arrived = options.autoOpenNew && !deferNewArrivals && !awaitingReturn()
      && !(initialChoiceOwed.current && awaitingReceiptDisplay())
      ? newestReturnArrival(response.items.filter((item) => !ownsReturn(item)), arrivalPrevious) : null;
    seenReturnRevisions.current = next;
    const currentDesignId = useDocumentStore.getState().identity?.designId;
    if (useCadReturnStore.getState().selectedBundle) initialChoiceOwed.current = false;
    const canChooseInitial = initialChoiceOwed.current && !awaitingReturn() && !awaitingReceiptDisplay()
      && !useCadReturnStore.getState().selectedBundle;
    const initial = canChooseInitial
      ? response.items.find((item) => (
          item.readable && (
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
        ? useCadReturnStore.getState().selectArrivedBundle(arrived, destination)
        : (useCadReturnStore.getState().selectBundle(opened, destination, true), 'initial');
      void refreshChannelDriverBases().catch(() => undefined);
      importedMeshStore.beginIntent();
      initialChoiceOwed.current = false;
    }
    if (projectOpenPending.current && canChooseInitial) {
      projectOpenPending.current = false;
      setStatus(initial
        ? `Project design loaded. Selected the latest matching return from ${initial.documentName ?? initial.name}; prepare it to restore Simulation geometry.`
        : 'Project design loaded. No matching CAD return is available yet; return the project from Fusion or Onshape to prepare Simulation geometry.');
    } else if (projectOpenPending.current && useCadReturnStore.getState().selectedBundle) {
      // A receipt or another selection owner resolved the project choice.
      projectOpenPending.current = false;
    }
    if (arrived) {
      if (projectMismatch) {
        refusedForeignReturn.current = true;
        const reason = `Received ${arrived.documentName ?? arrived.name}, but it belongs to another CAD-linked project. Open that project from File → CAD-linked designs.`;
        setError(reason);
        enterCadWorkspace();
        return;
      }
      const arrivedName = arrived.documentName ?? arrived.name;
      const keptName = selected ? selected.documentName ?? selected.name : null;
      setStatus(heldForManualPick
        ? `Received ${arrivedName} from Fusion 360. You selected ${keptName} after it was sent, so ${keptName} stays selected; select ${arrivedName} from the return list to use it.`
        : `Received ${arrivedName} from Fusion 360.${
          continuity === 'carried' ? ' Kept your mesh, channel, and solve settings.' : ''
        }`);
      enterCadWorkspace();
      if (!heldForManualPick) autoIngestSelected();
    } else if (!initial) {
      const currentSelected = useCadReturnStore.getState().selectedBundle;
      if (!currentSelected) return;
      if (currentSelected.bundleOrigin === 'native') return;
      if (!response.cadFolderConfigured) return;
      const current = response.items.find((bundle) => bundle.bundlePath === currentSelected.bundlePath);
      useCadReturnStore.getState().refreshSelectedBundle(current ?? null);
    }
  } catch (reason) {
    if (request === returnListRequest.current
      && useCadReturnStore.getState().isCurrentSelectionIntent(selection)
      && isCurrentDocumentLoad(documentLoad) && !background) {
      if (projectOpenPending.current) {
        projectOpenPending.current = false;
        setStatus('Project design loaded, but its CAD returns could not be read. Refresh CAD Link to try again.');
      }
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  } finally {
    if (request === returnListRequest.current) setLoading(false);
  }
}

export function enterCadWorkspace(): void {
  workspaceModeStore.setMode('cad');
  workspaceNavigation.activate('cadlink');
}

export interface CadArrivalActionsIntegration {
  ingestRequest: Ref<number>;
  ingestAbortController: Ref<AbortController | null>;
  mounted: Ref<boolean>;
  manualSelectionAt: Ref<number | null>;
  refusedForeignReturn: Ref<boolean>;
  setIngesting(value: boolean): void;
  setIngestError(value: string | null): void;
  setError(value: string | null): void;
  setStatus(value: string | null): void;
  setViewportNotice(value: string | null): void;
  setBundles(value: CadReturnBundle[]): void;
  reportViewportNotice(value: string | null): void;
  enterCadWorkspace(): void;
  autoIngestSelected(): void;
  rereadDrivers(): void;
  fusionInstanceId(): string | null;
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
    fusionInstanceId: () => null, onshapeInstanceId: () => null,
    showIngestedMesh: showIngestedMeshInViewport,
  };
  const adapter = { ...defaults, ...integration };
  const { ingestRequest, ingestAbortController, mounted, manualSelectionAt,
    refusedForeignReturn, setIngesting, setIngestError, setError, setStatus,
    setViewportNotice, setBundles, reportViewportNotice, enterCadWorkspace: activateWorkspace,
    autoIngestSelected, rereadDrivers,
    fusionInstanceId, onshapeInstanceId, showIngestedMesh } = adapter;
  const ingestSelected = async (): Promise<CadReturnIngestRecord> => {
    const current = useCadReturnStore.getState();
    if (!current.selectedBundle) throw new Error('Select a CAD return before preparing a simulation.');
    if (current.selectedBundle.bundleOrigin === 'native') throw new Error('Edit the native source assembly and prepare it again.');
    // This intent covers the ingest record itself. The viewport has a separate
    // token because its follow-up artifact fetch can be superseded independently.
    const ingestGeneration = current.beginIngestIntent();
    const documentLoad = currentDocumentLoad();
    const request = ++ingestRequest.current;
    ingestAbortController.current?.abort();
    const abortController = new AbortController();
    ingestAbortController.current = abortController;
    // Intent starts before the network request. A later viewport choice must
    // win even when this ingest's mesh fetch eventually completes.
    const viewportGeneration = importedMeshStore.beginIntent();
    setIngesting(true); setIngestError(null); setError(null); setStatus(null); setViewportNotice(null);
    try {
      const skipped = new Set(current.skippedSourceIds);
      const record = await ingestReturn({
        bundlePath: current.selectedBundle.bundlePath,
        // A workspace listing item has no origin and is a WGLink return; an
        // Onshape return carries its own. The server resolves each against
        // its own root, so this is the only thing that has to be dispatched.
        bundleOrigin: current.selectedBundle.bundleOrigin ?? 'wglink',
        mesh: {
          rigidSizeMm: current.rigidSizeMm,
          transitionMm: current.transitionMm,
          sourceSizeMm: Object.fromEntries(Object.entries(current.sourceSizesMm).filter(([id]) => !skipped.has(id))),
        },
        skippedSourceIds: current.skippedSourceIds,
        areaDriftOverrides: current.areaDriftOverrides,
        expectedDesignId: useDocumentStore.getState().identity?.designId ?? null,
        expectedInstanceId: (() => {
          const bundle = current.selectedBundle;
          const instances = bundle.instances ?? [];
          const liveInstanceId = fusionInstanceId() ?? null;
          if (liveInstanceId && instances.some((item) => item.instanceId === liveInstanceId)) {
            return liveInstanceId;
          }
          const anchor = bundle.solverAnchorInstanceId ?? null;
          return anchor && instances.some((item) => item.instanceId === anchor)
            ? anchor
            : null;
        })(),
        symmetryMode: useCadPreparationStore.getState().symmetryMode,
      }, fetcher, abortController.signal);
      if (!isCurrentDocumentLoad(documentLoad)
        || !useCadReturnStore.getState().applyIngest(record, ingestGeneration)) {
        const superseded = 'Discarded a completed ingest because its selected return or design was superseded. Rebuild the mesh for the current state.';
        if (request === ingestRequest.current && mounted.current) setStatus(superseded);
        throw new SupersededError(superseded);
      }
      if (request === ingestRequest.current && mounted.current) {
        const origin = current.selectedBundle.bundleOrigin === 'onshape' ? 'Onshape' : 'Fusion';
        setStatus(`Received ${current.selectedBundle.documentName || current.selectedBundle.name} from ${origin}.`);
        // Before the display, so the viewport adopts the CAD slot rather than
        // loading it invisibly behind the parametric design.
        activateWorkspace();
        // Awaited rather than fired and forgotten: the solve gate refuses an
        // ingestion whose mesh is not the one on screen, so a composed
        // pull/ingest/solve that raced this fetch failed on every return after
        // the first with a viewport-mismatch the user could not act on.
        await showIngestedMesh(
          record,
          current.selectedBundle.documentName || current.selectedBundle.name,
          reportViewportNotice,
          fetcher,
          viewportGeneration,
        );
      }
      return record;
    } catch (reason) {
      if (reason instanceof SupersededError) throw reason;
      if (!isCurrentDocumentLoad(documentLoad)
        || !useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) {
        const superseded = 'Discarded an ingest response because its selected return or design was superseded. Rebuild the mesh for the current state.';
        if (request === ingestRequest.current && mounted.current) setStatus(superseded);
        throw new SupersededError(superseded);
      }
      const message = reason instanceof Error ? reason.message : String(reason);
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
      setError(`That return belongs to another CAD-linked project. Open it from File → CAD-linked designs first.`);
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
    useCadReturnStore.getState().selectBundle(bundle, projectLineageId, automatic);
    rereadDrivers();
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
      state.selectArrivedBundle(bundle);
      ingestGeneration = state.beginIngestIntent();
      if (!useCadReturnStore.getState().applyIngest(result.ingest, ingestGeneration)) {
        setStatus('Discarded the Onshape return because the selected design changed.');
        return;
      }
      setBundles([bundle]);
      setStatus(`Returned and ingested ${result.bundle.documentName ?? result.bundle.name} from Onshape.`);
      // The CAD Link panel only exists inside the CAD workspace, and the
      // ingested return is now the solve truth — enter the mode that owns it.
      activateWorkspace();
      await showIngestedMesh(
        result.ingest,
        result.bundle.documentName ?? result.bundle.name,
        reportViewportNotice,
        fetcher,
        viewportGeneration,
      );
    } catch (reason) {
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

  return { ingestSelected, selectBundleAs, returnFromOnshape, supersedePreparation };
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
    awaitingReturn: () => false, awaitingReceiptDisplay: () => false, sendOwnersKnown: () => true, ownsReturn: () => false,
    setError: noop, setStatus: noop, setBundles: noop, setLoading: noop,
    ...overrides,
  };
  return {
    refresh: (options?: RefreshOptions) => refreshCadReturnArrivals(integration, options, fetcher),
    cancel: () => { integration.returnListRequest.current += 1; },
  };
}
