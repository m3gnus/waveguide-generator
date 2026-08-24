import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord, FusionCadStatus } from '../api/cadlink';
import { importedSubmissionBlocker } from '../jobs/importedSubmission';
import { preferencesStore } from '../prefs/preferences';
import { expandLegacy, toWire, withChannel, withPair } from '../results/crossoverSpec';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { consumeParkedSolveCommand, parkedSolveCommandStore } from '../stores/solveCommand';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { rememberCadProject, rememberedCadProject } from '../stores/cadProjectMemory';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import {
  CadLinkCoordinator,
  cadLinkCoordinatorBridge,
  showCadJobModel,
} from './CadLinkCoordinator';
import { cadSolveBlockerNow, jobsCoordinatorBridge, SolveEngineUnavailableError } from './JobsCoordinator';
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
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('CadLinkCoordinator', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetCadReturnStore();
    resetDesignStore();
    resetDocumentStore();
    resetSolveOptionsStore();
    parkedSolveCommandStore.clear();
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
    parkedSolveCommandStore.clear();
    workspaceModeStore.setMode('parametric');
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
      if (path.endsWith('/solve-command')) return json({ command: null });
      return json({}, 404);
    }));

    await renderCoordinator();
    await act(async () => { await vi.advanceTimersByTimeAsync(7_500); });
    expect(calls).toEqual([]);

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(calls.filter((path) => path.endsWith('/returns'))).toHaveLength(1);
    expect(calls.filter((path) => path.endsWith('/fusion-status'))).toHaveLength(1);
    expect(calls.filter((path) => path.endsWith('/solve-command')).length).toBeGreaterThanOrEqual(1);
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
      if (path.endsWith('/solve-command')) return json({ command: null });
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

  it('runs a Fusion solve command once and reports an already-accepted one instead of resubmitting', async () => {
    const reported: unknown[] = [];
    let command: Record<string, unknown> | null = {
      commandId: 'cmd-1', returnId: 'wgr_1', bundlePath: initialBundle.bundlePath,
      manifestSha256: 'sha256:m', requestedAt: '2026-08-14T12:00:00Z',
    };
    let outcome: Record<string, unknown> | null = null;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/solve-command/outcome')) {
        reported.push(JSON.parse(String(init?.body)));
        return json({ state: 'accepted', cleared: true });
      }
      if (path.endsWith('/solve-command')) return json({ command, outcome });
      if (path.endsWith('/ingest')) return json(ingestRecord);
      return json({}, 404);
    }));
    // The real solve path retires the parked command with the job it created.
    const solveCurrentCadImport = vi.fn(async () => {
      await consumeParkedSolveCommand('job-1');
      return 'submitted' as const;
    });
    vi.spyOn(jobsCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...jobsCoordinatorBridge.getSnapshot(), solveCurrentCadImport,
    });

    await renderCoordinator();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(solveCurrentCadImport).toHaveBeenCalledOnce();
    expect(reported).toEqual([{ commandId: 'cmd-1', state: 'accepted', jobId: 'job-1', reason: null }]);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBe('Solving the model Fusion sent.');
    expect(parkedSolveCommandStore.getSnapshot().command).toBeNull();
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');

    // A terminal command surfaces its existing job; it never submits again.
    outcome = { state: 'accepted', jobId: 'job-7', reason: null, at: '' };
    command = { ...command, commandId: 'cmd-2' };
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(solveCurrentCadImport).toHaveBeenCalledOnce();
  });

  it('refuses a Fusion solve command from another CAD-linked project before ingesting it', async () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_current', lineageId: 'wgl_current', baseEditVersion: 2,
    }, 'current');
    const otherProject = { ...initialBundle, designIds: ['wgd_other'] };
    const reported: Array<Record<string, unknown>> = [];
    let ingestCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [otherProject] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/solve-command/outcome')) {
        reported.push(JSON.parse(String(init?.body)));
        return json({ state: 'refused', cleared: true });
      }
      if (path.endsWith('/solve-command')) return json({ command: {
        commandId: 'cmd-other', returnId: 'wgr_other', bundlePath: otherProject.bundlePath,
        manifestSha256: 'sha256:m', requestedAt: '2026-08-20T12:00:00Z',
      }, outcome: null });
      if (path.endsWith('/ingest')) { ingestCalls += 1; return json(ingestRecord); }
      return json({}, 404);
    }));

    await renderCoordinator();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(ingestCalls).toBe(0);
    expect(reported).toEqual([expect.objectContaining({
      commandId: 'cmd-other', state: 'refused',
      reason: expect.stringContaining('another CAD-linked project'),
    })]);
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toContain('another CAD-linked project');
  });

  /** Everything a Fusion "Solve in WG" needs: a listing, a marker whose value
   * the test controls, an ingest per command, and a viewport artifact — so the
   * gate in `cadSolveBlockerNow` runs against real store state. */
  const solveCommandHarness = (options: {
    ingests: CadReturnIngestRecord[];
    solve?: () => Promise<'submitted' | 'busy'>;
  }) => {
    const reported: Array<Record<string, unknown>> = [];
    const submitted: string[] = [];
    let pendingCommand: Record<string, unknown> | null = null;
    let ingestIndex = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/solve-command/outcome')) {
        reported.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        // The server retires the marker with a terminal outcome.
        pendingCommand = null;
        return json({ state: 'recorded', cleared: true });
      }
      if (path.endsWith('/solve-command')) return json({ command: pendingCommand, outcome: null });
      if (path.endsWith('/ingest')) {
        const record = options.ingests[Math.min(ingestIndex, options.ingests.length - 1)];
        ingestIndex += 1;
        return json(record);
      }
      if (path.includes('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return json({}, 404);
    }));
    const solveCurrentCadImport = vi.fn(options.solve ?? (async () => {
      // The production gate, not a stand-in: this is the assertion.
      const blocker = cadSolveBlockerNow();
      if (blocker) throw new Error(blocker);
      const record = useCadReturnStore.getState().ingestRecord;
      submitted.push(String(record?.ingest_id));
      await consumeParkedSolveCommand(`job-${submitted.length}`);
      return 'submitted' as const;
    }));
    vi.spyOn(jobsCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...jobsCoordinatorBridge.getSnapshot(), solveCurrentCadImport,
    });
    return {
      reported,
      submitted,
      solveCurrentCadImport,
      issue(commandId: string) { pendingCommand = {
        commandId, returnId: 'wgr_1', bundlePath: initialBundle.bundlePath,
        manifestSha256: `sha256:${commandId}`, requestedAt: '2026-08-18T12:00:00Z',
      }; },
    };
  };

  it('solves consecutive Fusion-initiated commands instead of refusing on the previous mesh', async () => {
    vi.useFakeTimers();
    const harness = solveCommandHarness({
      ingests: [
        { ...ingestRecord, ingest_id: 'wgi_first' },
        { ...ingestRecord, ingest_id: 'wgi_second' },
      ],
    });
    await renderCoordinator();

    harness.issue('cmd-1');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });
    expect(harness.submitted).toEqual(['wgi_first']);
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_first');

    // The second command ingests fresh geometry. Before the viewport display
    // was awaited, the gate still saw the first ingest's mesh and refused.
    harness.issue('cmd-2');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });
    expect(harness.submitted).toEqual(['wgi_first', 'wgi_second']);
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_second');
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toBeNull();
    expect(harness.reported).toEqual([
      { commandId: 'cmd-1', state: 'accepted', jobId: 'job-1', reason: null },
      { commandId: 'cmd-2', state: 'accepted', jobId: 'job-2', reason: null },
    ]);
  });

  it('parks a Fusion solve for settings review when its source inventory resets', async () => {
    vi.useFakeTimers();
    const resetIngest = { ...ingestRecord, ingest_id: 'wgi_reset_inventory' };
    const harness = solveCommandHarness({ ingests: [resetIngest] });
    await renderCoordinator();
    useCadReturnStore.getState().selectBundle({
      ...initialBundle,
      name: 'previous.wgreturn',
      bundlePath: 'wgreturn/previous.wgreturn',
      sourceCount: 2,
      sources: [
        ...initialBundle.sources,
        {
          id: 'source-mf', role: 'MF', required: true,
          suggestedResolutionMm: 8, defaultDriveChannelId: 'drive-mf',
        },
      ],
    });

    harness.issue('cmd-reset');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });

    expect(harness.solveCurrentCadImport).not.toHaveBeenCalled();
    expect(harness.submitted).toEqual([]);
    expect(harness.reported).toEqual([]);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_reset_inventory');
    expect(parkedSolveCommandStore.getSnapshot().command).toMatchObject({
      commandId: 'cmd-reset',
      bundlePath: initialBundle.bundlePath,
      blockers: ['Review the new source inventory and solve settings before solving.'],
    });
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('review mesh, channel, and solve settings');

    // Clicking Solve after reviewing the reset settings consumes this request.
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().solveParkedCommand(); });
    expect(harness.submitted).toEqual(['wgi_reset_inventory']);
    expect(harness.reported).toEqual([
      { commandId: 'cmd-reset', state: 'accepted', jobId: 'job-1', reason: null },
    ]);
    expect(parkedSolveCommandStore.getSnapshot().command).toBeNull();
  });

  it('drops a viewport mesh it could not replace rather than blocking the next solve', async () => {
    vi.useFakeTimers();
    const harness = solveCommandHarness({
      ingests: [
        { ...ingestRecord, ingest_id: 'wgi_first' },
        { ...ingestRecord, ingest_id: 'wgi_second' },
      ],
    });
    await renderCoordinator();
    harness.issue('cmd-1');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_first');

    // Both display artifacts fail for the second ingestion.
    const failing = vi.mocked(fetch).getMockImplementation()!;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => (
      String(input).includes('/viewport-mesh') || String(input).endsWith('/mesh')
        ? json({}, 500)
        : failing(input, init)
    )));
    harness.issue('cmd-2');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });

    expect(importedMeshStore.getSnapshot().cad).toBeNull();
    expect(harness.submitted).toEqual(['wgi_first', 'wgi_second']);
  });

  it('parks a gated Fusion command, consumes it on the next solve, and never replays it', async () => {
    vi.useFakeTimers();
    // A blocking finding no longer gates the solve; the FEM exterior-only
    // choice is a real gate a user has to make, so it is what parks here.
    const blocking: CadReturnIngestRecord = {
      ...ingestRecord,
      ingest_id: 'wgi_blocked',
      evidence: { fem_air_volumes: [{ required: true }] },
    };
    const harness = solveCommandHarness({ ingests: [blocking] });
    await renderCoordinator();

    harness.issue('cmd-1');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });

    // Parked, not discarded: nothing was submitted and nothing was reported.
    expect(harness.submitted).toEqual([]);
    expect(harness.reported).toEqual([]);
    expect(parkedSolveCommandStore.getSnapshot().command).toMatchObject({
      commandId: 'cmd-1',
      bundlePath: initialBundle.bundlePath,
      blockers: ['This return includes FEM air volumes. Explicitly choose an exterior-only Phase 2 solve.'],
    });
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');

    // The command sits still while it is parked; polling never re-submits it.
    await act(async () => { await vi.advanceTimersByTimeAsync(5_200); });
    expect(harness.solveCurrentCadImport).toHaveBeenCalledOnce();

    // Making the gated choice and solving consumes the very same request.
    await act(async () => {
      useCadReturnStore.getState().setExteriorOnly(true);
      await cadLinkCoordinatorBridge.getSnapshot().solveParkedCommand();
    });
    expect(harness.submitted).toEqual(['wgi_blocked']);
    expect(harness.reported).toEqual([
      { commandId: 'cmd-1', state: 'accepted', jobId: 'job-1', reason: null },
    ]);
    expect(parkedSolveCommandStore.getSnapshot().command).toBeNull();

    // Terminal on the server, so a later poll cannot solve it a second time.
    await act(async () => { await vi.advanceTimersByTimeAsync(5_200); });
    expect(harness.submitted).toEqual(['wgi_blocked']);
  });

  it('reports a dismissed command as refused so it cannot come back', async () => {
    vi.useFakeTimers();
    const blocking: CadReturnIngestRecord = {
      ...ingestRecord,
      evidence: { fem_air_volumes: [{ required: true }] },
    };
    const harness = solveCommandHarness({ ingests: [blocking] });
    await renderCoordinator();
    harness.issue('cmd-1');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });
    expect(parkedSolveCommandStore.getSnapshot().command?.commandId).toBe('cmd-1');

    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().dismissSolveCommand(); });

    expect(harness.reported).toEqual([{
      commandId: 'cmd-1',
      state: 'refused',
      jobId: null,
      reason: 'Dismissed in Waveguide Generator without solving.',
    }]);
    expect(parkedSolveCommandStore.getSnapshot().command).toBeNull();
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('will not be offered again');

    await act(async () => { await vi.advanceTimersByTimeAsync(5_200); });
    expect(harness.submitted).toEqual([]);
  });

  it('refuses a Fusion command outright when the machine has no Metal engine', async () => {
    vi.useFakeTimers();
    const harness = solveCommandHarness({
      ingests: [ingestRecord],
      solve: async () => { throw new SolveEngineUnavailableError('Metal engine is unavailable'); },
    });
    await renderCoordinator();
    harness.issue('cmd-1');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });

    expect(harness.reported).toEqual([{
      commandId: 'cmd-1', state: 'refused', jobId: null, reason: 'Metal engine is unavailable',
    }]);
    expect(parkedSolveCommandStore.getSnapshot().command).toBeNull();
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toContain('Metal engine is unavailable');

    await act(async () => { await vi.advanceTimersByTimeAsync(5_200); });
    expect(harness.solveCurrentCadImport).toHaveBeenCalledOnce();
  });

  it('detects, selects, and automatically ingests a newly arrived return', async () => {
    let listing = { cadFolderConfigured: true, items: [initialBundle] };
    const ingestBodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/solve-command')) return json({ command: null });
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
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain(ingestRecord.ingest_id);
    // The status above renders only inside the CAD Link panel, which exists
    // only in CAD mode — so the arrival has to enter it to be visible at all.
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).toHaveBeenCalledWith('cadlink');
  });

  it('does not double-ingest an arrival while its solve command is being consumed', async () => {
    const solveResponse = deferred<Response>();
    let listing = { cadFolderConfigured: true, items: [initialBundle] };
    let ingestCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/solve-command')) return solveResponse.promise;
      if (path.endsWith('/ingest')) { ingestCalls += 1; return json(ingestRecord); }
      if (path.endsWith('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return json({}, 404);
    }));
    const solveCurrentCadImport = vi.fn(async () => 'busy' as const);
    vi.spyOn(jobsCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...jobsCoordinatorBridge.getSnapshot(), solveCurrentCadImport,
    });
    await renderCoordinator();

    const arrived = { ...initialBundle, modifiedAt: '2026-08-13T12:00:00Z', documentName: 'Command arrival' };
    listing = { cadFolderConfigured: true, items: [arrived] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });
    expect(ingestCalls).toBe(0);

    solveResponse.resolve(json({
      command: {
        commandId: 'cmd-arrival', returnId: 'wgr_arrival', bundlePath: arrived.bundlePath,
        manifestSha256: 'sha256:arrival', requestedAt: '2026-08-13T12:00:00Z',
      },
      outcome: null,
    }));
    await act(async () => {
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(ingestCalls).toBe(1);
    expect(solveCurrentCadImport).toHaveBeenCalledOnce();
    expect(parkedSolveCommandStore.getSnapshot().command?.commandId).toBe('cmd-arrival');
  });

  it('does not auto-ingest an arrival already owned by a parked solve command', async () => {
    let listing = { cadFolderConfigured: true, items: [initialBundle] };
    let ingestCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/solve-command')) return json({ command: null });
      if (path.endsWith('/ingest')) { ingestCalls += 1; return json(ingestRecord); }
      return json({}, 404);
    }));
    await renderCoordinator();
    const arrived = { ...initialBundle, modifiedAt: '2026-08-13T12:00:00Z' };
    parkedSolveCommandStore.park({
      commandId: 'cmd-parked', bundlePath: arrived.bundlePath, blockers: ['review settings'],
      parkedAt: '2026-08-13T12:00:00Z',
    });
    listing = { cadFolderConfigured: true, items: [arrived] };

    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });

    expect(ingestCalls).toBe(0);
    expect(parkedSolveCommandStore.getSnapshot().command?.commandId).toBe('cmd-parked');
  });

  it('retires a parked solve command when a different newer return arrives', async () => {
    let listing = { cadFolderConfigured: true, items: [initialBundle] };
    const reported: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/solve-command/outcome')) {
        reported.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return json({ state: 'refused', cleared: true });
      }
      if (path.endsWith('/solve-command')) return json({ command: null });
      if (path.endsWith('/ingest')) return json({ ...ingestRecord, ingest_id: 'wgi_after_superseded_command' });
      if (path.endsWith('/viewport-mesh')) return new Response(viewportMesh, { status: 200 });
      return json({}, 404);
    }));
    await renderCoordinator();
    parkedSolveCommandStore.park({
      commandId: 'cmd-old', bundlePath: initialBundle.bundlePath, blockers: ['review settings'],
      parkedAt: '2026-08-12T00:00:00Z',
    });
    const arrived = {
      ...initialBundle,
      name: 'newer.wgreturn', bundlePath: 'wgreturn/newer.wgreturn',
      documentName: 'Newer return', modifiedAt: '2026-08-13T12:00:00Z',
    };
    listing = { cadFolderConfigured: true, items: [arrived, initialBundle] };

    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(reported).toEqual([{
      commandId: 'cmd-old', state: 'refused', jobId: null,
      reason: 'Superseded by a newer return from Fusion.',
    }]);
    expect(parkedSolveCommandStore.getSnapshot().command).toBeNull();
    expect(useCadReturnStore.getState().selectedBundle).toEqual(arrived);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_after_superseded_command');
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
      if (path.endsWith('/solve-command')) return json({ command: null });
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
      if (path.endsWith('/solve-command')) return json({ command: null });
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
      if (path.endsWith('/solve-command')) return json({ command: null });
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
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('wgi_newest_listing');
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
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('wgi_newer');
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
      cad.setChannelDriverEnabled('custom-hf', true);
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
      if (path.endsWith('/solve-command')) return json({ command: null });
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

    let shown = false;
    await act(async () => { shown = await showCadJobModel(job); });

    const state = useCadReturnStore.getState();
    expect(shown).toBe(true);
    expect(state.driveChannels).toEqual(job.cad_setup?.drive_channels?.map(({ driver: _driver, ...channel }) => channel));
    expect(state.channelDrivers['drive-hf']).toEqual({
      enabled: true,
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
      if (path.endsWith('/solve-command')) return json({ command: null });
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
      if (path.endsWith('/solve-command')) return json({ command: null });
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
});
