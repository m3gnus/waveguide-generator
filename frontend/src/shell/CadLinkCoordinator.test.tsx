import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord, FusionCadStatus } from '../api/cadlink';
import { selectCadWorkspace } from '../api/cadWorkspace';
import { resetCadCoordinationForTests } from '../api/cadCoordination';
import type { CadOperationSummary } from '../api/cadOperations';
import { applyOpenedDesign, openCadLinkedProject, takeDesignOpenTicket } from '../design/openCadProject';
import { importedSubmissionBlocker } from '../jobs/importedSubmission';
import { showJobModel } from '../jobs/showJobModel';
import { preferencesStore } from '../prefs/preferences';
import { expandLegacy, toWire, withChannel, withPair } from '../results/crossoverSpec';
import { resetCadOperationsStore, useCadOperationsStore } from '../stores/cadOperations';
import { resetCadPreparationStore, useCadPreparationStore } from '../stores/cadPreparation';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { rememberCadProject, rememberedCadProject } from '../stores/cadProjectMemory';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import {
  CadLinkCoordinator,
  cadLinkCoordinatorBridge,
  cadPollIntervals,
  resetCadPollIntervals,
  returnBelongsToAnotherProject,
  showCadJobModel,
  showIngestedMeshInViewport,
  SupersededError,
} from './CadLinkCoordinator';
import { cadSolveBlockerNow, jobsCoordinatorBridge } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

const initialBundle: CadReturnBundle = {
  name: 'speaker.wgreturn',
  bundlePath: 'wgreturn/speaker.wgreturn',
  modifiedAt: '2026-08-11T00:00:00Z',
  readable: true,
  documentName: 'Speaker',
  requestId: null,
  sourceCount: 1,
  instanceCount: 1,
  designIds: [],
  sources: [{
    id: 'source-hf',
    role: 'HF',
    required: true,
    suggestedResolutionMm: 4,
    defaultDriveChannelId: 'drive-hf',
  }],
};

const closedFusion: FusionCadStatus = {
  cadApplication: 'fusion360',
  cadFolderConfigured: true,
  cadFolderPath: '/workspace',
  state: 'closed',
  processRunning: false,
  running: false,
  updatedAt: null,
  documentName: null,
  documentId: null,
  currentFormula: 'OSSE',
  fusionFormula: null,
  link: null,
  wgChangesAvailable: false,
  fusionChangesAvailable: false,
  documentChanged: false,
  documentChangeDetectable: false,
  staleDetectionExplanation: null,
  realizedDimensions: { state: 'link_unavailable', instanceId: null, exportId: null, parameters: [] },
};

const ingestRecord: CadReturnIngestRecord = {
  ingest_id: 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C',
  created_at: '',
  return_id: '',
  manifest_sha256: `sha256:${'1'.repeat(64)}`,
  artifact_sha256: `sha256:${'2'.repeat(64)}`,
  report_sha256: `sha256:${'3'.repeat(64)}`,
  acoustic_domain: 'free-space',
  scope: { status: 'clean', degraded_skip_count: 0 },
  sources: [{
    id: 'source-hf', role: 'HF', required: true, instance_id: null,
    default_drive_channel_id: 'drive-hf', suggested_resolution_mm: 4,
  }],
  mesh_sizes: { rigid_size_mm: 4, transition_mm: 4, source_size_mm: { 'source-hf': 4 } },
  skipped_source_ids: [],
  freshness: { verdict: 'per-instance', instances: [] },
  findings: [],
  symmetry: { planes: {}, cut_planes: [] },
  healing: { performed: false, mode: 'none' },
  sizing_estimate: {},
  polar_grid_derivation: {},
  tag_map: {},
};

/** One triangle: enough for the viewport to hold a scene tagged with its
 * ingest id, which is what the solve gate compares against. */
const viewportMesh = [
  '$MeshFormat', '2.2 0 8', '$EndMeshFormat',
  '$Nodes', '3', '1 0 0 0', '2 1 0 0', '3 0 1 0', '$EndNodes',
  '$Elements', '1', '1 2 2 1 1 1 2 3', '$EndElements', '',
].join('\n');

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}

function json(body: unknown, status = 200): Response {
  // These coordinator scenarios retain their prior clock behavior by opting
  // in explicitly; default-off and missing responses are covered in pollGate.
  const response = body && typeof body === 'object'
    && 'cadFolderConfigured' in body && 'items' in body && !('coordination' in body)
    ? { ...body, coordination: 'on' }
    : body;
  return new Response(JSON.stringify(response), { status, headers: { 'Content-Type': 'application/json' } });
}

/** A Fusion document with one managed link, ready to send to or return from. */
const linkedFusionStatus: FusionCadStatus = {
  ...closedFusion,
  state: 'current', processRunning: true, running: true,
  documentName: 'Speaker', documentId: 'fusion:doc-1',
  link: {
    instanceId: 'wgi_1', bundlePath: null, designId: 'wgd_1', lineageId: null,
    editVersion: null, designHash: null, designName: null, formula: 'OSSE',
    configPresent: true, parameterCount: 3, parameterDriftCount: 0,
    localBodyState: 'unmodified', bodyFingerprintHash: null,
    documentSignatureHash: 'sha256:doc-state', documentBodyCount: 2,
    sourceStateHash: null, exportId: 'wge_1', exportSequence: '4',
  },
};

/** The verbatim refusal a real Fusion session produced (field finding F3). */
const WRAPPER_REFUSAL = "WGLink instance '393aaad4-9e78-462d-ad6c-126d411fbefd' "
  + 'has no resolvable wrapper occurrence; placement was not defaulted to identity.';

function refusedReturnOperation(operationId: string, message: string): CadOperationSummary {
  return {
    operationId,
    kind: 'request_return',
    state: 'rejected',
    stage: 'executing',
    reason: 'adapter_refused',
    message,
    jobId: null,
    attemptGeneration: 1,
    setupRevisionId: null,
    preparationId: null,
    snapshot: null,
    legacy: false,
    createdAt: '2026-09-20T10:00:00Z',
    updatedAt: '2026-09-20T10:00:01Z',
  };
}

