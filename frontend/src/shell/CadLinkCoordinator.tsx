import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CadLinkApiError,
  getFusionCadStatus,
  ingestReturn,
  listReturns,
  type CadReturnBundle,
  type CadReturnIngestRecord,
  type FusionCadStatus,
} from '../api/cadlink';
import { sendDesignToCad, type WgLinkExportResponse } from '../api/designIo';
import { getOnshapeConnection, getOnshapeStatus, returnOnshapeToWg, type OnshapeConnection, type OnshapeStatus } from '../api/onshape';
import { preferencesStore, usePreferences } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { subscribeRevision, useDesignStore } from '../stores/design';
import { useDocumentStore, type DesignIdentity } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { filenameStem } from '../viewport/presentation';
import { fusionWorkflowView } from './cadWorkflowView';
import { workspaceNavigation } from './workspaceNavigation';

interface RefreshOptions {
  background?: boolean;
  autoOpenNew?: boolean;
}

interface CadLinkCoordinatorSnapshot {
  bundles: CadReturnBundle[];
  loading: boolean;
  ingesting: boolean;
  sendingToFusion: boolean;
  error: string | null;
  status: string | null;
  viewportNotice: string | null;
  fusionStatus: FusionCadStatus | null;
  onshapeStatus: OnshapeStatus | null;
  onshapeConnection: OnshapeConnection | null;
  pendingFusionConflict: boolean;
  refresh(options?: RefreshOptions): Promise<void>;
  refreshOnshapeStatus(committed?: DesignIdentity): Promise<void>;
  returnFromOnshape(): Promise<void>;
  expectFusionReturn(requestId: string, requestedAt?: number): void;
  ingest(): Promise<void>;
  /** The one Fusion outbound path: derives open-vs-update and the expected
   * document guard from the live status, and parks on the two-way conflict
   * (returning null) until the user confirms through the coordinator dialog. */
  sendWgToFusion(options?: { confirmed?: boolean }): Promise<WgLinkExportResponse | null>;
  cancelFusionConflict(): void;
  clearFeedback(): void;
  reportError(message: string): void;
  reportStatus(message: string): void;
  reportViewportNotice(message: string | null): void;
}

const unavailable = async () => { throw new Error('CAD Link coordinator is unavailable'); };
const unavailableRefreshOnshape = async (_committed?: DesignIdentity) => unavailable();
let bridgeSnapshot: CadLinkCoordinatorSnapshot = {
  bundles: [],
  loading: true,
  ingesting: false,
  sendingToFusion: false,
  error: null,
  status: null,
  viewportNotice: null,
  fusionStatus: null,
  onshapeStatus: null,
  onshapeConnection: null,
  pendingFusionConflict: false,
  refresh: unavailable,
  refreshOnshapeStatus: unavailableRefreshOnshape,
  returnFromOnshape: unavailable,
  expectFusionReturn: () => undefined,
  ingest: unavailable,
  sendWgToFusion: unavailable,
  cancelFusionConflict: () => undefined,
  clearFeedback: () => undefined,
  reportError: () => undefined,
  reportStatus: () => undefined,
  reportViewportNotice: () => undefined,
};
const bridgeListeners = new Set<() => void>();

export const cadLinkCoordinatorBridge = {
  getSnapshot: () => bridgeSnapshot,
  subscribe(listener: () => void) {
    bridgeListeners.add(listener);
    return () => bridgeListeners.delete(listener);
  },
};

