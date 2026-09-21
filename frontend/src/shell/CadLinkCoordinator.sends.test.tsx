/**
 * An accepted Send is displayed from its event (M1 transfer contract, C4, E2).
 *
 * The backend only retains a `receive_snapshot`; the page ingests and shows it
 * when the accepted operation arrives on the jobs channel. The listing is read
 * once for it, and its own record of what it has seen keeps one arrival from
 * being displayed twice. A later Send with a new id naming the same bundle
 * re-selects it and brings it to the front. A refused Send is said.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle, FusionCadStatus } from '../api/cadlink';
import type { CadOperationSummary } from '../api/cadOperations';
import { resetCadCoordinationForTests } from '../api/cadCoordination';
import { preferencesStore } from '../prefs/preferences';
import { recoverMissedSnapshots, resetCadOperationsStore, useCadOperationsStore } from '../stores/cadOperations';
import { resetCadPreparationStore } from '../stores/cadPreparation';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { currentDocumentLoad, resetDesignStore, seedDesign, useDesignStore } from '../stores/design';
import { keptContentKeyNow, replacingWouldLoseNow } from '../design/replacementCheck';
import { resetDocumentStore } from '../stores/document';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { CadLinkCoordinator, cadLinkCoordinatorBridge, resetCadPollIntervals } from './CadLinkCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

const bundle = (overrides: Partial<CadReturnBundle> = {}): CadReturnBundle => ({
  name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', modifiedAt: '2026-09-21T10:00:00Z',
  readable: true, documentName: 'Speaker', requestId: null, sourceCount: 1, instanceCount: 1, designIds: [],
  sources: [{ id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-hf' }],
  ...overrides,
});

const closedFusion = {
  cadApplication: 'fusion360', cadFolderConfigured: true, cadFolderPath: '/workspace', state: 'closed',
  processRunning: false, running: false, updatedAt: null, documentName: null, documentId: null,
  currentFormula: 'OSSE', fusionFormula: null, link: null, wgChangesAvailable: false,
  fusionChangesAvailable: false, documentChanged: false, documentChangeDetectable: false,
  staleDetectionExplanation: null,
  realizedDimensions: { state: 'link_unavailable', instanceId: null, exportId: null, parameters: [] },
} as unknown as FusionCadStatus;

const ingestRecord = {
  ingest_id: 'wgi_sent', created_at: '', return_id: '',
  manifest_sha256: `sha256:${'1'.repeat(64)}`, artifact_sha256: `sha256:${'2'.repeat(64)}`, report_sha256: `sha256:${'3'.repeat(64)}`,
  acoustic_domain: 'free-space', scope: { status: 'clean', degraded_skip_count: 0 },
  sources: [{ id: 'source-hf', role: 'HF', required: true, instance_id: null, default_drive_channel_id: 'drive-hf', suggested_resolution_mm: 4 }],
  mesh_sizes: { rigid_size_mm: 4, transition_mm: 4, source_size_mm: { 'source-hf': 4 } },
  skipped_source_ids: [], freshness: { verdict: 'per-instance', instances: [] }, findings: [],
  symmetry: { planes: {}, cut_planes: [] }, healing: { performed: false, mode: 'none' },
  sizing_estimate: {}, polar_grid_derivation: {}, tag_map: {},
};

const mesh = [
  '$MeshFormat', '2.2 0 8', '$EndMeshFormat', '$Nodes', '3', '1 0 0 0', '2 1 0 0', '3 0 1 0', '$EndNodes',
  '$Elements', '1', '1 2 2 1 1 1 2 3', '$EndElements', '',
].join('\n');

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function send(operationId: string, target: CadReturnBundle, overrides: Partial<CadOperationSummary> = {}): CadOperationSummary {
  return {
    operationId, kind: 'receive_snapshot', state: 'accepted', stage: null, reason: null,
    message: 'Received the snapshot.', jobId: null, attemptGeneration: 1, setupRevisionId: null, preparationId: null,
    snapshot: { manifestSha256: 'sha256:m', documentName: target.documentName, bundlePath: target.bundlePath },
    legacy: false, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

const flush = async () => { for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };

describe('an accepted Send is displayed from its event', () => {
  let host: HTMLDivElement;
  let root: Root;
  let listing: CadReturnBundle[];
  let ingests: string[];
  let listings: number;

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetCadReturnStore();
    resetCadPreparationStore();
    resetDesignStore();
    resetDocumentStore();
    resetSolveOptionsStore();
    resetCadOperationsStore();
    resetCadCoordinationForTests();
    preferencesStore.resetForTests();
    sessionStorage.clear();
    workspaceModeStore.setMode('parametric');
    listing = [];
    ingests = [];
    listings = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      // The coordination gate off: no listing runs on a clock, so every
      // listing here is an event's.
      if (path.endsWith('/returns')) { listings += 1; return json({ cadFolderConfigured: true, items: listing, coordination: 'off' }); }
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.endsWith('/ingest')) { ingests.push(JSON.parse(String(init?.body)).bundlePath); return json(ingestRecord); }
      if (path.endsWith('/viewport-mesh')) return new Response(mesh, { status: 200 });
      if (path.startsWith('/api/cadlink/operations')) return json({ operations: [] });
      return json({}, 404);
    }));
    vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    await act(async () => { root.render(<CadLinkCoordinator/>); await flush(); });
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    importedMeshStore.clear();
    resetCadOperationsStore();
    resetCadCoordinationForTests();
    workspaceModeStore.setMode('parametric');
    resetCadPollIntervals();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const deliver = async (operation: CadOperationSummary) => {
    await act(async () => { useCadOperationsStore.getState().apply(operation); await flush(); });
  };

  it('ingests and shows a new Send once, even when the listing then sees the same arrival', async () => {
    const arrived = bundle({ modifiedAt: '2026-09-21T12:00:00Z' });
    listing = [arrived];
    const before = listings;
    await deliver(send('send-1', arrived));

    expect(listings - before).toBeGreaterThanOrEqual(1);
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(arrived.bundlePath);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_sent');
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(ingests).toEqual([arrived.bundlePath]);

    // The listing's own sighting of the same arrival (gate on, or focus).
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await flush();
    });
    expect(ingests).toEqual([arrived.bundlePath]);
    // And the same event delivered again is not a second display.
    await deliver(send('send-1', arrived, { updatedAt: new Date(Date.now() + 1000).toISOString() }));
    expect(ingests).toEqual([arrived.bundlePath]);
  });

  it('brings a re-Send of the same model back to the front with a new id', async () => {
    const arrived = bundle({ modifiedAt: '2026-09-21T12:00:00Z' });
    const other = bundle({ name: 'other.wgreturn', bundlePath: 'wgreturn/other.wgreturn', documentName: 'Other' });
    listing = [arrived, other];
    await deliver(send('send-1', arrived));
    // The user moves on: another return, then Parametric mode.
    await act(async () => { cadLinkCoordinatorBridge.getSnapshot().selectBundle(other); await flush(); });
    act(() => workspaceModeStore.setMode('parametric'));
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(other.bundlePath);

    await deliver(send('send-2', arrived, { createdAt: new Date(Date.now() + 60_000).toISOString() }));

    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(arrived.bundlePath);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
  });

  it('keeps a return the user picked after the Send was made', async () => {
    const arrived = bundle({ modifiedAt: '2026-09-21T12:00:00Z' });
    const other = bundle({ name: 'other.wgreturn', bundlePath: 'wgreturn/other.wgreturn', documentName: 'Other' });
    listing = [other];
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().refresh({ background: true }); await flush(); });
    await act(async () => { cadLinkCoordinatorBridge.getSnapshot().selectBundle(other); await flush(); });
    // Sent, and written to the folder, a minute before the pick.
    const sentAt = new Date(Date.now() - 60_000).toISOString();
    listing = [other, { ...arrived, modifiedAt: sentAt }];

    await deliver(send('send-old', arrived, { createdAt: sentAt }));

    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(other.bundlePath);
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('stays selected');
  });

  it('says so when WG refused a Send, and does not drop it silently', async () => {
    const arrived = bundle();
    await deliver(send('send-bad', arrived, {
      state: 'rejected', reason: 'snapshot_invalid', message: 'The return in the WGLink folder is not the one Fusion named.',
    }));
    expect(cadLinkCoordinatorBridge.getSnapshot().error).toContain('not the one Fusion named');
    expect(ingests).toEqual([]);
  });

  it('does not re-list for every other operation update once a Send is shown', async () => {
    const arrived = bundle({ modifiedAt: '2026-09-21T12:00:00Z' });
    listing = [arrived];
    await deliver(send('send-1', arrived));
    const after = listings;
    for (let index = 0; index < 5; index += 1) {
      await deliver({ ...send(`op-${index}`, arrived), kind: 'prepare_and_solve', state: 'processing' });
    }
    expect(listings).toBe(after);
  });

  it('still shows a Send once per event stream when the tab cannot store its record', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    const arrived = bundle({ modifiedAt: '2026-09-21T12:00:00Z' });
    listing = [arrived];
    await deliver(send('send-1', arrived));
    const after = listings;
    await deliver({ ...send('op-other', arrived), kind: 'prepare_and_solve', state: 'processing' });
    await deliver({ ...send('op-other', arrived), kind: 'prepare_and_solve', state: 'processing', updatedAt: new Date(Date.now() + 1_000).toISOString() });
    expect(listings).toBe(after);
    setItem.mockRestore();
  });

  it('brings a re-Send of the model already selected back to the front', async () => {
    const arrived = bundle({ modifiedAt: '2026-09-21T12:00:00Z' });
    listing = [arrived];
    await deliver(send('send-1', arrived));
    act(() => workspaceModeStore.setMode('parametric'));
    await deliver(send('send-2', arrived));
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(arrived.bundlePath);
  });

  it('never shows a Send twice across a reload', async () => {
    const arrived = bundle({ modifiedAt: '2026-09-21T12:00:00Z' });
    listing = [arrived];
    await deliver(send('send-1', arrived));
    expect(ingests).toHaveLength(1);
    // The page reloads; its recovery hands the same accepted Send back.
    act(() => root.unmount());
    resetCadReturnStore();
    act(() => workspaceModeStore.setMode('parametric'));
    root = createRoot(host);
    await act(async () => { root.render(<CadLinkCoordinator/>); await flush(); });
    await deliver(send('send-1', arrived, { updatedAt: new Date(Date.now() + 5_000).toISOString() }));
    expect(ingests).toHaveLength(1);
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
  });

  it('says so when an accepted Send is no longer in the WGLink folder', async () => {
    listing = [];
    await deliver(send('send-gone', bundle()));
    expect(cadLinkCoordinatorBridge.getSnapshot().status).toContain('no longer in the WGLink folder');
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(ingests).toEqual([]);
  });

  it.each(['newest first', 'oldest first'])('recovering two Sends %s leaves the newest one displayed (review F3)', async (order) => {
    const old = bundle({ name: 'old.wgreturn', bundlePath: 'wgreturn/old.wgreturn', documentName: 'Old', modifiedAt: new Date(Date.now() - 60_000).toISOString() });
    const newest = bundle({ name: 'new.wgreturn', bundlePath: 'wgreturn/new.wgreturn', documentName: 'New', modifiedAt: new Date(Date.now() - 30_000).toISOString() });
    listing = [newest, old];
    const sends = [
      send('new-send', newest, { createdAt: newest.modifiedAt, updatedAt: newest.modifiedAt }),
      send('old-send', old, { createdAt: old.modifiedAt, updatedAt: old.modifiedAt }),
    ];
    if (order === 'oldest first') sends.reverse();
    await act(async () => {
      sends.forEach((item) => useCadOperationsStore.getState().apply(item));
      await flush();
    });
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(newest.bundlePath);
  });

  it('uses acceptance sequence when recovered Sends have equal whole-second timestamps', async () => {
    const tiedAt = new Date(Date.now() - 30_000).toISOString().replace(/\.\d{3}Z$/, 'Z');
    const old = bundle({ name: 'old.wgreturn', bundlePath: 'wgreturn/old.wgreturn', documentName: 'Old' });
    const newest = bundle({ name: 'new.wgreturn', bundlePath: 'wgreturn/new.wgreturn', documentName: 'New' });
    listing = [newest, old];
    await act(async () => {
      useCadOperationsStore.getState().apply(send('new-send', newest, { acceptedSeq: 42, createdAt: tiedAt, updatedAt: tiedAt }));
      useCadOperationsStore.getState().apply(send('old-send', old, { acceptedSeq: 41, createdAt: tiedAt, updatedAt: tiedAt }));
      await flush();
    });
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(newest.bundlePath);
  });

  it('keeps the first recovered Send on a tied timestamp when acceptance sequence is absent', async () => {
    const tiedAt = new Date(Date.now() - 30_000).toISOString().replace(/\.\d{3}Z$/, 'Z');
    const old = bundle({ name: 'old.wgreturn', bundlePath: 'wgreturn/old.wgreturn', documentName: 'Old' });
    const newest = bundle({ name: 'new.wgreturn', bundlePath: 'wgreturn/new.wgreturn', documentName: 'New' });
    listing = [newest, old];
    // Recovery applies the server's newest-first listing in order. Older
    // payloads have no acceptedSeq, so the first item owns an exact tie.
    await act(async () => {
      useCadOperationsStore.getState().apply(send('new-send', newest, { createdAt: tiedAt, updatedAt: tiedAt }));
      useCadOperationsStore.getState().apply(send('old-send', old, { createdAt: tiedAt, updatedAt: tiedAt }));
      await flush();
    });
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(newest.bundlePath);
  });

  it('never replaces a return the user selects while a Send is being displayed', async () => {
    const arrived = bundle({ modifiedAt: new Date(Date.now() - 5_000).toISOString() });
    const picked = bundle({ name: 'picked.wgreturn', bundlePath: 'wgreturn/picked.wgreturn', documentName: 'Picked' });
    listing = [picked];  // the Send's return is not listed yet on the first read
    await act(async () => {
      useCadOperationsStore.getState().apply(send('send-slow', arrived, { createdAt: arrived.modifiedAt }));
      // The user picks a return while the display is still reading.
      cadLinkCoordinatorBridge.getSnapshot().selectBundle(picked);
      listing = [picked, arrived];
      await flush();
    });
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(picked.bundlePath);
  });

  it('never replaces a return the user selects while the display is reading the folder', async () => {
    const arrived = bundle({ modifiedAt: new Date(Date.now() - 5_000).toISOString() });
    const picked = bundle({ name: 'picked.wgreturn', bundlePath: 'wgreturn/picked.wgreturn', documentName: 'Picked' });
    // The display's reads before its own second listing do not find its
    // return (its refresh, and entering CAD mode, each read once); that second
    // listing does, and the user picks another return while it is on its way.
    let returnsReads = 0;
    const real = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/returns')) {
        returnsReads += 1;
        listings += 1;
        if (returnsReads === 3) {
          act(() => { cadLinkCoordinatorBridge.getSnapshot().selectBundle(picked); });
          return json({ cadFolderConfigured: true, items: [picked, arrived], coordination: 'off' });
        }
        return json({ cadFolderConfigured: true, items: [picked], coordination: 'off' });
      }
      return real(input, init);
    }));
    await deliver(send('send-slow', arrived, { createdAt: arrived.modifiedAt }));
    expect(returnsReads).toBe(3);
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(picked.bundlePath);
  });

  it('does not record displaying a Send as the user\'s own pick', async () => {
    const first = bundle({ name: 'a.wgreturn', bundlePath: 'wgreturn/a.wgreturn', documentName: 'A' });
    const second = bundle({ name: 'b.wgreturn', bundlePath: 'wgreturn/b.wgreturn', documentName: 'B' });
    listing = [second, first];
    const t0 = Date.now();
    await deliver(send('send-a', first, { createdAt: new Date(t0 - 10_000).toISOString() }));
    // Made after A was sent but before A was displayed: newer than A, so it is shown.
    await deliver(send('send-b', second, { createdAt: new Date(t0 - 5_000).toISOString() }));
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(second.bundlePath);
    expect(cadLinkCoordinatorBridge.getSnapshot().status ?? '').not.toContain('stays selected');
  });

  // A Send is displayed in CAD mode and never written into the parametric
  // design: M1 removed the auto-open that replaced it (review A8, brief 8).
  const parametricState = () => {
    const history = useDesignStore.temporal.getState();
    return {
      design: JSON.stringify(useDesignStore.getState().design),
      revision: useDesignStore.getState().designRevision,
      past: history.pastStates.length,
      future: history.futureStates.length,
      kept: keptContentKeyNow(),
      wouldLose: replacingWouldLoseNow(),
      documentLoad: currentDocumentLoad(),
    };
  };

  const editTheParametricDesign = () => {
    act(() => {
      useDesignStore.getState().updateValue('length', 180);
      useDesignStore.getState().updateValue('length', 190);
      useDesignStore.getState().undo();  // one step back, so redo holds one too
    });
    const state = parametricState();
    expect(state.past).toBeGreaterThan(0);
    expect(state.future).toBeGreaterThan(0);
    expect(state.wouldLose).toBe(true);  // unsaved work a replacement would lose
    return state;
  };

  it('displaying a new Send leaves the parametric design and its undo history as they were', async () => {
    const before = editTheParametricDesign();
    const arrived = bundle({ modifiedAt: '2026-09-21T12:00:00Z' });
    listing = [arrived];

    await deliver(send('send-1', arrived));
    await deliver(send('send-2', arrived, { createdAt: new Date(Date.now() + 60_000).toISOString() }));

    expect(ingests).toEqual([arrived.bundlePath]);  // it was displayed
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(parametricState()).toEqual(before);
    act(() => workspaceModeStore.setMode('parametric'));
    expect(parametricState()).toEqual(before);
  });

  it('displaying a recovered Send leaves the parametric design and its undo history as they were', async () => {
    const before = editTheParametricDesign();
    const arrived = bundle({ modifiedAt: new Date(Date.now() - 30_000).toISOString() });
    listing = [arrived];
    const recovered = send('send-missed', arrived, { createdAt: arrived.modifiedAt, updatedAt: arrived.modifiedAt });
    const api = vi.fn(async (input: RequestInfo | URL) => (
      String(input).startsWith('/api/cadlink/operations') ? json({ operations: [recovered] }) : json({}, 404)
    )) as unknown as typeof fetch;

    await act(async () => { await recoverMissedSnapshots(Date.now() - 120_000, api); await flush(); });

    expect(ingests).toEqual([arrived.bundlePath]);  // recovery displayed it
    expect(parametricState()).toEqual(before);
  });

  it('a CAD project replacing the design moves every one of those measurements (their control)', () => {
    const before = editTheParametricDesign();
    act(() => {
      useDesignStore.getState().replaceDesign(
        { ...structuredClone(seedDesign), length: 240 } as typeof seedDesign,
        { loadSource: 'cad-project-switch' },
      );
    });
    const after = parametricState();
    expect(after.design).not.toBe(before.design);
    expect(after.revision).not.toBe(before.revision);
    expect(after.past).toBe(0);
    expect(after.future).toBe(0);
    expect(after.kept).not.toBe(before.kept);
    expect(after.documentLoad).not.toBe(before.documentLoad);
  });

  it('leaves a Send that is still being received alone (the control for every display above)', async () => {
    listing = [bundle()];
    await deliver(send('send-pending', bundle(), { state: 'received' }));
    expect(ingests).toEqual([]);
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
  });
});