describe('CadLinkCoordinator', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetCadReturnStore();
    resetCadPreparationStore();
    resetDesignStore();
    resetDocumentStore();
    resetSolveOptionsStore();
    resetCadOperationsStore();
    // These existing coordinator scenarios exercise explicit-on behavior.
    resetCadCoordinationForTests('on');
    workspaceModeStore.setMode('parametric');
    localStorage.removeItem('wg2.workspace.mode.v1');
    localStorage.removeItem('wg2.cad.project.v1');
    preferencesStore.resetForTests();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    importedMeshStore.clear();
    resetCadOperationsStore();
    workspaceModeStore.setMode('parametric');
    resetCadPollIntervals();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    host.remove();
  });

  const renderCoordinator = async () => {
    await act(async () => {
      root.render(<CadLinkCoordinator/>);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('skips the Fusion returns poll while Onshape is selected', async () => {
    vi.useFakeTimers();
    preferencesStore.update({ cadApplication: 'onshape' });
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path.includes('/onshape/connection')) return json({
        configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
        insecureKeyFile: false, account: { id: 'ACC', name: 'Owner' }, plan: null,
      });
      if (path.endsWith('/onshape/status')) return json({
        state: 'not_linked',
        credentials: { configured: true, credentialsPath: '/x/onshape.env', detail: null, insecureKeyFile: false },
        link: null,
        wgChangesAvailable: false,
        currentFormula: 'OSSE',
      });
      return json({}, 404);
    }));

    await renderCoordinator();
    await act(async () => { await vi.advanceTimersByTimeAsync(7_500); });

    expect(calls.some((path) => path.endsWith('/returns'))).toBe(false);
    expect(calls.some((path) => path.endsWith('/fusion-status'))).toBe(false);
    expect(calls.filter((path) => path.includes('/onshape/connection'))).toHaveLength(1);
    expect(calls.filter((path) => path.endsWith('/onshape/status'))).toHaveLength(1);
  });

  it('pauses Fusion polling while hidden and reconciles immediately when visible', async () => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({}, 404);
    }));

    await renderCoordinator();
    await act(async () => { await vi.advanceTimersByTimeAsync(7_500); });
    // Reading the CAD operations and recording the solver selection happen
    // once at start; they are not polls.
    expect(calls.filter((path) => (
      !path.startsWith('/api/cadlink/operations') && !path.endsWith('/solver-selection')
    ))).toEqual([]);

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(calls.filter((path) => path.endsWith('/returns'))).toHaveLength(1);
    expect(calls.filter((path) => path.endsWith('/fusion-status'))).toHaveLength(1);
    // Fusion's solve commands are the backend's to collect; nothing here reads them.
    expect(calls.filter((path) => path.endsWith('/solve-command'))).toEqual([]);
  });

  /** Poll cadence. The coordinator is mounted for the whole life of the app,
   * so anything it does on a timer is what an idle WG costs — and `pageIsVisible`
   * buys nothing in the packaged WebView2 window, which stays `visible` behind
   * other windows. These cover the three ways the cost is kept off the wire. */
  const cadenceHarness = (initial: { cadFolderConfigured: boolean }) => {
    const state = { listing: { ...initial, items: [] as CadReturnBundle[] } };
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path.endsWith('/returns')) return json(state.listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/api/cadlink/designs')) return json({ items: [] });
      if (path.endsWith('/api/cad-workspace/select')) {
        return json({ selected: true, path: 'C:/wgreturn-workspace' });
      }
      return json({}, 404);
    }));
    const counted = (suffix: string) => calls.filter((path) => path.endsWith(suffix)).length;
    return {
      state,
      counts: () => ({
        returns: counted('/returns'),
        fusionStatus: counted('/fusion-status'),
        solveCommand: counted('/solve-command'),
      }),
      clear: () => { calls.length = 0; },
    };
  };

  it('stops polling Fusion channels while no CAD workspace folder is configured', async () => {
    vi.useFakeTimers();
    const harness = cadenceHarness({ cadFolderConfigured: false });

    await renderCoordinator();
    // The listing that reports the folder is the one that silences the rest,
    // so each channel is read exactly once before anything is known.
    expect(harness.counts()).toEqual({ returns: 1, fusionStatus: 1, solveCommand: 0 });

    await act(async () => { await vi.advanceTimersByTimeAsync(7_500); });
    expect(harness.counts()).toEqual({ returns: 1, fusionStatus: 1, solveCommand: 0 });

    // The returns listing alone keeps a heartbeat, at the idle rate: nothing
    // tells the coordinator that Settings has chosen a folder, so this is what
    // saves the user from restarting WG after setting CAD Link up.
    harness.clear();
    harness.state.listing = { cadFolderConfigured: true, items: [] };
    // The clock stands at 7_500; the heartbeat is due at `returnsIdleMs`.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(cadPollIntervals.returnsIdleMs - 7_400);
    });
    expect(harness.counts().returns).toBe(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });
    const resumed = harness.counts();
    expect(resumed.fusionStatus).toBeGreaterThanOrEqual(1);
    expect(resumed.solveCommand).toBe(0);
  });

  it('resumes the moment a CAD workspace folder is chosen, without waiting for the heartbeat', async () => {
    vi.useFakeTimers();
    const harness = cadenceHarness({ cadFolderConfigured: false });

    await renderCoordinator();
    expect(harness.counts()).toEqual({ returns: 1, fusionStatus: 1, solveCommand: 0 });

    // Sit well past the base rates. Silence here is the point of the feature.
    await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
    expect(harness.counts()).toEqual({ returns: 1, fusionStatus: 1, solveCommand: 0 });

    // Choosing a folder through the manual path field never moves window
    // focus, so `focus` cannot cover this one -- only the selection itself
    // can. Drive the real API function rather than the store, so the wiring
    // from `selectCadWorkspace` through to the coordinator is under test.
    harness.clear();
    harness.state.listing = { cadFolderConfigured: true, items: [] };
    await act(async () => { await selectCadWorkspace('C:/wgreturn-workspace'); });

    // Immediately, not on the next idle heartbeat: every channel is read again
    // without the clock advancing at all.
    const resumed = harness.counts();
    expect(resumed.returns).toBeGreaterThanOrEqual(1);
    expect(resumed.fusionStatus).toBeGreaterThanOrEqual(1);
    expect(resumed.solveCommand).toBe(0);

    // And it holds the base rate afterwards rather than dropping back to idle.
    harness.clear();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(harness.counts()).toEqual({ returns: 2, fusionStatus: 2, solveCommand: 0 });
  });

  it('widens every poll once nothing has happened for the quiet window', async () => {
    vi.useFakeTimers();
    cadPollIntervals.quietMs = 5_000;
    const harness = cadenceHarness({ cadFolderConfigured: true });

    await renderCoordinator();
    harness.clear();
    // Base rate right up to the quiet mark: an unchanged listing and a `closed`
    // heartbeat, repeated, are the only evidence that WG is genuinely idle.
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(harness.counts()).toEqual({ returns: 2, fusionStatus: 2, solveCommand: 0 });

    // Past it, nothing is due again until the idle intervals come round.
    harness.clear();
    await act(async () => { await vi.advanceTimersByTimeAsync(9_000); });
    expect(harness.counts()).toEqual({ returns: 0, fusionStatus: 0, solveCommand: 0 });
  });

  it('snaps back to the base rate the moment CAD work resumes', async () => {
    vi.useFakeTimers();
    cadPollIntervals.quietMs = 5_000;
    const harness = cadenceHarness({ cadFolderConfigured: true });

    await renderCoordinator();
    await act(async () => { await vi.advanceTimersByTimeAsync(14_000); });
    harness.clear();

    // Regaining focus reconciles both at once rather than leaving the
    // user to wait out an interval chosen while they were in another window.
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(harness.counts()).toEqual({ returns: 1, fusionStatus: 1, solveCommand: 0 });

    harness.clear();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });
    expect(harness.counts()).toEqual({ returns: 1, fusionStatus: 1, solveCommand: 0 });

    // Entering the CAD workspace holds the base rate for as long as it is open,
    // however long the user then spends reading the panel.
    await act(async () => { workspaceModeStore.setMode('cad'); await Promise.resolve(); });
    harness.clear();
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(harness.counts().solveCommand).toBe(0);
    expect(harness.counts().returns).toBe(8);
  });

  it('fails closed on repeated links and posts the chosen Fusion instance on refresh', async () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_shared', lineageId: 'wgl_shared', baseEditVersion: 1,
    }, 'current');
    const link = (instanceId: string) => ({
      instanceId, bundlePath: null, designId: 'wgd_shared', lineageId: 'wgl_shared',
      editVersion: '1', designHash: 'sha256:design', designName: `Copy ${instanceId}`,
      formula: 'OSSE', configPresent: true, parameterCount: 3, parameterDriftCount: 0,
      localBodyState: 'unmodified' as const, bodyFingerprintHash: `sha256:body-${instanceId}`,
      documentSignatureHash: 'sha256:document', documentBodyCount: 2,
      sourceStateHash: 'sha256:sources', exportId: 'wge_shared', exportSequence: '1',
    });
    const requests: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) {
        const request = JSON.parse(String(init?.body)) as Record<string, unknown>;
        requests.push(request);
        const selected = request.instanceId === 'instance-b';
        return json({
          ...closedFusion,
          state: selected ? 'current' : 'instance_selection_required',
          processRunning: true,
          running: true,
          documentName: 'Repeated design',
          documentId: 'fusion:doc-repeated',
          link: selected ? link('instance-b') : null,
          matchingLinks: [link('instance-a'), link('instance-b')],
          selectedInstanceId: selected ? 'instance-b' : null,
        });
      }
      return json({}, 404);
    }));

    await renderCoordinator();
    expect(cadLinkCoordinatorBridge.getSnapshot().fusionStatus?.state)
      .toBe('instance_selection_required');

    let refusal: unknown;
    await act(async () => {
      refusal = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion()
        .catch((reason: unknown) => reason);
    });
    expect(refusal).toBeInstanceOf(Error);
    expect((refusal as Error).message).toContain('Choose which linked Fusion instance');

    await act(async () => {
      cadLinkCoordinatorBridge.getSnapshot().selectFusionInstance('instance-b');
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(requests.at(-1)?.instanceId).toBe('instance-b');
    expect(cadLinkCoordinatorBridge.getSnapshot().fusionStatus?.link?.instanceId)
      .toBe('instance-b');
  });

  it('posts the chosen Onshape link identity on every status refresh', async () => {
    preferencesStore.update({ cadApplication: 'onshape' });
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_shared', lineageId: 'wgl_shared', baseEditVersion: 1,
    }, 'current');
    const link = (instanceId: string) => ({
      instanceId,
      designId: 'wgd_shared', accountId: 'ACC', documentId: `DID-${instanceId}`,
      workspaceId: 'WID', documentName: `Copy ${instanceId}`, documentUrl: null,
      isPublic: false, partStudioElementId: `PART-${instanceId}`,
      variableStudioElementId: null, featureStudioElementId: null,
      nativeFeatureId: null, datumFeatureStudioElementId: null, datumFeatureId: null,
      buildMode: 'import' as const, lastSequence: 1, updatedAt: '2026-08-20T12:00:00Z',
    });
    const requests: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.includes('/onshape/connection')) return json({
        configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
        insecureKeyFile: false, account: { id: 'ACC', name: 'Owner' }, plan: null,
      });
      if (path.endsWith('/onshape/status')) {
        const request = JSON.parse(String(init?.body)) as Record<string, unknown>;
        requests.push(request);
        const selected = request.instanceId === 'wgo_b';
        return json({
          state: selected ? 'current' : 'instance_selection_required',
          credentials: { configured: true, credentialsPath: '/x/onshape.env', detail: null, insecureKeyFile: false },
          link: selected ? link('wgo_b') : null,
          matchingLinks: [link('wgo_a'), link('wgo_b')],
          selectedInstanceId: selected ? 'wgo_b' : null,
          wgChangesAvailable: false,
          currentFormula: 'osse',
        });
      }
      return json({}, 404);
    }));

    await renderCoordinator();
    expect(cadLinkCoordinatorBridge.getSnapshot().onshapeStatus?.state)
      .toBe('instance_selection_required');

    await act(async () => {
      cadLinkCoordinatorBridge.getSnapshot().selectOnshapeInstance('wgo_b');
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(requests.at(-1)?.instanceId).toBe('wgo_b');
    expect(cadLinkCoordinatorBridge.getSnapshot().onshapeStatus?.selectedInstanceId)
      .toBe('wgo_b');
  });

  it('enters CAD mode when an Onshape return lands so the panel can own it', async () => {
    preferencesStore.update({ cadApplication: 'onshape' });
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 1,
    }, 'current');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('/onshape/connection')) return json({
        configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
        insecureKeyFile: false, account: { id: 'ACC', name: 'Owner' }, plan: null,
      });
      if (path.endsWith('/onshape/status')) return json({
        state: 'current',
        credentials: { configured: true, credentialsPath: '/x/onshape.env', detail: null, insecureKeyFile: false },
        link: null,
        wgChangesAvailable: false,
        currentFormula: 'OSSE',
      });
      if (path.endsWith('/onshape/return')) return json({
        translationId: 'tr_1',
        bundle: { name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', documentName: 'Speaker', sourceCount: 1, instanceCount: 1 },
        ingest: ingestRecord,
      });
      return json({}, 404);
    }));
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    await renderCoordinator();

    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().returnFromOnshape(); });

    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(ingestRecord.ingest_id);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).toHaveBeenCalledWith('cadlink');
  });

  it('rebuilds an Onshape return locally, with no WGLink folder and no second translation', async () => {
    // The panel shows the mesh-size fields and Force full domain for an
    // Onshape import, and changing one marks the record stale -- so the
    // rebuild has to reach the ingest endpoint or the import cannot be
    // solved at all. The return leg publishes under WG's own data directory,
    // so the request states the bundle's origin and a name relative to it;
    // this setup has no WGLink folder to be relative to.
    preferencesStore.update({ cadApplication: 'onshape' });
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 1,
    }, 'current');
    const ingestBodies: Record<string, unknown>[] = [];
    const paths: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      paths.push(path);
      if (path.includes('/onshape/connection')) return json({
        configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
        insecureKeyFile: false, account: { id: 'ACC', name: 'Owner' }, plan: null,
      });
      if (path.endsWith('/onshape/status')) return json({
        state: 'current',
        credentials: { configured: true, credentialsPath: '/x/onshape.env', detail: null, insecureKeyFile: false },
        link: null,
        wgChangesAvailable: false,
        currentFormula: 'OSSE',
      });
      if (path.endsWith('/onshape/return')) return json({
        translationId: 'tr_1',
        bundle: {
          name: 'wgr_demo.wgreturn',
          bundlePath: 'wgr_demo.wgreturn',
          bundleOrigin: 'onshape',
          documentName: 'Speaker',
          sourceCount: 1,
          instanceCount: 1,
        },
        ingest: ingestRecord,
      });
      if (path.endsWith('/cadlink/ingest')) {
        ingestBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return json({ ...ingestRecord, ingest_id: 'wgi_rebuilt' });
      }
      // No returns listing exists to be relative to: an Onshape-only setup
      // has never selected a WGLink folder.
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: false, items: [] });
      return json({}, 404);
    }));
    vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    await renderCoordinator();
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().returnFromOnshape(); });

    act(() => {
      useCadReturnStore.getState().setRigidSize(9.5);
      useCadReturnStore.getState().setSourceSize('source-hf', 2.5);
      useCadPreparationStore.getState().setSymmetryMode('full');
      useCadReturnStore.getState().markIngestStale('The CAD symmetry preparation mode changed.');
    });
    expect(useCadReturnStore.getState().needsIngest).toBe(true);
    const translations = paths.filter((path) => path.endsWith('/onshape/return')).length;

    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().ingest(); });

    expect(ingestBodies).toHaveLength(1);
    expect(ingestBodies[0].bundlePath).toBe('wgr_demo.wgreturn');
    expect(ingestBodies[0].bundleOrigin).toBe('onshape');
    expect(ingestBodies[0].mesh).toMatchObject({ rigidSizeMm: 9.5, sourceSizeMm: { 'source-hf': 2.5 } });
    expect(ingestBodies[0].symmetryMode).toBe('full');
    const state = useCadReturnStore.getState();
    expect(state.ingestRecord?.ingest_id).toBe('wgi_rebuilt');
    expect(state.needsIngest).toBe(false);
    expect(state.ingestStaleReason).toBeNull();
    // The bundle Onshape already translated was re-prepared where it lay.
    expect(paths.filter((path) => path.endsWith('/onshape/return'))).toHaveLength(translations);
  });

  it('rebuilds a workspace return against the WGLink folder, not the Onshape area', async () => {
    const ingestBodies: Record<string, unknown>[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/cadlink/ingest')) {
        ingestBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return json(ingestRecord);
      }
      return json({}, 404);
    }));
    await renderCoordinator();
    act(() => { useCadReturnStore.getState().selectBundle(initialBundle); });

    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().ingest(); });

    expect(ingestBodies.at(-1)?.bundlePath).toBe('wgreturn/speaker.wgreturn');
    expect(ingestBodies.at(-1)?.bundleOrigin).toBe('wglink');
  });

  it.each(['design', 'instance'])('discards an Onshape return superseded by a newer %s', async (target) => {
    preferencesStore.update({ cadApplication: 'onshape' });
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 1,
    }, 'current');
    const pending = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('/onshape/connection')) return json({
        configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
        insecureKeyFile: false, account: { id: 'ACC', name: 'Owner' }, plan: null,
      });
      if (path.endsWith('/onshape/status')) return json({
        state: 'current',
        credentials: { configured: true, credentialsPath: '/x/onshape.env', detail: null, insecureKeyFile: false },
        link: null,
        wgChangesAvailable: false,
        currentFormula: 'OSSE',
      });
      if (path.endsWith('/onshape/return')) return pending.promise;
      return json({}, 404);
    }));
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    await renderCoordinator();

    let operation!: Promise<void>;
    await act(async () => { operation = cadLinkCoordinatorBridge.getSnapshot().returnFromOnshape(); });
    await act(async () => {
      if (target === 'design') {
        useDesignStore.getState().replaceDesign(designForFamily('OSSE'));
        useDocumentStore.getState().setCadLink({ designId: 'design-B', lineageId: 'project-B', baseEditVersion: 1 }, 'current');
      } else {
        cadLinkCoordinatorBridge.getSnapshot().selectOnshapeInstance('instance-B');
      }
    });
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    await act(async () => { pending.resolve(json({
        translationId: 'tr_1',
        bundle: { name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', documentName: 'Speaker', sourceCount: 1, instanceCount: 1 },
        ingest: ingestRecord,
      })); await operation; });
    expect(useDocumentStore.getState().identity?.designId).toBe(target === 'design'
      ? 'design-B' : 'wgd_01K00000000000000000000000');
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();

    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(activate).not.toHaveBeenCalledWith('cadlink');
  });

  it('resolves a pull with the exact correlated arrival and times the wait out', async () => {
    const linkedFusion: FusionCadStatus = {
      ...closedFusion,
      state: 'current',
      processRunning: true,
      running: true,
      documentName: 'Speaker',
      documentId: 'fusion:doc-1',
      link: {
        instanceId: 'wgi_1', bundlePath: null, designId: 'wgd_1', lineageId: null,
        editVersion: null, designHash: null, designName: null, formula: 'OSSE',
        configPresent: true, parameterCount: 3, parameterDriftCount: 0,
        localBodyState: 'unmodified', bodyFingerprintHash: null,
        documentSignatureHash: 'sha256:doc-state', documentBodyCount: 2,
        sourceStateHash: null, exportId: 'wge_1', exportSequence: '4',
      },
    };
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_1', lineageId: 'wgl_1', baseEditVersion: 1,
    }, 'current');
    let listing: { cadFolderConfigured: boolean; items: CadReturnBundle[] } = { cadFolderConfigured: true, items: [] };
    let pullRequests = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(linkedFusion);
      if (path.endsWith('/request-fusion-return')) {
        pullRequests += 1;
        return json({ status: 'requested', requestId: 'req_1', documentName: 'Speaker' });
      }
      return json({}, 404);
    }));
    await renderCoordinator();

    let pull!: Promise<CadReturnBundle>;
    let duplicate!: Promise<CadReturnBundle>;
    await act(async () => {
      pull = cadLinkCoordinatorBridge.getSnapshot().pullFromFusion();
      duplicate = cadLinkCoordinatorBridge.getSnapshot().pullFromFusion();
      await Promise.resolve(); await Promise.resolve();
    });
    expect(duplicate).toBe(pull);
    expect(pullRequests).toBe(1);
    expect(cadLinkCoordinatorBridge.getSnapshot().pullingFromFusion).toBe(true);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('Waiting for Fusion…');

    // An uncorrelated bundle must not settle this pull.
    listing = { cadFolderConfigured: true, items: [{ ...initialBundle, requestId: 'req_other' }] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
    });

    const correlated = { ...initialBundle, requestId: 'req_1', documentName: 'Speaker pulled' };
    listing = { cadFolderConfigured: true, items: [correlated] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
    });
    await act(async () => { await expect(pull).resolves.toMatchObject({ requestId: 'req_1' }); });
    expect(cadLinkCoordinatorBridge.getSnapshot().pullingFromFusion).toBe(false);
  });

  it('rejects and reports a pull that Fusion never answers', async () => {
    const linkedFusion: FusionCadStatus = {
      ...closedFusion,
      state: 'current', processRunning: true, running: true,
      documentName: 'Speaker', documentId: 'fusion:doc-1',
      link: {
        instanceId: 'wgi_1', bundlePath: null, designId: 'wgd_1', lineageId: null,
        editVersion: null, designHash: null, designName: null, formula: 'OSSE',
        configPresent: true, parameterCount: 3, parameterDriftCount: 0,
        localBodyState: 'unmodified', bodyFingerprintHash: null,
        documentSignatureHash: 'sha256:doc-state', documentBodyCount: 2,
        sourceStateHash: null, exportId: 'wge_1', exportSequence: '4',
      },
    };
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_1', lineageId: 'wgl_1', baseEditVersion: 1,
    }, 'current');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json(linkedFusion);
      if (path.endsWith('/request-fusion-return')) {
        return json({ status: 'requested', requestId: 'req_1', documentName: 'Speaker' });
      }
      return json({}, 404);
    }));
    await renderCoordinator();

    const started = Date.now();
    const clock = vi.spyOn(Date, 'now');
    // The handler is attached with the promise, not after the act() below:
    // a late handler makes the rejection look unhandled to the runner.
    let rejection: unknown;
    await act(async () => {
      cadLinkCoordinatorBridge.getSnapshot().pullFromFusion()
        .then(() => undefined, (reason) => { rejection = reason; });
      await Promise.resolve(); await Promise.resolve();
    });
    clock.mockReturnValue(started + 61_000);
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });
    expect(String(rejection)).toContain('did not return the requested model within 60 seconds');
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toContain('within 60 seconds');
  });

  it('asks for a CAD Link folder when none is chosen, and then finishes the send', async () => {
    // Refusing to guess a folder is deliberate (the add-in's `workspace_root`
    // says why). Refusing silently is the defect: a first-time user pressed
    // Send and nothing happened at all. The folder is still never invented --
    // the user chooses it -- but the operation they asked for continues.
    let folder: string | null = null;
    const calls: Array<{ path: string; method: string; body: unknown }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push({ path, method: init?.method ?? 'GET', body: init?.body ?? null });
      if (path === '/api/cad-workspace/path') return json({ selected: folder !== null, path: folder });
      if (path === '/api/cad-workspace/select') {
        folder = '/chosen/cadlink';
        return json({ selected: true, path: folder });
      }
      if (path === '/api/export/wglink') return json({
        bundlePath: '/chosen/cadlink/wglink/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
        sequence: 1, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
      });
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: folder !== null, items: [] });
      if (path.endsWith('/fusion-status')) return json({
        ...closedFusion, cadFolderConfigured: folder !== null, cadFolderPath: folder,
      });
      return json({}, 404);
    }));
    await renderCoordinator();

    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion(); });

    const picker = calls.filter((call) => call.path === '/api/cad-workspace/select');
    expect(picker).toHaveLength(1);
    // No body: that is what makes the server open the native folder picker
    // rather than accept a path WG chose on the user's behalf.
    expect(picker[0].method).toBe('POST');
    expect(picker[0].body).toBeNull();
    // And the send the user asked for happened, after the folder existed.
    const order = calls.map((call) => call.path);
    expect(order.indexOf('/api/cad-workspace/select'))
      .toBeLessThan(order.indexOf('/api/export/wglink'));
    expect(calls.filter((call) => call.path === '/api/export/wglink')).toHaveLength(1);
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toBeNull();
  });

  it('runs the send guards against the link the folder picker revealed, not the one before it', async () => {
    // The exchange folder is read from the filesystem and the link is not
    // (server/workspace/api.py answers `None` for a folder that is not a
    // directory right now, while the link and heartbeat come from the data
    // directory). So an established link with Fusion-side changes can be
    // reported alongside `cadFolderConfigured: false` -- a renamed, unmounted
    // or not-yet-synced folder is enough. Choosing the folder changes the
    // world the guards are about, so every guard has to see the status read
    // after the pick. Deciding from the pre-pick status made the two-way
    // conflict guard unreachable (`not-configured` has a null `action`) and
    // sent an unbound create export over a live link, reporting success.
    let folder: string | null = null;
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/cad-workspace/select') {
        folder = '/chosen/cadlink';
        return json({ selected: true, path: folder });
      }
      // Present, so that a send which does get this far succeeds rather than
      // failing on the mock: the defect this covers reported success.
      if (path === '/api/cad-workspace/path') return json({ selected: folder !== null, path: folder });
      if (path === '/api/export/wglink') return json({
        bundlePath: '/chosen/cadlink/wglink/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
        sequence: 1, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
      });
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: folder !== null, items: [] });
      if (path.endsWith('/fusion-status')) return json({
        ...linkedFusionStatus,
        state: 'stale', wgChangesAvailable: true, fusionChangesAvailable: true,
        cadFolderConfigured: folder !== null, cadFolderPath: folder,
      });
      return json({}, 404);
    }));
    await renderCoordinator();
    expect(cadLinkCoordinatorBridge.getSnapshot().fusionStatus?.cadFolderConfigured).toBe(false);

    let outcome: unknown = 'not settled';
    await act(async () => {
      outcome = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion();
    });

    expect(calls).toContain('/api/cad-workspace/select');
    // Parked on the two-way conflict dialog, which is what `null` means.
    expect(outcome).toBeNull();
    expect(cadLinkCoordinatorBridge.getSnapshot().pendingFusionConflict).toBe(true);
    // Nothing was exported, so nothing overwrote the link with a create send.
    expect(calls).not.toContain('/api/export/wglink');
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toBeNull();
  });

  it('will not send when the status cannot be read again after the folder is chosen', async () => {
    // Without a post-pick status there is nothing to derive open-vs-update
    // from, and the shape of a missing status -- `fusionWorkflowView(null)`
    // answers `open` -- is exactly the unbound create send this must not make.
    let folder: string | null = null;
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/cad-workspace/select') {
        folder = '/chosen/cadlink';
        return json({ selected: true, path: folder });
      }
      if (path === '/api/cad-workspace/path') return json({ selected: folder !== null, path: folder });
      if (path === '/api/export/wglink') return json({
        bundlePath: '/chosen/cadlink/wglink/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
        sequence: 1, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
      });
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: folder !== null, items: [] });
      if (path.endsWith('/fusion-status')) {
        return folder === null
          ? json({ ...linkedFusionStatus, cadFolderConfigured: false, cadFolderPath: null })
          : json({ detail: 'fusion status unavailable' }, 503);
      }
      return json({}, 404);
    }));
    await renderCoordinator();

    let outcome: unknown = 'not settled';
    await act(async () => {
      outcome = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion()
        .then((value) => ({ resolved: value }), (reason: unknown) => ({ rejected: String(reason) }));
    });

    expect(calls).toContain('/api/cad-workspace/select');
    expect(calls).not.toContain('/api/export/wglink');
    // A rejection, never `null`: `null` means parked on the conflict dialog.
    expect(outcome).toMatchObject({ rejected: expect.stringContaining('could not check') });
    expect(cadLinkCoordinatorBridge.getSnapshot().error ?? '').toContain('could not check');
  });

  it('refuses to send while it has never read the Fusion status', async () => {
    // The null status is not a state with an answer in it: `fusionWorkflowView`
    // reads it as "Checking Fusion 360…" and hands back `action: 'open'`, which
    // is a create send with no expected document, instance or return-state
    // hash -- over whatever link the document actually has. The Send control
    // is live throughout (ParamPanel renders on any truthy `action`), and a
    // heartbeat that will not answer is reached by a 503, by Fusion being
    // unreachable, or by the blanking window the next test covers.
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/workspace' });
      if (path === '/api/export/wglink') return json({
        bundlePath: '/workspace/wglink/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
        sequence: 1, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
      });
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json({ detail: 'heartbeat unreadable' }, 503);
      return json({}, 404);
    }));
    await renderCoordinator();
    expect(cadLinkCoordinatorBridge.getSnapshot().fusionStatus).toBeNull();

    let outcome: unknown = 'not settled';
    await act(async () => {
      outcome = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion()
        .then((value) => ({ resolved: value }), (reason: unknown) => ({ rejected: String(reason) }));
    });

    expect(calls).not.toContain('/api/export/wglink');
    expect(outcome).toMatchObject({ rejected: expect.stringContaining('could not check') });
    expect(cadLinkCoordinatorBridge.getSnapshot().error ?? '').toContain('could not check');
    // And it never claims a send it did not make.
    expect(cadLinkCoordinatorBridge.getSnapshot().status ?? '').not.toContain('Opening in Fusion');
  });

  it('reads the status again on Send when the only heartbeat there was failed', async () => {
    // The heartbeat poll is suspended outright while no exchange folder is
    // configured -- the add-in writes its heartbeat into that folder, so there
    // is nothing to read -- which makes the read at mount the only one there
    // will ever be. If that one fails, refusing and saying "try again in a
    // moment" promises a recovery nothing delivers: Send never re-read, and
    // the folder picker was never offered. So Send itself is the retry.
    vi.useFakeTimers();
    let statusCalls = 0;
    let folder: string | null = null;
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/cad-workspace/select') {
        folder = '/chosen/cadlink';
        return json({ selected: true, path: folder });
      }
      if (path === '/api/cad-workspace/path') return json({ selected: folder !== null, path: folder });
      if (path === '/api/export/wglink') return json({
        bundlePath: '/chosen/cadlink/wglink/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
        sequence: 9, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
      });
      // No folder configured: this is what suspends the heartbeat poll.
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: folder !== null, items: [] });
      if (path.endsWith('/fusion-status')) {
        statusCalls += 1;
        // The one read at mount fails; the server is fine immediately after.
        return statusCalls === 1
          ? json({ detail: 'heartbeat unreadable' }, 503)
          : json({
            ...linkedFusionStatus, state: 'stale', wgChangesAvailable: true,
            fusionChangesAvailable: false, cadFolderConfigured: folder !== null,
          });
      }
      return json({}, 404);
    }));
    await renderCoordinator();
    expect(statusCalls).toBe(1);
    expect(cadLinkCoordinatorBridge.getSnapshot().fusionStatus).toBeNull();

    // Two minutes of nothing: the poll really is suspended, so waiting is not
    // the recovery. This is the condition the fix has to work under, not a
    // behaviour to change -- the heartbeat has nothing to read until a folder
    // is chosen.
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(statusCalls).toBe(1);
    expect(cadLinkCoordinatorBridge.getSnapshot().fusionStatus).toBeNull();

    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion(); });

    // Send re-read, found no folder, offered the picker, and carried on.
    expect(statusCalls).toBeGreaterThan(1);
    expect(calls).toContain('/api/cad-workspace/select');
    expect(calls).toContain('/api/export/wglink');
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toBeNull();
  });

  it('asks for the status once on Send, and refuses when that also fails', async () => {
    // The retry is bounded the way the folder pick is: one attempt, then a
    // refusal. An unbounded one spins against a server that is down -- and it
    // spins inside the microtask queue, so it starves the event loop rather
    // than tripping a timeout. That is why this mock recovers on the fourth
    // call: an unbounded retry then terminates and is caught by the count and
    // the export below, instead of hanging the suite.
    let statusCalls = 0;
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/workspace' });
      if (path === '/api/export/wglink') return json({
        bundlePath: '/workspace/wglink/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
        sequence: 1, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
      });
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) {
        statusCalls += 1;
        return statusCalls >= 4
          ? json({
            ...linkedFusionStatus, state: 'stale', wgChangesAvailable: true,
            fusionChangesAvailable: false,
          })
          : json({ detail: 'heartbeat unreadable' }, 503);
      }
      return json({}, 404);
    }));
    await renderCoordinator();
    const atMount = statusCalls;

    let outcome: unknown = 'not settled';
    await act(async () => {
      outcome = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion()
        .then((value) => ({ resolved: value }), (reason: unknown) => ({ rejected: String(reason) }));
    });

    expect(statusCalls - atMount).toBe(1);
    expect(calls).not.toContain('/api/export/wglink');
    expect(calls).not.toContain('/api/cad-workspace/select');
    expect(outcome).toMatchObject({ rejected: expect.stringContaining('could not check') });
  });

  it('binds the update to the link once the status is readable', async () => {
    // The control for the two refusals around it: the same design, the same
    // mock, and a heartbeat that answers. If this ever stops binding all three
    // expectations, the refusals above stop meaning anything.
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_1', lineageId: 'wgl_1', baseEditVersion: 1,
    }, 'current');
    let exported: Record<string, unknown> | null = null;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/workspace' });
      if (path === '/api/export/wglink') {
        exported = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
        return json({
          bundlePath: '/workspace/wglink/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
          sequence: 5, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
        });
      }
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json({
        ...linkedFusionStatus, state: 'stale', wgChangesAvailable: true, fusionChangesAvailable: false,
      });
      return json({}, 404);
    }));
    await renderCoordinator();

    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion(); });

    expect(exported).toMatchObject({
      expectedFusionDocumentId: 'fusion:doc-1',
      expectedFusionInstanceId: 'wgi_1',
      expectedFusionReturnStateHash: 'sha256:doc-state',
    });
    expect(cadLinkCoordinatorBridge.getSnapshot().status ?? '').toContain('Update sent to Fusion 360');
  });

  it('refuses to send in the window where a parameter edit has blanked the status', async () => {
    // Editing a parameter blanks `fusionStatus` and starts a fresh read, so
    // there is a real window -- up to the next poll -- in which the status is
    // null while the Send control is on screen and the link is live. Pressing
    // Send there used to export a create with nothing bound to the link.
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_1', lineageId: 'wgl_1', baseEditVersion: 1,
    }, 'current');
    const calls: string[] = [];
    const statusAnswer = () => json({
      ...linkedFusionStatus, state: 'stale', wgChangesAvailable: true, fusionChangesAvailable: false,
    });
    // One deferred per call: two fetches must not share a Response, whose body
    // can only be read once.
    const held: Array<(value: Response) => void> = [];
    let blockStatus = false;
    let exportBody: Record<string, unknown> | null = null;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/workspace' });
      if (path === '/api/export/wglink') {
        exportBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
        return json({
          bundlePath: '/workspace/wglink/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
          sequence: 1, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
        });
      }
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) {
        // The read the edit started has not come back yet; that is the window.
        if (!blockStatus) return statusAnswer();
        return new Promise<Response>((resolve) => { held.push(resolve); });
      }
      return json({}, 404);
    }));
    await renderCoordinator();
    expect(cadLinkCoordinatorBridge.getSnapshot().fusionStatus).not.toBeNull();

    blockStatus = true;
    await act(async () => { useDesignStore.getState().updateField('R', 321); });
    expect(cadLinkCoordinatorBridge.getSnapshot().fusionStatus).toBeNull();

    let settled: unknown = 'pending';
    await act(async () => {
      cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion()
        .then((value) => { settled = { resolved: value }; },
          (reason: unknown) => { settled = { rejected: String(reason) }; });
      await Promise.resolve(); await Promise.resolve();
    });

    // Nothing has gone out while the status is unknown: the send is waiting on
    // the read it asked for, not deciding without one.
    expect(calls).not.toContain('/api/export/wglink');
    expect(settled).toBe('pending');

    await act(async () => {
      held.forEach((resolve) => resolve(statusAnswer()));
      await Promise.resolve(); await Promise.resolve();
      await Promise.resolve(); await Promise.resolve();
    });

    // And when the status arrives the send is bound to the link, never the
    // unbound create the null status used to produce.
    expect(exportBody).toMatchObject({
      expectedFusionDocumentId: 'fusion:doc-1',
      expectedFusionInstanceId: 'wgi_1',
      expectedFusionReturnStateHash: 'sha256:doc-state',
    });
    expect(settled).toMatchObject({ resolved: expect.objectContaining({ exportId: 'wge_1' }) });
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toBeNull();
  });

  it('asks for the exchange folder once, and refuses rather than reopening the picker', async () => {
    // The re-entry is bounded to one recursion: the second pass may not ask
    // again. Without the bound a folder WG still cannot use reopens the native
    // picker on top of itself, and the refusal that says so is unreachable.
    let picks = 0;
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/cad-workspace/select') {
        picks += 1;
        // Chosen the first time, dismissed the second: so an unbounded
        // re-entry terminates and is told apart by its message, instead of
        // spinning until the test times out.
        return json(picks === 1 ? { selected: true, path: '/chosen/cadlink' } : { selected: false, path: null });
      }
      if (path === '/api/cad-workspace/path') return json({ selected: false, path: null });
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: false, items: [] });
      // Still unusable after the pick: the folder is read from the filesystem
      // every time, so choosing one WG cannot use changes nothing.
      if (path.endsWith('/fusion-status')) return json({ ...linkedFusionStatus, cadFolderConfigured: false });
      return json({}, 404);
    }));
    await renderCoordinator();

    let outcome: unknown = 'not settled';
    await act(async () => {
      outcome = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion()
        .then((value) => ({ resolved: value }), (reason: unknown) => ({ rejected: String(reason) }));
    });

    expect(picks).toBe(1);
    expect(outcome).toMatchObject({ rejected: expect.stringContaining('still has no shared folder') });
    expect(cadLinkCoordinatorBridge.getSnapshot().error ?? '').toContain('still has no shared folder');
    expect(calls).not.toContain('/api/export/wglink');
  });

  /** Drive a send that parks in the folder picker, so the world can be changed
   * underneath it. The picker is a native dialog the server opens, so it is
   * held open for as long as the user takes -- seconds at least. */
  const sendAcrossThePicker = async (options: {
    duringPicker?: () => void;
    holdFirstStatusRead?: boolean;
  } = {}) => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_1', lineageId: 'wgl_1', baseEditVersion: 1,
    }, 'current');
    let folder: string | null = null;
    const picker = deferred<Response>();
    // Every status read taken while the hold is on, not just the first: the
    // folder selection itself wakes the coordinator into a read, so "the first
    // read after the pick" is not reliably the one the send is waiting on.
    let holdingStatus = options.holdFirstStatusRead === true;
    const heldStatusReads: Array<{ resolve: (value: Response) => void }> = [];
    const statusBodies: Array<Record<string, unknown>> = [];
    const calls: string[] = [];
    let exportBody: Record<string, unknown> | null = null;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/cad-workspace/select') return picker.promise;
      if (path === '/api/cad-workspace/path') return json({ selected: folder !== null, path: folder });
      if (path === '/api/export/wglink') {
        exportBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
        return json({
          bundlePath: '/chosen/cadlink/wglink/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
          sequence: 1, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
        });
      }
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: folder !== null, items: [] });
      if (path.endsWith('/fusion-status')) {
        statusBodies.push(JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>);
        if (holdingStatus && folder !== null) {
          const held = deferred<Response>();
          heldStatusReads.push(held);
          return held.promise;
        }
        return json({
          ...linkedFusionStatus, state: 'stale', wgChangesAvailable: true,
          cadFolderConfigured: folder !== null,
        });
      }
      return json({}, 404);
    }));
    await renderCoordinator();

    let settled: unknown = 'pending';
    const sends: Array<Promise<unknown>> = [];
    await act(async () => {
      const first = cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion();
      sends.push(first);
      first.then((value) => { settled = { resolved: value }; },
        (reason: unknown) => { settled = { rejected: String(reason) }; });
      await Promise.resolve(); await Promise.resolve();
    });

    options.duringPicker?.();

    folder = '/chosen/cadlink';
    await act(async () => {
      picker.resolve(json({ selected: true, path: folder }));
      await Promise.resolve(); await Promise.resolve();
      await Promise.resolve(); await Promise.resolve();
    });

    return {
      calls, sends, statusBodies,
      settled: () => settled,
      exportBody: () => exportBody,
      heldStatusReads,
      releaseHeldStatus: async (bump: () => void) => {
        // Let go of the hold first, so the read `bump` starts answers at once
        // and becomes the newest one; the reads held before it are then
        // released into a world that has already moved past them.
        holdingStatus = false;
        bump();
        await act(async () => {
          heldStatusReads.forEach((held) => held.resolve(json({
            ...linkedFusionStatus, state: 'stale', wgChangesAvailable: true, cadFolderConfigured: true,
          })));
          await Promise.resolve(); await Promise.resolve();
          await Promise.resolve(); await Promise.resolve();
        });
      },
    };
  };

  it('exports the design the guards were computed from, not the one captured before the picker', async () => {
    // The native folder picker is open for as long as the user takes, and WG
    // stays live behind it. The status the guards re-run against is read after
    // it closes, from the design as it is then; the export used to carry the
    // design as it was when Send was pressed. So the guards could clear a
    // revision the bundle did not contain -- the same defect as deciding from
    // a pre-picker status, one await further along.
    const run = await sendAcrossThePicker({
      duringPicker: () => { act(() => { useDesignStore.getState().updateField('R', 321); }); },
    });

    const statusRead = run.statusBodies[run.statusBodies.length - 1];
    expect((statusRead?.design as Record<string, unknown>).R).toBe(321);
    expect((run.exportBody()?.design as Record<string, unknown>).R).toBe(321);
    expect(run.exportBody()?.designRevision).toBe(useDesignStore.getState().designRevision);
  });

  it('control: with no edit across the picker, the export carries that same design', async () => {
    const run = await sendAcrossThePicker();
    const statusRead = run.statusBodies[run.statusBodies.length - 1];
    expect((statusRead?.design as Record<string, unknown>).R)
      .toBe((run.exportBody()?.design as Record<string, unknown>).R);
    expect(run.exportBody()?.designRevision).toBe(useDesignStore.getState().designRevision);
  });

  it('refuses rather than deciding from a status another read has already replaced', async () => {
    // The read the send waited for came back after a newer one, so it was
    // never published: the panel is showing the newer status and the send
    // would be deciding from the older one. That is the same disagreement
    // between what was decided and what is true as the two defects above.
    const run = await sendAcrossThePicker({ holdFirstStatusRead: true });
    // The send is parked on a status read that has not answered yet.
    expect(run.heldStatusReads.length).toBeGreaterThan(0);
    expect(run.calls).not.toContain('/api/export/wglink');
    await run.releaseHeldStatus(() => {
      // A parameter edit starts a fresh status read, which supersedes the
      // one the send is still waiting on.
      act(() => { useDesignStore.getState().updateField('R', 321); });
    });

    expect(run.calls).not.toContain('/api/export/wglink');
    expect(run.settled()).toMatchObject({ rejected: expect.stringContaining('could not check') });
  });

  it('opens one folder dialog across rapid presses, not one on top of another', async () => {
    // Two presses used to reach `selectCadWorkspace` twice and stack two
    // native folder dialogs, because `sendingToFusion` is only set inside the
    // export -- past the picker -- so it disables nothing while the dialog is
    // open. Only the pick is shared: overlapping sends stay supported, fenced
    // by the send request number, which the newest-send test pins.
    let second!: Promise<unknown>;
    const run = await sendAcrossThePicker({
      duringPicker: () => {
        act(() => {
          second = cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion()
            .catch(() => undefined);
        });
      },
    });
    expect(run.calls.filter((path) => path === '/api/cad-workspace/select')).toHaveLength(1);
    // Both presses continue from the folder chosen in that one dialog.
    await act(async () => { await Promise.all([run.sends[0], second]); });
    expect(run.settled()).toMatchObject({ resolved: expect.objectContaining({ exportId: 'wge_1' }) });
  });

  it('says plainly what is needed when the folder picker is dismissed', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/cad-workspace/path') return json({ selected: false, path: null });
      // A dismissed picker returns the selection as it was: still none.
      if (path === '/api/cad-workspace/select') return json({ selected: false, path: null });
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: false, items: [] });
      if (path.endsWith('/fusion-status')) return json({ ...closedFusion, cadFolderConfigured: false, cadFolderPath: null });
      return json({}, 404);
    }));
    await renderCoordinator();

    let outcome: unknown = 'not settled';
    await act(async () => {
      outcome = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion()
        .then((value) => ({ resolved: value }), (reason: unknown) => ({ rejected: String(reason) }));
    });

    expect(calls).toContain('/api/cad-workspace/select');
    // The send is abandoned, not attempted: WG does not go on to ask for an
    // export destination it has just been told there is none of.
    expect(calls).not.toContain('/api/cad-workspace/path');
    expect(calls).not.toContain('/api/export/wglink');
    // A rejection, never a `null` answer: `null` already means "parked on the
    // two-way conflict dialog", and the File menu says exactly that when it
    // sees one. The sentence names the dialog the user just dismissed, which
    // is what the old "set it in Settings" refusal did not.
    expect(outcome).toMatchObject({ rejected: expect.stringContaining('Choose the folder when WG asks') });
    expect(cadLinkCoordinatorBridge.getSnapshot().error ?? '')
      .toContain('Choose the folder when WG asks');
  });

  it('ends a pull the moment Fusion refuses it, with the refusal WG already holds', async () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_1', lineageId: 'wgl_1', baseEditVersion: 1,
    }, 'current');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json(linkedFusionStatus);
      if (path.endsWith('/request-fusion-return')) {
        return json({ status: 'requested', requestId: 'req_1', documentName: 'Speaker' });
      }
      return json({}, 404);
    }));
    await renderCoordinator();

    let rejection: unknown;
    await act(async () => {
      cadLinkCoordinatorBridge.getSnapshot().pullFromFusion()
        .then(() => undefined, (reason) => { rejection = reason; });
      await Promise.resolve(); await Promise.resolve();
    });

    // An operation that has only been claimed is not an answer. WGLink queues
    // the work on Fusion's main thread and can sit in `executing` for as long
    // as the export takes, so the pull goes on waiting.
    act(() => {
      useCadOperationsStore.getState().apply({
        ...refusedReturnOperation('req_1', ''), state: 'processing', stage: 'queued-for-fusion',
        reason: null, attemptGeneration: 1,
      });
    });
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });
    expect(rejection).toBeUndefined();
    expect(cadLinkCoordinatorBridge.getSnapshot().pullingFromFusion).toBe(true);
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toBeNull();

    // Fusion answered in well under a second, and WG holds its refusal: the
    // return request's operation id is its request id
    // (server/cadlink/fusion_return.py hands `request_id` to `accept_operation`).
    act(() => {
      useCadOperationsStore.getState().apply(refusedReturnOperation('req_1', WRAPPER_REFUSAL));
    });
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });

    const snapshot = cadLinkCoordinatorBridge.getSnapshot();
    // Never the 60-second wall clock, which would state that Fusion did not
    // answer -- something WG can see is false.
    expect(String(rejection)).not.toContain('60 seconds');
    expect(snapshot.error ?? '').not.toContain('60 seconds');
    expect(snapshot.error ?? '').toContain('refused');
    // The opaque identity and the internal invariant are evidence, not the
    // primary path.
    expect(snapshot.error ?? '').not.toContain('393aaad4');
    expect(snapshot.error ?? '').not.toContain('defaulted to identity');
    expect(snapshot.errorDiagnostics?.message).toBe(snapshot.error);
    expect(snapshot.errorDiagnostics?.detail).toContain(WRAPPER_REFUSAL);
    expect(snapshot.pullingFromFusion).toBe(false);
  });

  it('lets an arrived bundle win over a refusal recorded under the same request id', async () => {
    // WG can hold both at once: WGLink writes the bundle and the operation
    // record separately, and a retried or partly-failed operation can end
    // `rejected` after the geometry it produced is already in the folder. The
    // bundle is geometry the user can use; the refusal is a report about the
    // request. Presenting the refusal instead would take working geometry off
    // the screen and replace it with an error about work that did land.
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_1', lineageId: 'wgl_1', baseEditVersion: 1,
    }, 'current');
    let listing: { cadFolderConfigured: boolean; items: CadReturnBundle[] } = { cadFolderConfigured: true, items: [] };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(linkedFusionStatus);
      if (path.endsWith('/request-fusion-return')) {
        return json({ status: 'requested', requestId: 'req_1', documentName: 'Speaker' });
      }
      if (path.endsWith('/ingest')) return json(ingestRecord);
      return json({}, 404);
    }));
    await renderCoordinator();

    let settled: CadReturnBundle | null = null;
    let rejection: unknown;
    await act(async () => {
      cadLinkCoordinatorBridge.getSnapshot().pullFromFusion()
        .then((bundle) => { settled = bundle; }, (reason) => { rejection = reason; });
      await Promise.resolve(); await Promise.resolve();
    });

    // Both arrive in the same poll: the geometry in the listing, the refusal
    // under the request id this pull is waiting on.
    listing = {
      cadFolderConfigured: true,
      items: [{ ...initialBundle, requestId: 'req_1', documentName: 'Speaker pulled' }],
    };
    act(() => {
      useCadOperationsStore.getState().apply(refusedReturnOperation('req_1', WRAPPER_REFUSAL));
    });
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });

    // The pull settles on the geometry, and the refusal never becomes the
    // answer: no rejection, no error, and nothing on screen about a refusal.
    expect(rejection).toBeUndefined();
    expect(settled).toMatchObject({ requestId: 'req_1' });
    const snapshot = cadLinkCoordinatorBridge.getSnapshot();
    expect(snapshot.error).toBeNull();
    expect(snapshot.errorDiagnostics).toBeNull();
    expect(snapshot.status ?? '').not.toContain('refused');
    // And the arrival went on down the normal path: selected, then ingested.
    expect(useCadReturnStore.getState().selectedBundle?.requestId).toBe('req_1');
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(ingestRecord.ingest_id);
    expect(snapshot.pullingFromFusion).toBe(false);
  });

  it('chains pull, ingest, and solve, and stops at the readiness gate instead of solving around it', async () => {
    const linkedFusion: FusionCadStatus = {
      ...closedFusion,
      state: 'current', processRunning: true, running: true,
      documentName: 'Speaker', documentId: 'fusion:doc-1',
      link: {
        instanceId: 'wgi_1', bundlePath: null, designId: 'wgd_1', lineageId: null,
        editVersion: null, designHash: null, designName: null, formula: 'OSSE',
        configPresent: true, parameterCount: 3, parameterDriftCount: 0,
        localBodyState: 'unmodified', bodyFingerprintHash: null,
        documentSignatureHash: 'sha256:doc-state', documentBodyCount: 2,
        sourceStateHash: null, exportId: 'wge_1', exportSequence: '4',
      },
    };
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_1', lineageId: 'wgl_1', baseEditVersion: 1,
    }, 'current');
    let listing: { cadFolderConfigured: boolean; items: CadReturnBundle[] } = { cadFolderConfigured: true, items: [] };
    const ingested = ingestRecord;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(linkedFusion);
      if (path.endsWith('/request-fusion-return')) return json({ status: 'requested', requestId: 'req_1', documentName: 'Speaker' });
      if (path.endsWith('/ingest')) return json(ingested);
      return json({}, 404);
    }));
    // The readiness gate lives inside solveCurrentCadImport, so a blocked
    // chain is a refusal thrown from there — the same thing the real one does.
    const solveCurrentCadImport = vi.fn(async () => {
      throw new Error('This return includes FEM air volumes. Explicitly choose an exterior-only Phase 2 solve.');
    });
    vi.spyOn(jobsCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...jobsCoordinatorBridge.getSnapshot(), solveCurrentCadImport,
    });
    await renderCoordinator();

    const arrive = async () => {
      listing = { cadFolderConfigured: true, items: [{ ...initialBundle, requestId: 'req_1', modifiedAt: '2026-08-13T12:00:00Z' }] };
      await act(async () => {
        await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
        await Promise.resolve();
      });
    };

    // A refused gate leaves the prepared geometry and the reason on screen.
    let outcome!: string;
    await act(async () => {
      const chain = cadLinkCoordinatorBridge.getSnapshot().pullAndSolve().then((value) => { outcome = value; });
      await Promise.resolve(); await Promise.resolve();
      await arrive();
      await chain;
    });
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(ingestRecord.ingest_id);
    expect(outcome).toBe('blocked');
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toContain('exterior-only Phase 2 solve');

    // With the gate satisfied the same chain reaches the solve.
    solveCurrentCadImport.mockImplementation(async () => 'submitted' as never);
    listing = { cadFolderConfigured: true, items: [] };
    await act(async () => {
      const chain = cadLinkCoordinatorBridge.getSnapshot().pullAndSolve().then((value) => { outcome = value; });
      await Promise.resolve(); await Promise.resolve();
      await arrive();
      await chain;
    });
    expect(solveCurrentCadImport).toHaveBeenCalledTimes(2);
    expect(outcome).toBe('solving');
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBe('Solving the current Fusion geometry.');
  });

  /** The bundle names its target; an open model that is not it is not it.
   * A model with no CAD identity used to read as "no objection", which is how
   * a return for one design was prepared into an unrelated model. */
  it('treats a design-owned return as foreign to a model with no CAD identity', () => {
    const owned = { ...initialBundle, designIds: ['wgd_tritonia'] };
    expect(returnBelongsToAnotherProject(owned, null)).toBe(true);
    expect(returnBelongsToAnotherProject(owned, undefined)).toBe(true);
    expect(returnBelongsToAnotherProject(owned, 'wgd_other')).toBe(true);
    expect(returnBelongsToAnotherProject(owned, 'wgd_tritonia')).toBe(false);
    // CAD-authored geometry names no design and stays adoptable anywhere.
    expect(returnBelongsToAnotherProject(initialBundle, null)).toBe(false);
    expect(returnBelongsToAnotherProject(initialBundle, 'wgd_anything')).toBe(false);
  });

  it('refuses an arriving return that names a design the open model is not', async () => {
    // Deliberately no setCadLink: an ordinary parametric model is open, which
    // is the state the reported incident happened in.
    const foreign: CadReturnBundle = {
      ...initialBundle,
      name: 'tritonia.wgreturn',
      bundlePath: 'wgreturn/tritonia.wgreturn',
      documentName: 'waveguide v1',
      designIds: ['wgd_tritonia'],
      modifiedAt: new Date().toISOString(),
    };
    let ingestCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [foreign] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/api/cadlink/designs')) return json({ items: [] });
      if (path.endsWith('/ingest')) { ingestCalls += 1; return json(ingestRecord); }
      return json({}, 404);
    }));

    await renderCoordinator();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(ingestCalls).toBe(0);
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toContain('another CAD-linked project');
  });

  /** Refusing is only half the answer. The refusal is shown by entering CAD
   * Link, and an empty CAD Link used to be filled from the remembered project
   * -- so WG declined the return the user had just sent and then prepared a
   * different project's older geometry over the top of the message. */
  it('does not prepare the remembered project after refusing a foreign return', async () => {
    rememberCadProject('wgl_partymeh');
    const foreign: CadReturnBundle = {
      ...initialBundle,
      name: 'tritonia.wgreturn',
      bundlePath: 'wgreturn/tritonia.wgreturn',
      documentName: 'waveguide v1',
      designIds: ['wgd_tritonia'],
      modifiedAt: new Date().toISOString(),
    };
    const remembered: CadReturnBundle = {
      ...initialBundle,
      name: 'PartyMEH.wgreturn',
      bundlePath: 'wgreturn/PartyMEH.wgreturn',
      documentName: 'PartyMEH',
      designIds: [],
      modifiedAt: '2026-08-11T00:00:00Z',
    };
    const ingested: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) {
        return json({ cadFolderConfigured: true, items: [foreign, remembered] });
      }
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/api/cadlink/designs')) return json({ items: [{
        designId: null, lineageId: 'wgl_partymeh', filename: null,
        documentName: 'PartyMEH', archiveStem: 'PartyMEH', exportCount: 0,
        createdAt: '', updatedAt: '',
      }] });
      if (path.endsWith('/ingest')) {
        ingested.push(String((JSON.parse(String(init?.body)) as { bundlePath: string }).bundlePath));
        return json(ingestRecord);
      }
      return json({}, 404);
    }));

    await renderCoordinator();
    await act(async () => {
      await Promise.resolve(); await Promise.resolve();
      await Promise.resolve(); await Promise.resolve();
    });

    expect(ingested).toEqual([]);
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toContain('another CAD-linked project');
    expect(rememberedCadProject()).toBe('wgl_partymeh');
  });

  it('detects, selects, and automatically ingests a newly arrived return', async () => {
    let listing = { cadFolderConfigured: true, items: [initialBundle] };
    const ingestBodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/ingest')) {
        ingestBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return json(ingestRecord);
      }
      if (path.endsWith('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return json({}, 404);
    }));
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    await renderCoordinator();
    expect(useCadReturnStore.getState().selectedBundle).toEqual(initialBundle);
    // A first listing is not an arrival: nothing new happened, so nothing may
    // take the workspace away from the parametric design on screen.
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');

    const arrived = { ...initialBundle, modifiedAt: '2026-08-13T12:00:00Z', documentName: 'Speaker rebuilt' };
    listing = { cadFolderConfigured: true, items: [arrived] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(useCadReturnStore.getState().selectedBundle).toEqual(arrived);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(ingestRecord.ingest_id);
    expect(ingestBodies).toHaveLength(1);
    // The strip names the document it received, never the ingestion id.
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBe('Received Speaker rebuilt from Fusion.');
    // The status above renders only inside the CAD Link panel, which exists
    // only in CAD mode — so the arrival has to enter it to be visible at all.
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).toHaveBeenCalledWith('cadlink');
  });

  it('automatically ingests a manually selected readable return', async () => {
    const selected = {
      ...initialBundle,
      name: 'selected.wgreturn',
      bundlePath: 'wgreturn/selected.wgreturn',
      documentName: 'Selected speaker',
    };
    const ingested = { ...ingestRecord, ingest_id: 'wgi_selected' };
    const ingestPaths: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle, selected] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/ingest')) {
        ingestPaths.push(JSON.parse(String(init?.body)).bundlePath);
        return json(ingested);
      }
      if (path.endsWith('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return json({}, 404);
    }));
    await renderCoordinator();

    await act(async () => {
      cadLinkCoordinatorBridge.getSnapshot().selectBundle(selected);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(ingestPaths).toEqual([selected.bundlePath]);
    expect(useCadReturnStore.getState().selectedBundle).toEqual(selected);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_selected');
  });

  it('keeps the newest rapid selection and discards the superseded ingest result', async () => {
    const olderResponse = deferred<Response>();
    const newerResponse = deferred<Response>();
    const older = {
      ...initialBundle,
      name: 'older.wgreturn', bundlePath: 'wgreturn/older.wgreturn', documentName: 'Older choice',
    };
    const newer = {
      ...initialBundle,
      name: 'newer.wgreturn', bundlePath: 'wgreturn/newer.wgreturn', documentName: 'Newest choice',
    };
    const ingestPaths: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle, older, newer] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/ingest')) {
        const bundlePath = JSON.parse(String(init?.body)).bundlePath as string;
        ingestPaths.push(bundlePath);
        return bundlePath === older.bundlePath ? olderResponse.promise : newerResponse.promise;
      }
      if (path.endsWith('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return json({}, 404);
    }));
    await renderCoordinator();

    act(() => cadLinkCoordinatorBridge.getSnapshot().selectBundle(older));
    act(() => cadLinkCoordinatorBridge.getSnapshot().selectBundle(newer));
    expect(ingestPaths).toEqual([older.bundlePath, newer.bundlePath]);

    await act(async () => {
      olderResponse.resolve(json({ ...ingestRecord, ingest_id: 'wgi_older_choice' }));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();

    await act(async () => {
      newerResponse.resolve(json({ ...ingestRecord, ingest_id: 'wgi_newest_choice' }));
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    expect(useCadReturnStore.getState().selectedBundle).toEqual(newer);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_newest_choice');
  });

  it('ignores an older return listing that finishes after a newer refresh', async () => {
    const olderResponse = deferred<Response>();
    const newerResponse = deferred<Response>();
    let listingRequest = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) {
        listingRequest += 1;
        if (listingRequest === 1) return json({ cadFolderConfigured: true, items: [initialBundle] });
        return listingRequest === 2 ? olderResponse.promise : newerResponse.promise;
      }
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/ingest')) return json({ ...ingestRecord, ingest_id: 'wgi_newest_listing' });
      if (path.endsWith('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return json({}, 404);
    }));
    await renderCoordinator();
    const older = { ...initialBundle, modifiedAt: '2026-08-12T00:00:00Z', documentName: 'Older listing' };
    const newer = { ...initialBundle, modifiedAt: '2026-08-13T00:00:00Z', documentName: 'Newest listing' };

    let olderRefresh!: Promise<void>;
    let newerRefresh!: Promise<void>;
    await act(async () => {
      olderRefresh = cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      newerRefresh = cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });
    await act(async () => {
      newerResponse.resolve(json({ cadFolderConfigured: true, items: [newer] }));
      await newerRefresh;
    });
    await act(async () => {
      olderResponse.resolve(json({ cadFolderConfigured: true, items: [older] }));
      await olderRefresh;
    });

    expect(cadLinkCoordinatorBridge.getSnapshot().bundles).toEqual([newer]);
    expect(useCadReturnStore.getState().selectedBundle).toEqual(newer);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBe('Received Newest listing from Fusion.');
  });

  it('marks an ingestion stale while the CAD Link panel is unmounted', async () => {
    let listing = { cadFolderConfigured: true, items: [initialBundle] };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({}, 404);
    }));
    await renderCoordinator();
    act(() => {
      useCadReturnStore.getState().applyIngest(ingestRecord, useCadReturnStore.getState().beginIngestIntent());
      useCadReturnStore.getState().setSourceSize('source-hf', 3);
    });

    listing = { cadFolderConfigured: true, items: [{
      ...initialBundle,
      // The revision timestamp can remain stable when a listing's parsed
      // source evidence changes; reconciliation must still invalidate ingest.
      sources: [{ ...initialBundle.sources[0], suggestedResolutionMm: 2.75 }],
    }] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });

    const state = useCadReturnStore.getState();
    expect(host.childElementCount).toBe(0);
    expect(state.ingestRecord).toBe(ingestRecord);
    expect(state.needsIngest).toBe(true);
    expect(state.ingestStaleReason).toContain('source inventory or source sizing suggestions changed');
    expect(state.sourceSizesMm['source-hf']).toBe(3);
  });

  it.each([
    ['the selected return changes', () => useCadReturnStore.getState().selectBundle({
      ...initialBundle,
      name: 'other.wgreturn',
      bundlePath: 'wgreturn/other.wgreturn',
      documentName: 'Other speaker',
    })],
    ['the design is replaced', () => useDesignStore.getState().loadDesign(designForFamily('ICW'))],
  ] as const)('discards and reports an in-flight ingest when %s', async (_reason, supersede) => {
    const response = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/ingest')) return response.promise;
      return json({}, 404);
    }));
    await renderCoordinator();

    let pending!: Promise<void>;
    await act(async () => {
      pending = cadLinkCoordinatorBridge.getSnapshot().ingest();
      await Promise.resolve();
    });
    act(supersede);
    await act(async () => {
      response.resolve(json(ingestRecord));
      await pending;
    });

    const state = useCadReturnStore.getState();
    expect(state.ingestRecord).toBeNull();
    expect(state.needsIngest).toBe(true);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('Discarded');
  });

  it('keeps the newer ingest busy when an older request finishes first', async () => {
    const older = deferred<Response>();
    const newer = deferred<Response>();
    let ingestCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/ingest')) {
        ingestCalls += 1;
        return ingestCalls === 1 ? older.promise : newer.promise;
      }
      return json({}, 404);
    }));
    await renderCoordinator();
    let first!: Promise<void>;
    let second!: Promise<void>;
    await act(async () => {
      first = cadLinkCoordinatorBridge.getSnapshot().ingest();
      second = cadLinkCoordinatorBridge.getSnapshot().ingest();
      await Promise.resolve();
    });
    await act(async () => {
      older.resolve(json({ ...ingestRecord, ingest_id: 'wgi_older' }));
      await first;
    });
    expect(cadLinkCoordinatorBridge.getSnapshot().ingesting).toBe(true);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBeNull();

    await act(async () => {
      newer.resolve(json({ ...ingestRecord, ingest_id: 'wgi_newer' }));
      await second;
      await Promise.resolve();
    });
    expect(cadLinkCoordinatorBridge.getSnapshot().ingesting).toBe(false);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBe('Received Speaker from Fusion.');
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_newer');
  });

  it('parks a both-changed send on the conflict dialog and proceeds only on confirm', async () => {
    const bothChanged: FusionCadStatus = {
      ...closedFusion,
      state: 'stale',
      processRunning: true,
      running: true,
      documentName: 'Speaker v3',
      documentId: 'fusion:doc-1',
      wgChangesAvailable: true,
      fusionChangesAvailable: true,
      link: {
        instanceId: 'wgi_1', bundlePath: null, designId: 'wgd_1', lineageId: null,
        editVersion: null, designHash: null, designName: null, formula: 'OSSE',
        configPresent: true, parameterCount: 3, parameterDriftCount: 0,
        localBodyState: 'unmodified', bodyFingerprintHash: null,
        documentSignatureHash: 'sha256:doc-state', documentBodyCount: 2,
        sourceStateHash: null, exportId: 'wge_1', exportSequence: '4',
      },
    };
    const exportBodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/workspace' });
      if (path === '/api/export/wglink') {
        exportBodies.push(JSON.parse(String(init?.body)));
        return json({
          bundlePath: '/workspace/wglink/speaker.wglink', bundleId: 'wgb_2', exportId: 'wge_2',
          sequence: 5, designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
        });
      }
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json(bothChanged);
      return json({}, 404);
    }));
    await renderCoordinator();
    await act(async () => { await Promise.resolve(); });

    const origin = document.createElement('button');
    origin.textContent = 'Send from rail';
    document.body.append(origin);
    origin.focus();
    let parked: unknown = 'unset';
    await act(async () => { parked = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion(); });
    await act(async () => { await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())); });
    expect(parked).toBeNull();
    expect(cadLinkCoordinatorBridge.getSnapshot().pendingFusionConflict).toBe(true);
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('Both WG and Fusion changed');
    expect(exportBodies).toHaveLength(0);

    const dialog = host.querySelector<HTMLElement>('[role="dialog"]')!;
    const cancel = [...dialog.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Cancel')!;
    const proceed = [...dialog.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.startsWith('Continue'))!;
    expect(document.activeElement).toBe(cancel);
    const backwards = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
    act(() => document.dispatchEvent(backwards));
    expect(backwards.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(proceed);
    const forwards = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => document.dispatchEvent(forwards));
    expect(forwards.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(cancel);

    // Escape is Cancel, and gives focus back to the control that opened it.
    await act(async () => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); });
    expect(cadLinkCoordinatorBridge.getSnapshot().pendingFusionConflict).toBe(false);
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(exportBodies).toHaveLength(0);
    expect(document.activeElement).toBe(origin);

    // Confirm sends one update carrying the expected-document guard.
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion(); });
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion({ confirmed: true }); });
    expect(exportBodies).toHaveLength(1);
    expect(exportBodies[0]).toMatchObject({
      expectedFusionDocumentId: 'fusion:doc-1',
      expectedFusionInstanceId: 'wgi_1',
      expectedFusionReturnStateHash: 'sha256:doc-state',
    });
    expect(cadLinkCoordinatorBridge.getSnapshot().pendingFusionConflict).toBe(false);
    origin.remove();
  });

  it('keeps Fusion identity and feedback from the newest overlapping send', async () => {
    const older = deferred<Response>();
    const newer = deferred<Response>();
    let sendCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/workspace' });
      if (path === '/api/export/wglink') {
        sendCalls += 1;
        return sendCalls === 1 ? older.promise : newer.promise;
      }
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({}, 404);
    }));
    await renderCoordinator();
    let first!: Promise<unknown>;
    let second!: Promise<unknown>;
    await act(async () => {
      first = cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion();
      second = cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion();
      await Promise.resolve(); await Promise.resolve();
    });
    const result = (sequence: number, designId: string) => json({
      bundlePath: `/workspace/${designId}.wglink`, bundleId: `wgb_${sequence}`,
      exportId: `wge_${sequence}`, sequence, designHash: 'sha256:a', geometryHash: 'sha256:b',
      artifactSha256: 'sha256:c', identity: { designId, lineageId: `lineage-${designId}`, baseEditVersion: sequence },
    });
    await act(async () => {
      newer.resolve(result(2, 'newer-design'));
      await second;
    });
    expect(useDocumentStore.getState().identity?.designId).toBe('newer-design');
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('sequence 2');

    await act(async () => {
      older.resolve(result(1, 'older-design'));
      await first;
    });
    expect(useDocumentStore.getState().identity?.designId).toBe('newer-design');
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('sequence 2');
  });

  /** A send is slow and the coordinator is mounted for the whole life of the
   * app, so neither the request counter nor the mounted flag notices that the
   * document the export described has been replaced. The registry identity the
   * export creates belongs to the design that was exported — never to whatever
   * is on screen when the response happens to land. */
  const deferredSend = () => {
    const pending = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/workspace' });
      if (path === '/api/export/wglink') return pending.promise;
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({}, 404);
    }));
    return pending;
  };

  const sendResult = () => json({
    bundlePath: '/workspace/speaker.wglink', bundleId: 'wgb_1', exportId: 'wge_1',
    sequence: 1, designHash: 'sha256:a', geometryHash: 'sha256:b', artifactSha256: 'sha256:c',
    identity: { designId: 'wgd_sent', lineageId: 'wgl_sent', baseEditVersion: 1 },
  });

  const athPolarBlocks = () => Object.keys(useDesignStore.getState().design.extra_blocks ?? {})
    .filter((name) => name.startsWith('ABEC.Polars:'));

  it.each([
    ['another design is opened over it', () => {
      useDesignStore.getState().replaceDesign(designForFamily('R-OSSE'));
      useDocumentStore.getState().setCadLink({
        designId: 'wgd_other', lineageId: 'wgl_other', baseEditVersion: 4,
      }, 'current');
    }, 'wgd_other'],
    ['a new design replaces it', () => resetDesignStore(), null],
  ] as const)('does not apply a completed Fusion send once %s', async (_label, replace, expected) => {
    const pending = deferredSend();
    await renderCoordinator();
    let send!: Promise<unknown>;
    await act(async () => {
      send = cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion();
      await Promise.resolve();
    });

    act(replace);
    await act(async () => { pending.resolve(sendResult()); await send; });

    expect(useDocumentStore.getState().identity?.designId ?? null).toBe(expected);
    // The polar blocks are the other half of the same write: they are read
    // from, and written back into, the design store that was just replaced.
    expect(athPolarBlocks()).toEqual([]);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('stayed with the design that was exported');
  });

  /** The other half of the rule: an edit is not a replacement. The document
   * that asked for this export is still the one on screen, so it keeps the
   * registry identity the export created for it. */
  it('keeps the link an export created when the same document is edited while it sends', async () => {
    const pending = deferredSend();
    await renderCoordinator();
    let send!: Promise<unknown>;
    await act(async () => {
      send = cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion();
      await Promise.resolve();
    });

    act(() => useDesignStore.getState().updateField('R', 321));
    await act(async () => { pending.resolve(sendResult()); await send; });

    expect(useDocumentStore.getState().identity?.designId).toBe('wgd_sent');
    expect(useDesignStore.getState().design.R).toBe(321);
    expect(athPolarBlocks()).toContain('ABEC.Polars:SPL_H');
  });

  /** Recording committed polars writes them into the blocks the next freshness
   * check hashes. Doing that for a directivity the user has since changed would
   * report the document as current with settings Fusion has never seen. */
  it('does not record committed polars over directivity changed while the send was in flight', async () => {
    const pending = deferredSend();
    await renderCoordinator();
    let send!: Promise<unknown>;
    await act(async () => {
      send = cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion();
      await Promise.resolve();
    });

    act(() => useSolveOptionsStore.setState((state) => ({ polar: { ...state.polar, distance: 3 } })));
    await act(async () => { pending.resolve(sendResult()); await send; });

    // The identity still belongs to this document, so it is adopted.
    expect(useDocumentStore.getState().identity?.designId).toBe('wgd_sent');
    expect(athPolarBlocks()).toEqual([]);
  });

  it.each([
    ['loadDesign', () => useDesignStore.getState().loadDesign(designForFamily('ICW'))],
    ['replaceDesign', () => useDesignStore.getState().replaceDesign(designForFamily('R-OSSE'))],
    ['New design', () => resetDesignStore()],
  ] as const)('%s returns to parametric mode and invalidates retained CAD state', async (_path, replace) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({}, 404);
    }));
    await renderCoordinator();
    act(() => {
      const cad = useCadReturnStore.getState();
      cad.applyIngest(ingestRecord, cad.beginIngestIntent());
      cad.setSourceChannel('source-hf', 'custom-hf');
      cad.setChannelMotion('custom-hf', 'axial');
      cad.setChannelDriverField('custom-hf', 'sd_cm2', 54);
      cad.setCombineEnabled(true);
      cad.setCombineCrossover('custom-hf→custom-mf', 1_200);
      workspaceModeStore.setMode('cad');
    });

    expect(importedSubmissionBlocker()).toBeNull();
    const retainedBundle = useCadReturnStore.getState().selectedBundle;
    const retainedRecord = useCadReturnStore.getState().ingestRecord;
    const retainedChannels = useCadReturnStore.getState().driveChannels;
    const retainedDrivers = useCadReturnStore.getState().channelDrivers;
    const retainedCrossovers = useCadReturnStore.getState().combineSpec;

    act(replace);

    const state = useCadReturnStore.getState();
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(state.selectedBundle).toBe(retainedBundle);
    expect(state.ingestRecord).toBe(retainedRecord);
    expect(state.driveChannels).toBe(retainedChannels);
    expect(state.channelDrivers).toBe(retainedDrivers);
    expect(state.combineSpec).toBe(retainedCrossovers);
    expect(state.needsIngest).toBe(true);
    expect(state.ingestStaleReason).toContain('design was replaced');
    expect(importedSubmissionBlocker()).toBe(state.ingestStaleReason);
  });

  it('keeps CAD Link active and selects only this project’s latest return on project open', async () => {
    const oldDesignId = 'wgd_old_project';
    const nextDesignId = 'wgd_next_project';
    useDocumentStore.getState().setCadLink({
      designId: oldDesignId, lineageId: 'wgl_old_project', baseEditVersion: 1,
    }, 'current');
    const matching = {
      ...initialBundle,
      name: 'matching.wgreturn',
      bundlePath: 'wgreturn/matching.wgreturn',
      documentName: 'Next project',
      designIds: [nextDesignId],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle, matching] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({}, 404);
    }));
    const activate = vi.spyOn(workspaceNavigation, 'activate');
    await renderCoordinator();

    act(() => {
      useCadReturnStore.getState().selectBundle({ ...initialBundle, designIds: [oldDesignId] });
      const generation = useCadReturnStore.getState().beginIngestIntent();
      useCadReturnStore.getState().applyIngest(ingestRecord, generation);
      workspaceModeStore.setMode('cad');
    });
    await act(async () => {
      useDesignStore.getState().replaceDesign(designForFamily('R-OSSE'), {
        loadSource: 'cad-project-switch',
      });
      useDocumentStore.getState().setCadLink({
        designId: nextDesignId, lineageId: 'wgl_next_project', baseEditVersion: 2,
      }, 'current');
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).toHaveBeenCalledWith('cadlink');
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(matching.bundlePath);
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('Selected the latest matching return');
  });

  /** Opening a project replaces the design, and the settings the CAD rail then
   * restores must be that project's. They were restored under the project that
   * was open before it, and the ingestion that followed filed them -- another
   * project's voltage and drivers -- over the destination's own saved setup. */
  it('restores the opened project’s own solve profile, and does not save the previous one over it', async () => {
    const presetA = {
      id: 'Faital Pro::12RS430::8', label: 'Faital Pro 12RS430', source: 'database' as const,
      kind: 'lf' as const, z_ohm: 8, xo_min_hz: null,
      base: { sd_cm2: 552, bl_t_m: 18, re_ohm: 6.8 },
    };
    const presetB = {
      id: 'B&C::DE250::8', label: 'B&C DE250', source: 'database' as const,
      kind: 'cd' as const, z_ohm: 8, xo_min_hz: 1_200,
      base: { re_ohm: 5.4, bl_t_m: 17.5 },
    };
    const returnA = {
      ...initialBundle, name: 'a.wgreturn', bundlePath: 'wgreturn/a.wgreturn',
      documentName: 'Project A', designIds: ['wgd_a'],
    };
    const returnB = {
      ...initialBundle, name: 'b.wgreturn', bundlePath: 'wgreturn/b.wgreturn',
      documentName: 'Project B', designIds: ['wgd_b'],
    };
    const recordB = { ...ingestRecord, project: { lineage_id: 'wgl_b' } };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [returnA, returnB] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/api/cadlink/designs')) return json({ items: [] });
      if (path.endsWith('/ingest')) return json(recordB);
      if (path.endsWith('/viewport-mesh')) return new Response(viewportMesh);
      return json({}, 404);
    }));
    await renderCoordinator();

    // Each project's own saved setup, as its own selections would have written
    // them: 3 V and an LF driver for A, 8 V and a compression driver for B.
    act(() => {
      const cad = useCadReturnStore.getState();
      cad.selectBundle(returnB, 'wgl_b');
      cad.setDriveVoltage(8);
      cad.setChannelDriverPreset('drive-hf', presetB);
      cad.selectBundle(returnA, 'wgl_a');
      cad.setDriveVoltage(3);
      cad.setChannelDriverPreset('drive-hf', presetA);
    });
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 1,
    }, 'current');
    expect(useCadReturnStore.getState().driveVoltageV).toBe(3);

    // The project switcher opens B: `applyOpenedDesign` replaces the design
    // first and assigns the identity immediately afterwards.
    await act(async () => {
      useDesignStore.getState().replaceDesign(designForFamily('R-OSSE'), {
        loadSource: 'cad-project-switch',
      });
      useDocumentStore.getState().setCadLink({
        designId: 'wgd_b', lineageId: 'wgl_b', baseEditVersion: 1,
      }, 'current');
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });

    const switched = useCadReturnStore.getState();
    expect(switched.selectedBundle?.bundlePath).toBe(returnB.bundlePath);
    expect(switched.projectLineageId).toBe('wgl_b');
    expect(switched.driveVoltageV).toBe(8);
    expect(switched.channelDrivers['drive-hf'].preset).toMatchObject({ id: 'B&C::DE250::8' });

    // Preparing B files the settings under B, so reopening B still finds them.
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().ingest(); });
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(recordB.ingest_id);

    act(() => {
      resetCadReturnStore();
      useCadReturnStore.getState().selectBundle(returnB, 'wgl_b');
    });
    const reopened = useCadReturnStore.getState();
    expect(reopened.driveVoltageV).toBe(8);
    expect(reopened.channelDrivers['drive-hf'].preset).toMatchObject({ id: 'B&C::DE250::8' });
    // A's profile is untouched by any of it.
    act(() => {
      resetCadReturnStore();
      useCadReturnStore.getState().selectBundle(returnA, 'wgl_a');
    });
    expect(useCadReturnStore.getState().driveVoltageV).toBe(3);
  });

  it('restores driver, crossover, mesh, and sweep inputs from a historical CAD run', async () => {
    const record = {
      ...ingestRecord,
      sources: [
        ingestRecord.sources[0],
        {
          ...ingestRecord.sources[0],
          id: 'source-mf', role: 'MF', default_drive_channel_id: 'drive-mf',
        },
      ],
      mesh_sizes: {
        rigid_size_mm: 7,
        transition_mm: 14,
        source_size_mm: { 'source-hf': 3, 'source-mf': 5 },
      },
    };
    const job = {
      id: 'historical-cad',
      run_number: 82,
      label: 'Tritonia run',
      config_summary: { geometry_type: 'imported' },
      cad_source: { ingest_id: record.ingest_id, document_name: 'Tritonia V' },
      cad_setup: {
        type: 'imported',
        ingest_id: record.ingest_id,
        drive_channels: [
          { id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' },
          {
            id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal',
            driver: {
              sd_cm2: 82, bl_t_m: 11.4, re_ohm: 5.8, le_mh: 0.4,
              mmd_g: 18.5, cms_m_per_n: 0.00042, rms_kg_per_s: 1.2,
              xmax_mm: 6, count: 2, rear_volume_l: 1.5,
            },
          },
        ],
        combine: {
          members: ['drive-mf', 'drive-hf'], crossovers_hz: [1_250],
          level_match: false, align: true,
        },
        drive_voltage_v: 4,
        mesh: {
          rigid_size_mm: 8,
          transition_mm: 16,
          source_size_mm: { 'source-mf': 4, 'source-hf': 2.5 },
        },
        skipped_source_ids: [],
        exterior_only: true,
      },
      solve_options: {
        engine: 'metal', symmetry: 'auto', frequency_range: null,
        num_frequencies: null, frequency_spacing: 'linear',
        frequencies_hz: [400, 800, 1_600], verbose: true,
        mesh_validation_mode: 'strict', polar_config: {
          angle_range: [-90, 90, 19], distance: 2, norm_angle: 5,
          inclination: 45, enabled_axes: ['horizontal'],
          observation_origin: 'mouth', spherical_sampling: false,
        },
        stage_delay_ms: 0,
      },
    } as unknown as import('../api/jobsSocket').JobItem;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === `/api/cadlink/ingest/${record.ingest_id}`) return json(record);
      if (path.endsWith('/viewport-mesh')) return new Response(viewportMesh);
      return json({}, 404);
    }));

    // The user's solver choice, which the run's own engine must not replace.
    act(() => { useSolveOptionsStore.setState({ engine: 'bempp' }); });
    let shown = false;
    await act(async () => { shown = await showCadJobModel(job); });

    const state = useCadReturnStore.getState();
    expect(shown).toBe(true);
    expect(state.driveChannels).toEqual(job.cad_setup?.drive_channels?.map(({ driver: _driver, ...channel }) => channel));
    expect(state.channelDrivers['drive-hf']).toEqual({
      fields: {
        sd_cm2: 82, bl_t_m: 11.4, re_ohm: 5.8, le_mh: 0.4,
        mmd_g: 18.5, cms_m_per_n: 0.00042, rms_kg_per_s: 1.2,
        xmax_mm: 6, count: 2, rear_volume_l: 1.5,
      },
      // The stored setup names no driver, so this is still hand entry.
      preset: null,
    });
    expect(state.combineEnabled).toBe(true);
    // A job submitted before the per-channel spec carries only the legacy
    // triple; drift compares one shape, so it is expanded on the way in.
    expect(state.combineSpec).toEqual(expandLegacy(['drive-mf', 'drive-hf'], [1_250], false, true));
    expect(state.driveVoltageV).toBe(4);
    expect(state.sourceSizesMm).toEqual({ 'source-mf': 4, 'source-hf': 2.5 });
    expect(state.rigidSizeMm).toBe(8);
    expect(state.transitionMm).toBe(16);
    expect(state.exteriorOnly).toBe(true);
    expect(state.frequencyStartHz).toBe(400);
    expect(state.frequencyEndHz).toBe(1_600);
    expect(state.frequencyCount).toBe(3);
    expect(useSolveOptionsStore.getState()).toMatchObject({
      frequencyMode: 'list', frequencyListText: '400\n800\n1600',
      frequencySpacing: 'linear', meshValidationMode: 'strict', verbose: true,
    });
    // Recalling a run never changes the user's solver choice: the run keeps
    // its engine on its own record, and the next solve uses the selector's.
    expect(useSolveOptionsStore.getState().engine).toBe('bempp');

    // A job submitted with the per-channel spec restores it verbatim, unlinked
    // pair and manual gain included — the shape drift compares against.
    const v2Spec = withChannel(
      withPair(expandLegacy(['drive-mf', 'drive-hf'], [1_250]), 'drive-mf→drive-hf', { family: 'bessel', order: 3 }),
      'drive-hf',
      { gain: { mode: 'manual', db: -1.5 }, invert: true },
    );
    const v2Job = {
      ...job,
      cad_setup: { ...job.cad_setup, combine: toWire(v2Spec) },
    } as unknown as import('../api/jobsSocket').JobItem;
    await act(async () => { await showCadJobModel(v2Job); });
    expect(useCadReturnStore.getState().combineSpec).toEqual(v2Spec);
  });

  /** Two archived CAD runs, picked one after the other while the first
   * ingestion record is still in flight. The click that selected the second run
   * is the newest intent there is; a slow response for the first must not
   * become the CAD rail, the solve inputs, or the geometry on screen. */
  const archivedCadJob = (
    runNumber: number, ingestId: string, voltage: number,
  ) => ({
    id: `run-${runNumber}`,
    run_number: runNumber,
    label: `Run ${runNumber}`,
    config_summary: { geometry_type: 'imported' },
    cad_source: { ingest_id: ingestId, document_name: `Document ${runNumber}` },
    cad_setup: {
      type: 'imported',
      ingest_id: ingestId,
      drive_channels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }],
      drive_voltage_v: voltage,
      mesh: { rigid_size_mm: voltage, transition_mm: voltage, source_size_mm: { 'source-hf': voltage } },
      skipped_source_ids: [],
      exterior_only: false,
    },
    solve_options: {
      engine: 'metal', symmetry: 'auto', frequency_range: [200, 20_000],
      num_frequencies: 24, frequency_spacing: 'log', frequencies_hz: null,
      verbose: false, mesh_validation_mode: 'warn', polar_config: null,
      stage_delay_ms: 0,
    },
  } as unknown as import('../api/jobsSocket').JobItem);

  const archivedCadRoutes = (slow: string, pending: { promise: Promise<Response> }) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      const ingest = /\/api\/cadlink\/ingest\/([^/]+)$/.exec(path)?.[1];
      if (ingest) {
        return ingest === slow
          ? pending.promise
          : json({ ...ingestRecord, ingest_id: ingest });
      }
      if (path.endsWith('/viewport-mesh')) return new Response(viewportMesh);
      return json({}, 404);
    }));
  };

  it('keeps the newest archived CAD run when an older record arrives after it', async () => {
    const pending = deferred<Response>();
    archivedCadRoutes('wgi_first', pending);

    let first!: Promise<boolean>;
    await act(async () => {
      first = showCadJobModel(archivedCadJob(1, 'wgi_first', 3));
      await Promise.resolve();
    });
    await act(async () => { await showCadJobModel(archivedCadJob(2, 'wgi_second', 7)); });

    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_second');
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_second');

    let firstResult = true;
    await act(async () => {
      pending.resolve(json({ ...ingestRecord, ingest_id: 'wgi_first' }));
      firstResult = await first;
    });

    expect(firstResult).toBe(false);
    const state = useCadReturnStore.getState();
    expect(state.ingestRecord?.ingest_id).toBe('wgi_second');
    expect(state.selectedBundle?.documentName).toBe('Document 2');
    expect(state.driveVoltageV).toBe(7);
    expect(state.rigidSizeMm).toBe(7);
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_second');
  });

  it('drops an archived CAD run whose record lands after a parametric run was selected', async () => {
    const pending = deferred<Response>();
    archivedCadRoutes('wgi_first', pending);

    let first!: Promise<boolean>;
    await act(async () => {
      first = showCadJobModel(archivedCadJob(1, 'wgi_first', 3));
      await Promise.resolve();
    });
    // The real parametric selection: `selectJob` routes it to `showJobModel`,
    // which replaces the working design and returns to parametric mode.
    const parametric = {
      id: 'run-3',
      run_number: 3,
      config_summary: { geometry_type: 'parametric' },
      script_snapshot: { version: 1, design: designForFamily('ICW') },
    } as unknown as import('../api/jobsSocket').JobItem;
    await act(async () => { expect(await showJobModel(parametric)).toBe(true); });
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');

    let firstResult = true;
    await act(async () => {
      pending.resolve(json({ ...ingestRecord, ingest_id: 'wgi_first' }));
      firstResult = await first;
    });

    expect(firstResult).toBe(false);
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();
    expect(importedMeshStore.getSnapshot().cad).toBeNull();
    expect(useDesignStore.getState().design.formula).toBe('ICW');
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
  });

  it('reopens the remembered CAD project when the mode comes back empty', async () => {
    rememberCadProject('wgl_remembered');
    expect(rememberedCadProject()).toBe('wgl_remembered');
    // The open design belongs to no return, exactly the state a reload in
    // Parametric mode leaves behind: nothing auto-selects on the first listing.
    useDocumentStore.getState().setCadLink({ designId: 'wgd_unrelated', lineageId: 'wgl_unrelated', baseEditVersion: 1 }, 'current');
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path.endsWith('/api/cadlink/designs')) return json({ items: [{
        designId: null, lineageId: 'wgl_remembered', filename: null,
        documentName: initialBundle.documentName, archiveStem: initialBundle.documentName,
        exportCount: 0, createdAt: '2026-08-23T14:34:20Z', updatedAt: '2026-08-23T19:15:10Z',
      }] });
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({}, 404);
    }));

    await renderCoordinator();
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();

    await act(async () => {
      workspaceModeStore.setMode('cad');
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });

    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(initialBundle.bundlePath);
    expect(calls.some((path) => path.endsWith('/api/cadlink/designs'))).toBe(true);
  });

  it('never restores over a selection the listing already made', async () => {
    rememberCadProject('wgl_remembered');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/api/cadlink/designs')) return json({ items: [] });
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({}, 404);
    }));
    await renderCoordinator();
    const other = { ...initialBundle, bundlePath: 'wgreturn/other.wgreturn', name: 'other.wgreturn' };
    act(() => useCadReturnStore.getState().selectBundle(other));

    await act(async () => {
      workspaceModeStore.setMode('cad');
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });

    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe('wgreturn/other.wgreturn');
  });

  /** Fusion's solve commands are the backend's to collect and prepare
   * (CAD-OPERATIONS.md, "Delivery"). This client reads the operations the
   * backend made of them, and acts on one only when the user asks. */
  const cadOperation = (overrides: Partial<CadOperationSummary> = {}): CadOperationSummary => ({
    operationId: 'op-1',
    kind: 'prepare_and_solve',
    state: 'needs_user_input',
    stage: 'ready',
    reason: 'ready_to_solve',
    message: 'Prepared, and waiting for you to start the solve.',
    jobId: null,
    attemptGeneration: 1,
    setupRevisionId: 'wgs_1',
    preparationId: 'wgp_1',
    snapshot: { manifestSha256: ingestRecord.manifest_sha256 },
    legacy: false,
    createdAt: '2026-09-14T10:00:00Z',
    updatedAt: '2026-09-14T10:00:05Z',
    ...overrides,
  });

  const operationRoutes = () => {
    const calls: string[] = [];
    const posted: Array<{ path: string; body: unknown }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push(path);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path === '/api/cadlink/operations') return json({ operations: [cadOperation()] });
      if (path.endsWith('/prepare') || path.endsWith('/cancel')) {
        posted.push({ path, body: init?.body ? JSON.parse(String(init.body)) : null });
        // As the routes answer: prepare with the row as it was before the
        // claim, cancel with the row the request left.
        return path.endsWith('/cancel')
          ? json(cadOperation({ state: 'cancelled', reason: null, updatedAt: '2026-09-14T10:00:10Z' }))
          : json({ operation: cadOperation() });
      }
      // A marker Fusion left behind: the backend's to collect, never this client's.
      if (path.endsWith('/solve-command')) return json({ command: {
        commandId: 'cmd-1', returnId: 'wgr_1', bundlePath: initialBundle.bundlePath,
        manifestSha256: 'sha256:m', requestedAt: '2026-09-14T10:00:00Z',
      }, outcome: null });
      return json({}, 404);
    }));
    return { calls, posted };
  };

  it('reads the pending CAD operations and never consumes a Fusion solve command itself', async () => {
    const { calls } = operationRoutes();
    await renderCoordinator();
    await vi.waitFor(() => {
      expect(useCadOperationsStore.getState().operations['op-1']?.reason).toBe('ready_to_solve');
    });
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(calls.filter((path) => path.includes('/solve-command'))).toEqual([]);
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();
  });

  it('prepares an operation from its project setup on Solve now, and cancels it on Dismiss', async () => {
    const { posted } = operationRoutes();
    await renderCoordinator();
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().solveOperation('op-1'); });
    // No setup revision: the backend resolves the snapshot's own project setup.
    expect(posted).toEqual([{ path: '/api/cadlink/operations/op-1/prepare', body: { submit: true } }]);
    // Nothing moves until the attempt reports on the jobs channel.
    expect(useCadOperationsStore.getState().operations['op-1']).toMatchObject({
      state: 'needs_user_input', attemptGeneration: 1,
    });

    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().dismissOperation('op-1'); });
    expect(posted[1]).toEqual({ path: '/api/cadlink/operations/op-1/cancel', body: null });
    expect(useCadOperationsStore.getState().operations['op-1']?.state).toBe('cancelled');
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBe('Dismissed the solve Fusion asked for.');
  });

  it('says a dismissed solve follows its job when the job already exists, not that it was dismissed', async () => {
    operationRoutes();
    const routed = vi.mocked(fetch).getMockImplementation()!;
    // The backend reconciles before it dismisses: the job exists, so the operation follows it.
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => (
      String(input).endsWith('/cancel')
        ? json(cadOperation({ state: 'accepted', stage: 'submitted', reason: null, message: null, jobId: 'job-7' }))
        : routed(input, init)
    )));
    await renderCoordinator();

    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().dismissOperation('op-1'); });

    expect(useCadOperationsStore.getState().operations['op-1']).toMatchObject({ state: 'accepted', jobId: 'job-7' });
    const status = cadLinkCoordinatorBridge.getSnapshot().status ?? '';
    expect(status).not.toContain('Dismissed');
    expect(status).toContain('Jobs rail');
  });

  it('approves reviewed findings only on the preparation that reported them', async () => {
    const { posted } = operationRoutes();
    await renderCoordinator();
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().approveOperation('op-1', {
        preparationId: 'wgp_1', findingIds: ['finding-a'],
      });
    });
    expect(posted).toEqual([{
      path: '/api/cadlink/operations/op-1/prepare',
      body: { submit: true, approvals: { preparationId: 'wgp_1', findingIds: ['finding-a'] } },
    }]);
  });

  /** The design on screen as it was opened from its own project. */
  function openedProject() {
    return {
      dialect: 'ath', migrationsApplied: [],
      passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
      design: { ...useDesignStore.getState().design, R: 150 },
      cadlink: {
        identity: { designId: 'wgd_current', lineageId: 'wgl_current', baseEditVersion: 2 },
        classification: 'current',
      },
    } as unknown as Parameters<typeof applyOpenedDesign>[0];
  }

  it('leaves unsaved edits alone while a solve Fusion sent for another project runs to its job', async () => {
    act(() => { applyOpenedDesign(openedProject(), 'current.cfg'); });
    useDesignStore.getState().updateField('R', 321);
    const design = useDesignStore.getState().design;
    const revision = useDesignStore.getState().designRevision;
    const identity = useDocumentStore.getState().identity;
    const { calls } = operationRoutes();
    await renderCoordinator();

    const otherProject = { manifestSha256: `sha256:${'b'.repeat(64)}` };
    await act(async () => {
      const { apply } = useCadOperationsStore.getState();
      apply(cadOperation({ operationId: 'op-b', state: 'received', stage: 'received', reason: null, attemptGeneration: 0, snapshot: otherProject, updatedAt: '2026-09-14T10:00:00Z' }));
      apply(cadOperation({ operationId: 'op-b', state: 'processing', stage: 'preparing-mesh', reason: null, snapshot: otherProject, updatedAt: '2026-09-14T10:00:01Z' }));
      apply(cadOperation({ operationId: 'op-b', state: 'accepted', stage: 'submitted', reason: null, jobId: 'job-b', snapshot: otherProject, updatedAt: '2026-09-14T10:00:06Z' }));
      for (let i = 0; i < 6; i += 1) await Promise.resolve();
    });

    expect(useCadOperationsStore.getState().operations['op-b']?.state).toBe('accepted');
    expect(useDesignStore.getState().design).toBe(design);
    expect(useDesignStore.getState().design.R).toBe(321);
    expect(useDesignStore.getState().designRevision).toBe(revision);
    expect(useDocumentStore.getState().identity).toEqual(identity);
    // Nothing was opened, selected or prepared on this client's side.
    expect(calls.filter((path) => (
      path.startsWith('/api/cadlink/designs') || path.includes('/solve-command') || path.endsWith('/ingest')
    ))).toEqual([]);
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();
  });

  it('keeps the return the user picked over one Fusion was asked for before the pick', async () => {
    const linkedFusion: FusionCadStatus = {
      ...closedFusion,
      state: 'current', processRunning: true, running: true,
      documentName: 'Speaker', documentId: 'fusion:doc-1',
      link: {
        instanceId: 'wgi_1', bundlePath: null, designId: 'wgd_1', lineageId: null,
        editVersion: null, designHash: null, designName: null, formula: 'OSSE',
        configPresent: true, parameterCount: 3, parameterDriftCount: 0,
        localBodyState: 'unmodified', bodyFingerprintHash: null,
        documentSignatureHash: 'sha256:doc-state', documentBodyCount: 2,
        sourceStateHash: null, exportId: 'wge_1', exportSequence: '4',
      },
    };
    useDocumentStore.getState().setCadLink({ designId: 'wgd_1', lineageId: 'wgl_1', baseEditVersion: 1 }, 'current');
    const picked: CadReturnBundle = {
      ...initialBundle, name: 'picked.wgreturn', bundlePath: 'wgreturn/picked.wgreturn', documentName: 'Speaker picked',
    };
    const pulled: CadReturnBundle = {
      ...initialBundle, name: 'pulled.wgreturn', bundlePath: 'wgreturn/pulled.wgreturn', requestId: 'req_1',
      documentName: 'Speaker pulled', modifiedAt: '2026-09-14T10:00:30Z',
    };
    let listing: { cadFolderConfigured: boolean; items: CadReturnBundle[] } = { cadFolderConfigured: true, items: [picked] };
    const ingested: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(linkedFusion);
      if (path.endsWith('/request-fusion-return')) {
        return json({ status: 'requested', requestId: 'req_1', documentName: 'Speaker' });
      }
      if (path.endsWith('/ingest')) {
        const bundlePath = String((JSON.parse(String(init?.body)) as { bundlePath: string }).bundlePath);
        ingested.push(bundlePath);
        return json({ ...ingestRecord, ingest_id: bundlePath === picked.bundlePath ? 'wgi_picked' : 'wgi_pulled' });
      }
      if (path.includes('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return json({}, 404);
    }));
    await renderCoordinator();

    let pull!: Promise<CadReturnBundle>;
    await act(async () => {
      pull = cadLinkCoordinatorBridge.getSnapshot().pullFromFusion();
      pull.catch(() => undefined);
      await Promise.resolve(); await Promise.resolve();
    });
    // While Fusion is still exporting, the user picks another return.
    await act(async () => { cadLinkCoordinatorBridge.getSnapshot().selectBundle(picked); });
    await vi.waitFor(() => expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_picked'));

    listing = { cadFolderConfigured: true, items: [pulled, picked] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
    });
    await act(async () => { await expect(pull).rejects.toBeInstanceOf(SupersededError); });

    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(picked.bundlePath);
    expect(ingested).toEqual([picked.bundlePath]);
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_picked');
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('Speaker pulled');
  });

  it('keeps the return the user picked over one Fusion wrote before the pick, and takes one written after it', async () => {
    const picked: CadReturnBundle = {
      ...initialBundle, name: 'picked.wgreturn', bundlePath: 'wgreturn/picked.wgreturn', documentName: 'Speaker picked',
    };
    const older: CadReturnBundle = {
      ...initialBundle, name: 'older.wgreturn', bundlePath: 'wgreturn/older.wgreturn', documentName: 'Speaker older',
      modifiedAt: '2026-01-01T00:00:00Z',
    };
    const newer: CadReturnBundle = {
      ...initialBundle, name: 'newer.wgreturn', bundlePath: 'wgreturn/newer.wgreturn', documentName: 'Speaker newer',
      modifiedAt: '2099-01-01T00:00:00Z',
    };
    let listing: { cadFolderConfigured: boolean; items: CadReturnBundle[] } = { cadFolderConfigured: true, items: [picked] };
    const ingested: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/ingest')) {
        const bundlePath = String((JSON.parse(String(init?.body)) as { bundlePath: string }).bundlePath);
        ingested.push(bundlePath);
        return json({ ...ingestRecord, ingest_id: `wgi_${bundlePath.split('/')[1].split('.')[0]}` });
      }
      if (path.includes('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return json({}, 404);
    }));
    await renderCoordinator();
    await act(async () => { cadLinkCoordinatorBridge.getSnapshot().selectBundle(picked); });
    await vi.waitFor(() => expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_picked'));

    // Written before the pick, and only noticed after it: the pick is newer.
    listing = { cadFolderConfigured: true, items: [older, picked] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
    });
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(picked.bundlePath);
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_picked');

    // Sent from Fusion after the pick: newer intent, which takes the selection.
    listing = { cadFolderConfigured: true, items: [newer, older, picked] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
    });
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(newer.bundlePath);
    await vi.waitFor(() => expect(ingested).toEqual([picked.bundlePath, newer.bundlePath]));
  });

  it('never lets a display mesh finishing for one model cancel the viewport load of a newer selection', async () => {
    let displayReady = false;
    const fetcher = (async (input: RequestInfo | URL) => {
      const path = String(input);
      if (!path.endsWith('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return displayReady ? new Response(viewportMesh, { status: 200 }) : new Response('', { status: 202 });
    }) as typeof fetch;
    const first = { ...ingestRecord, ingest_id: 'wgi_first' };
    useCadReturnStore.getState().selectBundle(initialBundle);
    useCadReturnStore.setState({ ingestRecord: first });
    workspaceModeStore.setMode('cad');
    await showIngestedMeshInViewport(first, 'Speaker', undefined, fetcher);
    expect(importedMeshStore.getSnapshot().cad?.artifactToken).toBe('wgi_first:solver');

    // The user selects another return; its viewport load begins at once and
    // is still in flight when the first model's display tessellation lands.
    useCadReturnStore.getState().selectBundle({
      ...initialBundle, name: 'second.wgreturn', bundlePath: 'wgreturn/second.wgreturn',
    });
    const second = importedMeshStore.beginIntent();
    displayReady = true;
    await new Promise((resolve) => { window.setTimeout(resolve, 600); });

    expect(importedMeshStore.isCurrentGeneration(second)).toBe(true);
    expect(importedMeshStore.getSnapshot().cad?.artifactToken).toBe('wgi_first:solver');
  });

  /** Stub the three calls opening a CAD-linked project actually makes. */
  function projectOpenRoutes(designId: string, lineageId: string, name: string) {
    return (path: string): Response | null => {
      if (path.endsWith(`/designs/${designId}`)) {
        return json({ designId, lineageId, editVersion: 2, filename: `${name}.cfg`, text: 'R = 160' });
      }
      if (path.endsWith('/cadlink/designs')) {
        return json({ items: [{
          designId, lineageId, filename: `${name}.cfg`, documentName: name,
          archiveStem: name, exportCount: 1, editVersion: 2,
          createdAt: '2026-09-04T00:00:00Z', updatedAt: '2026-09-04T00:00:00Z',
        }] });
      }
      if (path === '/api/design/open') {
        return json({
          dialect: 'ath', migrationsApplied: [],
          passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
          design: useDesignStore.getState().design,
          cadlink: {
            identity: { designId, lineageId, baseEditVersion: 2 },
            classification: 'current',
          },
        });
      }
      return null;
    };
  }

  it('drops a viewport mesh it could not replace rather than blocking the next solve', async () => {
    const first: CadReturnBundle = { ...initialBundle, name: 'first.wgreturn', bundlePath: 'wgreturn/first.wgreturn' };
    const second: CadReturnBundle = { ...initialBundle, name: 'second.wgreturn', bundlePath: 'wgreturn/second.wgreturn' };
    let artifactsFail = false;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [first, second] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/ingest')) {
        const bundlePath = String((JSON.parse(String(init?.body)) as { bundlePath: string }).bundlePath);
        return json({ ...ingestRecord, ingest_id: bundlePath === first.bundlePath ? 'wgi_first' : 'wgi_second' });
      }
      if (path.includes('/viewport-mesh') || path.endsWith('/mesh')) {
        return artifactsFail ? json({}, 500) : new Response(viewportMesh, { status: 200 });
      }
      return json({}, 404);
    }));
    await renderCoordinator();
    await act(async () => { cadLinkCoordinatorBridge.getSnapshot().selectBundle(first); });
    await vi.waitFor(() => expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_first'));

    // Both display artifacts fail for the next model the user picks.
    artifactsFail = true;
    await act(async () => { cadLinkCoordinatorBridge.getSnapshot().selectBundle(second); });
    await vi.waitFor(() => expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_second'));
    await vi.waitFor(() => expect(importedMeshStore.getSnapshot().cad).toBeNull());
    // An empty slot is honest, and it is no reason to refuse the solve.
    expect(cadSolveBlockerNow()).toBeNull();
  });

  /** Opening another project from File → CAD-linked designs while a send is on
   * the wire: the identity the export registers belongs to the design that was
   * exported, never to the one on screen when the response lands. */
  it('does not adopt a send identity after File → CAD-linked designs opened another project', async () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_current', lineageId: 'wgl_current', baseEditVersion: 2,
    }, 'current');
    const pending = deferred<Response>();
    const routes = projectOpenRoutes('wgd_other', 'wgl_other', 'Tritonia');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/workspace' });
      if (path === '/api/export/wglink') return pending.promise;
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return routes(path) ?? json({}, 404);
    }));

    await renderCoordinator();
    let send!: Promise<unknown>;
    await act(async () => {
      send = cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion();
      await Promise.resolve();
    });
    await act(async () => {
      await openCadLinkedProject('wgd_other', takeDesignOpenTicket(), { loadSource: 'cad-project-switch' });
    });
    expect(useDocumentStore.getState().identity?.designId).toBe('wgd_other');

    await act(async () => { pending.resolve(sendResult()); await send; });
    expect(useDocumentStore.getState().identity?.designId).toBe('wgd_other');
  });

  /** "Use these settings and solve" checks what is on screen when it is
   * pressed, not when the card rendered: the selection, the ingestion or the
   * listing can all have moved since, and nothing is recorded for a model
   * the settings were not prepared with. */
  it('records the settings on screen only for the model, ingestion and project they belong to', async () => {
    const recorded: Array<{ lineageId: string }> = [];
    const prepared: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path === '/api/cadlink/project-setups') {
        const body = JSON.parse(String(init?.body)) as { lineageId: string };
        recorded.push(body);
        return json({ lineageId: body.lineageId, inventorySha256: 'sha256:i', revisionId: 'wgs_9' });
      }
      if (path.endsWith('/prepare')) {
        prepared.push(path);
        const operationId = decodeURIComponent(path.split('/')[4]);
        return json({ operation: useCadOperationsStore.getState().operations[operationId] });
      }
      return json({}, 404);
    }));
    // The parametric document has a lineage of its own, which is never the
    // CAD model's project.
    useDocumentStore.getState().setCadLink({ designId: 'wgd_doc', lineageId: 'wgl_document', baseEditVersion: 1 }, 'current');
    await renderCoordinator();

    const prepare = (lineageId: string | null) => {
      const store = useCadReturnStore.getState();
      store.selectBundle(initialBundle, lineageId);
      const record = lineageId
        ? { ...ingestRecord, project: { lineage_id: lineageId } } as unknown as CadReturnIngestRecord
        : ingestRecord;
      expect(store.applyIngest(record, store.beginIngestIntent())).toBe(true);
    };
    const attempt = async (
      operationId: string,
      snapshot: { manifestSha256?: string; projectLineageId?: string | null } = {},
    ): Promise<string | null> => {
      act(() => {
        useCadOperationsStore.getState().apply(cadOperation({
          operationId, reason: 'setup_required', stage: 'received', setupRevisionId: null, preparationId: null,
          snapshot: {
            manifestSha256: ingestRecord.manifest_sha256, documentName: 'Speaker', projectLineageId: 'wgl_guarded_a', ...snapshot,
          },
        }));
      });
      let failure: unknown = null;
      await act(async () => {
        failure = await cadLinkCoordinatorBridge.getSnapshot().solveOperationWithSettings(operationId)
          .then(() => null, (reason: unknown) => reason);
      });
      return failure instanceof Error ? failure.message : null;
    };

    // Another model's snapshot.
    prepare('wgl_guarded_a');
    expect(await attempt('op-other-model', { manifestSha256: `sha256:${'9'.repeat(64)}` })).toContain('not the model on screen');
    // Waiting to be ingested again.
    prepare('wgl_guarded_a');
    act(() => { useCadReturnStore.setState({ needsIngest: true }); });
    expect(await attempt('op-stale')).toContain('changed since it was prepared');
    // A revised listing of the return, paired with the ingestion of the old one.
    prepare('wgl_guarded_a');
    act(() => {
      useCadReturnStore.setState({ selectedBundle: { ...initialBundle, modifiedAt: '2026-09-14T12:00:00Z' } });
    });
    expect(await attempt('op-revised')).toContain('changed since it was prepared');
    // Filed under another project than the one the backend names.
    prepare('wgl_guarded_a');
    expect(await attempt('op-other-project', { projectLineageId: 'wgl_guarded_b' })).toContain('another project');
    // No project named, and none filed: the document's lineage is not a substitute.
    prepare(null);
    expect(await attempt('op-unfiled', { projectLineageId: null })).toContain('which project');
    expect(recorded).toEqual([]);
    expect(prepared).toEqual([]);
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toContain('which project');

    // No project named: the ingestion's own is the one recorded for.
    prepare('wgl_guarded_a');
    expect(await attempt('op-filed', { projectLineageId: null })).toBeNull();
    expect(recorded.map(({ lineageId }) => lineageId)).toEqual(['wgl_guarded_a']);
    expect(prepared).toEqual(['/api/cadlink/operations/op-filed/prepare']);
  });
});