function publishBridge(snapshot: CadLinkCoordinatorSnapshot): void {
  bridgeSnapshot = snapshot;
  bridgeListeners.forEach((listener) => listener());
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

/** Prefer the independently tessellated full CAD display artifact. Older
 * records and advisory display failures fall back to the exact solver mesh. */
export async function showIngestedMeshInViewport(
  record: CadReturnIngestRecord,
  name: string,
  onNotice?: (notice: string) => void,
  fetcher: typeof fetch = fetch,
  generation = importedMeshStore.beginIntent(),
): Promise<void> {
  const ingestId = record.ingest_id;
  const available = importedMeshStore.getSnapshot().cad;
  if (available?.ingestId === ingestId) {
    if (workspaceModeStore.getSnapshot().mode === 'cad') importedMeshStore.showCad(generation);
    return;
  }
  try {
    const response = await fetcher(`/api/cadlink/ingest/${encodeURIComponent(ingestId)}/viewport-mesh`);
    if (!importedMeshStore.isCurrentGeneration(generation)) return;
    if (response.ok) {
      const meshText = await response.text();
      if (!importedMeshStore.isCurrentGeneration(generation)) return;
      importedMeshStore.setCad(createImportedMeshScene(
        name,
        parseMSH(meshText),
        'cad',
        ingestId,
        record.symmetry.cut_planes ?? [],
        {
          fullDomain: true,
          solvedTriangleCount: record.mesh?.stats.triangle_count,
          artifactToken: record.viewport_mesh?.content_sha256 ?? `${ingestId}:viewport`,
        },
      ), generation, workspaceModeStore.getSnapshot().mode === 'cad');
      return;
    }
    if (response.status === 409) {
      onNotice?.('The independent CAD viewport artifact failed verification. Showing the exact solver mesh instead.');
    }
  } catch {
    // The independent display artifact is advisory; try the solver artifact.
  }
  if (!importedMeshStore.isCurrentGeneration(generation)) return;
  try {
    const response = await fetcher(`/api/cadlink/ingest/${encodeURIComponent(ingestId)}/mesh`);
    if (!response.ok || !importedMeshStore.isCurrentGeneration(generation)) return;
    const meshText = await response.text();
    if (!importedMeshStore.isCurrentGeneration(generation)) return;
    importedMeshStore.setCad(createImportedMeshScene(
      name,
      parseMSH(meshText),
      'cad',
      ingestId,
      record.symmetry.cut_planes ?? [],
      {
        solvedTriangleCount: record.mesh?.stats.triangle_count,
        artifactToken: record.mesh_content_sha256 ?? `${ingestId}:solver`,
      },
    ), generation, workspaceModeStore.getSnapshot().mode === 'cad');
  } catch {
    // The viewport keeps whatever it was showing if both artifacts fail.
  }
}

export function CadLinkCoordinator() {
  const preferences = usePreferences();
  const design = useDesignStore((state) => state.design);
  const designRevision = useDesignStore((state) => state.designRevision);
  const identity = useDocumentStore((state) => state.identity);
  const filename = useDocumentStore((state) => state.filename);
  const setCadLink = useDocumentStore((state) => state.setCadLink);
  const selectedBundlePath = useCadReturnStore((state) => state.selectedBundle?.bundlePath ?? null);
  const [bundles, setBundles] = useState<CadReturnBundle[]>([]);
  const [loading, setLoading] = useState(true);
  const [ingesting, setIngesting] = useState(false);
  const [sendingToFusion, setSendingToFusion] = useState(false);
  const [pendingFusionConflict, setPendingFusionConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [viewportNotice, setViewportNotice] = useState<string | null>(null);
  const [fusionStatus, setFusionStatus] = useState<FusionCadStatus | null>(null);
  const [onshapeStatus, setOnshapeStatus] = useState<OnshapeStatus | null>(null);
  const [onshapeConnection, setOnshapeConnection] = useState<OnshapeConnection | null>(null);
  const seenReturnRevisions = useRef<Map<string, string> | null>(null);
  const returnListRequest = useRef(0);
  const fusionSendRequest = useRef(0);
  const ingestRequest = useRef(0);
  const fusionStatusRequest = useRef(0);
  const onshapeStatusRequest = useRef(0);
  const onshapeConnectionRequested = useRef(false);
  const mounted = useRef(true);
  const pendingReturnRequestId = useRef<string | null>(null);
  const pendingReturnRequestedAt = useRef<number | null>(null);
  const onshape = preferences.cadApplication === 'onshape';

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      fusionSendRequest.current += 1;
      ingestRequest.current += 1;
    };
  }, []);

  useEffect(() => subscribeRevision((event) => {
    if (event.reason !== 'load') return;
    workspaceModeStore.setMode('parametric');
    // A replacement may be the document this return belongs to, so retain the
    // evidence and channel work for the freshness verdict instead of guessing
    // ownership here. The stale gate makes the old geometry unsendable now.
    useCadReturnStore.getState().markIngestStale(
      'The design was replaced after this CAD return was ingested. Re-ingest before solving.',
    );
  }), []);

  const refreshFusionStatus = useCallback(async () => {
    if (preferences.cadApplication !== 'fusion360') return;
    const request = ++fusionStatusRequest.current;
    try {
      const next = await getFusionCadStatus(design, identity, selectedBundlePath);
      if (request === fusionStatusRequest.current
        && ['closed', 'addin_offline', 'no_document', 'not_linked', 'current', 'stale'].includes(next.state)) {
        setFusionStatus(next);
      }
    } catch {
      // Presence is advisory. Workspace and export errors are presented by the
      // actual action; a missed heartbeat must not hide CAD returns.
    }
  }, [design, identity, preferences.cadApplication, selectedBundlePath]);

  useEffect(() => {
    setFusionStatus(null);
    if (preferences.cadApplication !== 'fusion360') return undefined;
    void refreshFusionStatus();
    const timer = window.setInterval(() => { void refreshFusionStatus(); }, 2_500);
    return () => {
      window.clearInterval(timer);
      fusionStatusRequest.current += 1;
    };
  }, [designRevision, preferences.cadApplication, refreshFusionStatus]);

  // `committed` is the identity a send just registered. Without it the refresh
  // that follows a first send would still carry the pre-send identity -- which
  // is null for an unsaved design -- and report the design it had just linked
  // as unlinked until the next render settled.
  const refreshOnshapeStatus = useCallback(async (committed?: DesignIdentity) => {
    const request = ++onshapeStatusRequest.current;
    try {
      const next = await getOnshapeStatus(design, committed ?? identity);
      if (request === onshapeStatusRequest.current) setOnshapeStatus(next);
    } catch {
      // Advisory, like the Fusion heartbeat: the send itself reports failures.
    }
  }, [design, identity]);

  // No interval. This status is derived from WG's own registry and changes
  // only when the design or a send does, both of which re-run this effect.
  useEffect(() => {
    setOnshapeStatus(null);
    if (!onshape) return;
    void refreshOnshapeStatus();
  }, [designRevision, onshape, refreshOnshapeStatus]);

  // The connection route is the only check here that spends Onshape API rate
  // limit. Delay it until Onshape is used, then make at most one request for
  // this always-mounted coordinator's lifetime -- never on a timer.
  useEffect(() => {
    if (!onshape || onshapeConnectionRequested.current) return;
    onshapeConnectionRequested.current = true;
    void getOnshapeConnection()
      .then((next) => { if (mounted.current) setOnshapeConnection(next); })
      .catch(() => { /* the status card already reports an unconfigured link */ });
  }, [onshape]);

  const refresh = useCallback(async (options: RefreshOptions = {}) => {
    const background = options.background === true;
    const request = ++returnListRequest.current;
    // The wgreturn folder belongs exclusively to the Fusion add-in. Keeping
    // the guard inside the command also prevents callers from accidentally
    // reading it while the always-mounted coordinator is in Onshape mode.
    if (preferencesStore.getSnapshot().cadApplication === 'onshape') {
      setLoading(false);
      return;
    }
    if (!background) { setLoading(true); setError(null); }
    try {
      const response = await listReturns();
      // Poll, manual refresh, and the post-send refresh may overlap. Only the
      // newest request may revise evidence or auto-select a return; otherwise a
      // slow old directory snapshot can roll the whole CAD state backwards.
      if (request !== returnListRequest.current) return;
      setBundles(response.items);
      const previous = seenReturnRevisions.current;
      const next = new Map(response.items.map((item) => [item.bundlePath, item.modifiedAt]));
      const requested = pendingReturnRequestId.current
        ? response.items.find((item) => (
            item.readable && item.requestId === pendingReturnRequestId.current
          )) ?? null
        : null;
      if (
        pendingReturnRequestId.current
        && pendingReturnRequestedAt.current !== null
        && Date.now() - pendingReturnRequestedAt.current > 60_000
      ) {
        pendingReturnRequestId.current = null;
        pendingReturnRequestedAt.current = null;
        setError('Fusion did not return the requested model within 60 seconds. Check Fusion for a WGLink message, then retry.');
      }
      const arrived = options.autoOpenNew
        ? requested ?? (
            pendingReturnRequestId.current
              ? null
              : newestReturnArrival(response.items, previous)
          )
        : null;
      seenReturnRevisions.current = next;
      const initial = previous === null
        ? response.items.find((item) => item.readable) ?? null
        : null;
      const opened = arrived ?? initial;
      if (opened) {
        useCadReturnStore.getState().selectBundle(opened);
        // Selecting evidence invalidates a load for the previous return, but
        // mode—not return discovery—decides what the viewport displays.
        importedMeshStore.beginIntent();
      }
      if (arrived) {
        if (arrived.requestId === pendingReturnRequestId.current) {
          pendingReturnRequestId.current = null;
          pendingReturnRequestedAt.current = null;
        }
        setStatus(`Received ${arrived.documentName ?? arrived.name} from Fusion 360.`);
        workspaceNavigation.activate('cadlink');
      } else if (!initial) {
        const selected = useCadReturnStore.getState().selectedBundle;
        if (!selected) return;
        const current = response.items.find((bundle) => bundle.bundlePath === selected.bundlePath);
        useCadReturnStore.getState().refreshSelectedBundle(current ?? null);
      }
    } catch (reason) {
      if (request === returnListRequest.current && !background) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      // A background poll can supersede the initial foreground listing. The
      // latest completion owns the loading flag even when it did not raise it.
      if (request === returnListRequest.current) setLoading(false);
    }
  }, []);

  /** One outbound Fusion action for every surface. The rail card and CAD Link
   * panel both call this bridge so identity adoption, feedback, and return-list
   * refresh cannot drift into subtly different send paths. */
  const sendToFusion = useCallback(async (target?: { documentId: string; returnStateHash: string | null }) => {
    const request = ++fusionSendRequest.current;
    setSendingToFusion(true); setError(null); setStatus(null);
    try {
      const result = await sendDesignToCad(
        design,
        designRevision,
        filenameStem(filename),
        identity,
        fetch,
        undefined,
        target ?? null,
      );
      if (request === fusionSendRequest.current && mounted.current) {
        if (result.identity) setCadLink(result.identity, 'current');
        setStatus(target
          ? `Update sent to Fusion 360 · sequence ${result.sequence}`
          : `Opening in Fusion 360 · sequence ${result.sequence}`);
        await refresh();
      }
      return result;
    } catch (reason) {
      if (request === fusionSendRequest.current && mounted.current) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      if (request === fusionSendRequest.current && mounted.current) setSendingToFusion(false);
    }
  }, [design, designRevision, filename, identity, refresh, setCadLink]);

  // The one Fusion outbound entry point (menu, rail, and panel). Deriving the
  // action and the expected-document guard here means no call site can send an
  // update without them — the drift that let the file menu bypass the two-way
  // conflict confirmation.
  const sendWgToFusion = useCallback(async (options?: { confirmed?: boolean }) => {
    const current = fusionStatus;
    const action = fusionWorkflowView(current).action;
    if (action === 'update' && current?.fusionChangesAvailable && !options?.confirmed) {
      setPendingFusionConflict(true);
      return null;
    }
    setPendingFusionConflict(false);
    return sendToFusion(action === 'update' && current?.documentId
      ? { documentId: current.documentId, returnStateHash: current.link?.documentSignatureHash ?? null }
      : undefined);
  }, [fusionStatus, sendToFusion]);

  const cancelFusionConflict = useCallback(() => setPendingFusionConflict(false), []);

  // Returns arrive in the workspace's wgreturn folder, which only the Fusion
  // add-in writes. Onshape bundles use WG's data directory and never enter this
  // lifecycle, so there is deliberately no returns poll in Onshape mode.
  useEffect(() => {
    if (onshape) { setLoading(false); return undefined; }
    void refresh({ autoOpenNew: true });
    const timer = window.setInterval(() => {
      void refresh({ background: true, autoOpenNew: true });
    }, 2_500);
    return () => {
      window.clearInterval(timer);
      returnListRequest.current += 1;
    };
  }, [onshape, refresh]);

  const expectFusionReturn = useCallback((requestId: string, requestedAt = Date.now()) => {
    pendingReturnRequestId.current = requestId;
    pendingReturnRequestedAt.current = requestedAt;
  }, []);

  const reportViewportNotice = useCallback((message: string | null) => setViewportNotice(message), []);

  const ingest = useCallback(async () => {
    const current = useCadReturnStore.getState();
    if (!current.selectedBundle) return;
    // This intent covers the ingest record itself. The viewport has a separate
    // token because its follow-up artifact fetch can be superseded independently.
    const ingestGeneration = current.beginIngestIntent();
    const request = ++ingestRequest.current;
    // Intent starts before the network request. A later viewport choice must
    // win even when this ingest's mesh fetch eventually completes.
    const viewportGeneration = importedMeshStore.beginIntent();
    setIngesting(true); setError(null); setStatus(null); setViewportNotice(null);
    try {
      const skipped = new Set(current.skippedSourceIds);
      const record = await ingestReturn({
        bundlePath: current.selectedBundle.bundlePath,
        mesh: {
          rigidSizeMm: current.rigidSizeMm,
          transitionMm: current.transitionMm,
          sourceSizeMm: Object.fromEntries(Object.entries(current.sourceSizesMm).filter(([id]) => !skipped.has(id))),
        },
        skippedSourceIds: current.skippedSourceIds,
        areaDriftOverrides: current.areaDriftOverrides,
      });
      if (!useCadReturnStore.getState().applyIngest(record, ingestGeneration)) {
        if (request === ingestRequest.current && mounted.current) {
          setStatus('Discarded a completed ingest because its selected return or design was superseded. Rebuild the mesh for the current state.');
        }
        return;
      }
      if (request !== ingestRequest.current || !mounted.current) return;
      setStatus(`Ingested ${record.ingest_id}. Review the verdicts before solving.`);
      void showIngestedMeshInViewport(
        record,
        current.selectedBundle.documentName || current.selectedBundle.name,
        reportViewportNotice,
        fetch,
        viewportGeneration,
      );
    } catch (reason) {
      if (!useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) {
        if (request === ingestRequest.current && mounted.current) {
          setStatus('Discarded an ingest response because its selected return or design was superseded. Rebuild the mesh for the current state.');
        }
        return;
      }
      if (request !== ingestRequest.current || !mounted.current) return;
      const message = reason instanceof Error ? reason.message : String(reason);
      const structured = reason instanceof CadLinkApiError ? reason.areaDriftSources : [];
      structured.forEach(current.flagAreaDrift);
      if (!structured.length) {
        const drift = /source ['"]([^'"]+)['"] area drift/i.exec(message);
        if (drift) current.flagAreaDrift(drift[1]);
      }
      setError(message);
    } finally {
      if (request === ingestRequest.current && mounted.current) setIngesting(false);
    }
  }, [reportViewportNotice]);

  const returnFromOnshape = useCallback(async () => {
    if (!identity?.designId) throw new Error('Send this design to Onshape before returning it.');
    const ingestGeneration = useCadReturnStore.getState().beginIngestIntent();
    const viewportGeneration = importedMeshStore.beginIntent();
    setIngesting(true); setError(null); setStatus(null); setViewportNotice(null);
    try {
      const result = await returnOnshapeToWg(identity.designId);
      const sources = result.ingest.sources.map((source) => ({
        id: source.id,
        role: source.role,
        required: source.required,
        suggestedResolutionMm: source.suggested_resolution_mm,
        defaultDriveChannelId: source.default_drive_channel_id,
      }));
      const bundle: CadReturnBundle = {
        name: result.bundle.name,
        bundlePath: result.bundle.bundlePath,
        modifiedAt: result.ingest.created_at,
        readable: true,
        documentName: result.bundle.documentName,
        requestId: null,
        sourceCount: result.bundle.sourceCount,
        instanceCount: result.bundle.instanceCount,
        sources,
      };
      const state = useCadReturnStore.getState();
      state.selectBundle(bundle);
      const selectedGeneration = state.beginIngestIntent();
      if (!useCadReturnStore.getState().applyIngest(result.ingest, selectedGeneration)) {
        setStatus('Discarded the Onshape return because the selected design changed.');
        return;
      }
      setBundles([bundle]);
      setStatus(`Returned and ingested ${result.bundle.documentName ?? result.bundle.name} from Onshape.`);
      // The CAD Link panel only exists inside the CAD workspace, and the
      // ingested return is now the solve truth — enter the mode that owns it.
      workspaceModeStore.setMode('cad');
      workspaceNavigation.activate('cadlink');
      void showIngestedMeshInViewport(
        result.ingest,
        result.bundle.documentName ?? result.bundle.name,
        reportViewportNotice,
        fetch,
        viewportGeneration,
      );
    } catch (reason) {
      if (useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      setIngesting(false);
    }
  }, [identity?.designId, reportViewportNotice]);

  const clearFeedback = useCallback(() => { setError(null); setStatus(null); }, []);
  const reportError = useCallback((message: string) => setError(message), []);
  const reportStatus = useCallback((message: string) => setStatus(message), []);

  useEffect(() => {
    publishBridge({
      bundles,
      loading,
      ingesting,
      sendingToFusion,
      error,
      status,
      viewportNotice,
      fusionStatus,
      onshapeStatus,
      onshapeConnection,
      pendingFusionConflict,
      refresh,
      refreshOnshapeStatus,
      returnFromOnshape,
      expectFusionReturn,
      ingest,
      sendWgToFusion,
      cancelFusionConflict,
      clearFeedback,
      reportError,
      reportStatus,
      reportViewportNotice,
    });
    return () => publishBridge({
      ...bridgeSnapshot,
      bundles: [],
      loading: true,
      ingesting: false,
      sendingToFusion: false,
      error: null,
      status: null,
      viewportNotice: null,
      fusionStatus: null,
      onshapeStatus: null,
      onshapeConnection: null,
      pendingFusionConflict: false,
      refresh: unavailable,
      refreshOnshapeStatus: unavailableRefreshOnshape,
      returnFromOnshape: unavailable,
      expectFusionReturn: () => undefined,
      ingest: unavailable,
      sendWgToFusion: unavailable,
      cancelFusionConflict: () => undefined,
      clearFeedback: () => undefined,
      reportError: () => undefined,
      reportStatus: () => undefined,
      reportViewportNotice: () => undefined,
    });
  }, [
    bundles,
    cancelFusionConflict,
    clearFeedback,
    error,
    expectFusionReturn,
    fusionStatus,
    ingest,
    ingesting,
    sendingToFusion,
    loading,
    onshapeConnection,
    onshapeStatus,
    pendingFusionConflict,
    refresh,
    refreshOnshapeStatus,
    returnFromOnshape,
    reportError,
    reportStatus,
    reportViewportNotice,
    sendWgToFusion,
    status,
    viewportNotice,
  ]);

  // The conflict dialog renders here, not in any one entry point, so the menu,
  // the rail card, and the panel all pass through the same confirmation.
  if (!pendingFusionConflict) return null;
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) cancelFusionConflict(); }}>
    <div className="settings-dialog update-dialog" role="dialog" aria-modal="true" aria-labelledby="cad-conflict-title">
      <header><div><h2 id="cad-conflict-title">Both WG and Fusion changed</h2></div></header>
      <div className="update-dialog-body">
        <p>Sending rebuilds only the linked waveguide from WG. Separate cabinet and mid-woofer bodies stay in Fusion, but direct edits to the linked waveguide are replaced.</p>
        <p>To keep the Fusion edits instead, cancel and bring the Fusion geometry into WG first.</p>
        <div className="update-dialog-actions">
          <button onClick={cancelFusionConflict}>Cancel</button>
          <button className="primary" disabled={sendingToFusion} onClick={() => { void sendWgToFusion({ confirmed: true }).catch(() => undefined); }}>Continue: send WG changes</button>
        </div>
      </div>
    </div>
  </div>;
}
