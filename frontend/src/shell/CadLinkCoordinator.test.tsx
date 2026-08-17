import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord, FusionCadStatus } from '../api/cadlink';
import { importedSubmissionBlocker } from '../jobs/importedSubmission';
import { preferencesStore } from '../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { CadLinkCoordinator, cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import { jobsCoordinatorBridge } from './JobsCoordinator';
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
    workspaceModeStore.setMode('parametric');
    preferencesStore.resetForTests();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    importedMeshStore.clear();
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
    let listing: { items: CadReturnBundle[] } = { items: [] };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(linkedFusion);
      if (path.endsWith('/request-fusion-return')) {
        return json({ status: 'requested', requestId: 'req_1', documentName: 'Speaker' });
      }
      return json({}, 404);
    }));
    await renderCoordinator();

    let pull!: Promise<CadReturnBundle>;
    await act(async () => {
      pull = cadLinkCoordinatorBridge.getSnapshot().pullFromFusion();
      await Promise.resolve(); await Promise.resolve();
    });
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('Waiting for Fusion…');

    // An uncorrelated bundle must not settle this pull.
    listing = { items: [{ ...initialBundle, requestId: 'req_other' }] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
    });

    const correlated = { ...initialBundle, requestId: 'req_1', documentName: 'Speaker pulled' };
    listing = { items: [correlated] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
    });
    await expect(pull).resolves.toMatchObject({ requestId: 'req_1' });
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
      if (path.endsWith('/returns')) return json({ items: [] });
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
    let listing: { items: CadReturnBundle[] } = { items: [] };
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
      throw new Error('Acknowledge 1 blocking finding before solving.');
    });
    vi.spyOn(jobsCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...jobsCoordinatorBridge.getSnapshot(), solveCurrentCadImport,
    });
    await renderCoordinator();

    const arrive = async () => {
      listing = { items: [{ ...initialBundle, requestId: 'req_1', modifiedAt: '2026-08-13T12:00:00Z' }] };
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
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toContain('Acknowledge 1 blocking finding');

    // With the gate satisfied the same chain reaches the solve.
    solveCurrentCadImport.mockImplementation(async () => 'submitted' as never);
    listing = { items: [] };
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
      if (path.endsWith('/returns')) return json({ items: [initialBundle] });
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/solve-command/outcome')) {
        reported.push(JSON.parse(String(init?.body)));
        return json({ state: 'accepted', cleared: true });
      }
      if (path.endsWith('/solve-command')) return json({ command, outcome });
      if (path.endsWith('/ingest')) return json(ingestRecord);
      return json({}, 404);
    }));
    const solveCurrentCadImport = vi.fn(async () => 'submitted' as const);
    vi.spyOn(jobsCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...jobsCoordinatorBridge.getSnapshot(), solveCurrentCadImport,
    });

    await renderCoordinator();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(solveCurrentCadImport).toHaveBeenCalledOnce();
    expect(reported).toEqual([{ commandId: 'cmd-1', state: 'accepted' }]);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBe('Solving the model Fusion sent.');

    // A terminal command surfaces its existing job; it never submits again.
    outcome = { state: 'accepted', jobId: 'job-7', reason: null, at: '' };
    command = { ...command, commandId: 'cmd-2' };
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(solveCurrentCadImport).toHaveBeenCalledOnce();
  });

  it('detects and auto-selects a newly arrived return', async () => {
    let listing = { items: [initialBundle] };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({}, 404);
    }));
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    await renderCoordinator();
    expect(useCadReturnStore.getState().selectedBundle).toEqual(initialBundle);

    const arrived = { ...initialBundle, modifiedAt: '2026-08-13T12:00:00Z', documentName: 'Speaker rebuilt' };
    listing = { items: [arrived] };
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });

    expect(useCadReturnStore.getState().selectedBundle).toEqual(arrived);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBe(
      'Received Speaker rebuilt from Fusion 360. Kept your mesh, channel, and solve settings.',
    );
    expect(activate).toHaveBeenCalledWith('cadlink');
  });

  it('ignores an older return listing that finishes after a newer refresh', async () => {
    const olderResponse = deferred<Response>();
    const newerResponse = deferred<Response>();
    let listingRequest = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) {
        listingRequest += 1;
        if (listingRequest === 1) return json({ items: [initialBundle] });
        return listingRequest === 2 ? olderResponse.promise : newerResponse.promise;
      }
      if (path.endsWith('/fusion-status')) return json(closedFusion);
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
      newerResponse.resolve(json({ items: [newer] }));
      await newerRefresh;
    });
    await act(async () => {
      olderResponse.resolve(json({ items: [older] }));
      await olderRefresh;
    });

    expect(cadLinkCoordinatorBridge.getSnapshot().bundles).toEqual([newer]);
    expect(useCadReturnStore.getState().selectedBundle).toEqual(newer);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toBe(
      'Received Newest listing from Fusion 360. Kept your mesh, channel, and solve settings.',
    );
  });

  it('marks an ingestion stale while the CAD Link panel is unmounted', async () => {
    let listing = { items: [initialBundle] };
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

    listing = { items: [{
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
      if (path.endsWith('/returns')) return json({ items: [initialBundle] });
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
      if (path.endsWith('/returns')) return json({ items: [initialBundle] });
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
      if (path.endsWith('/returns')) return json({ items: [] });
      if (path.endsWith('/fusion-status')) return json(bothChanged);
      return json({}, 404);
    }));
    await renderCoordinator();
    await act(async () => { await Promise.resolve(); });

    let parked: unknown = 'unset';
    await act(async () => { parked = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion(); });
    expect(parked).toBeNull();
    expect(cadLinkCoordinatorBridge.getSnapshot().pendingFusionConflict).toBe(true);
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('Both WG and Fusion changed');
    expect(exportBodies).toHaveLength(0);

    // Cancel clears without sending.
    await act(async () => { cadLinkCoordinatorBridge.getSnapshot().cancelFusionConflict(); });
    expect(cadLinkCoordinatorBridge.getSnapshot().pendingFusionConflict).toBe(false);
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(exportBodies).toHaveLength(0);

    // Confirm sends one update carrying the expected-document guard.
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion(); });
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion({ confirmed: true }); });
    expect(exportBodies).toHaveLength(1);
    expect(exportBodies[0]).toMatchObject({
      expectedFusionDocumentId: 'fusion:doc-1',
      expectedFusionReturnStateHash: 'sha256:doc-state',
    });
    expect(cadLinkCoordinatorBridge.getSnapshot().pendingFusionConflict).toBe(false);
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
      if (path.endsWith('/returns')) return json({ items: [initialBundle] });
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
      if (path.endsWith('/returns')) return json({ items: [initialBundle] });
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
      cad.acknowledge('reviewed-finding', true);
      workspaceModeStore.setMode('cad');
    });

    expect(importedSubmissionBlocker()).toBeNull();
    const retainedBundle = useCadReturnStore.getState().selectedBundle;
    const retainedRecord = useCadReturnStore.getState().ingestRecord;
    const retainedChannels = useCadReturnStore.getState().driveChannels;
    const retainedDrivers = useCadReturnStore.getState().channelDrivers;
    const retainedCrossovers = useCadReturnStore.getState().combineCrossoversHz;
    const retainedAcknowledgements = useCadReturnStore.getState().acknowledgedFindingIds;

    act(replace);

    const state = useCadReturnStore.getState();
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(state.selectedBundle).toBe(retainedBundle);
    expect(state.ingestRecord).toBe(retainedRecord);
    expect(state.driveChannels).toBe(retainedChannels);
    expect(state.channelDrivers).toBe(retainedDrivers);
    expect(state.combineCrossoversHz).toBe(retainedCrossovers);
    expect(state.acknowledgedFindingIds).toBe(retainedAcknowledgements);
    expect(state.needsIngest).toBe(true);
    expect(state.ingestStaleReason).toContain('design was replaced');
    expect(importedSubmissionBlocker()).toBe(state.ingestStaleReason);
  });
});
