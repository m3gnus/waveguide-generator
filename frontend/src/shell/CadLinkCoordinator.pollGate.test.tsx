/**
 * WG's CAD coordination gate (M1 contract, C7 and C8).
 *
 * Explicit on keeps the returns listing and the Fusion-status read on their
 * adaptive clocks. Off is the default: neither runs
 * unconditionally; they run while CAD work is in flight and on explicit
 * events, and operation changes arrive as pushes on the jobs socket.
 *
 * Every zero here has a positive control measured the same way: the count of
 * returns-listing and Fusion-status requests the page made.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FusionCadStatus } from '../api/cadlink';
import type { CadOperationSummary } from '../api/cadOperations';
import { cadCoordinationOff, resetCadCoordinationForTests } from '../api/cadCoordination';
import { preferencesStore } from '../prefs/preferences';
import { resetCadOperationsStore, useCadOperationsStore } from '../stores/cadOperations';
import { resetCadPreparationStore } from '../stores/cadPreparation';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore } from '../stores/document';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { CadLinkCoordinator, cadLinkCoordinatorBridge, resetCadPollIntervals } from './CadLinkCoordinator';
import { cadSourceLine } from './TopBar';

/** Fusion open with a linked document: under the gate's "on" rules, a reason
 * to poll at the base rate for as long as it stays open. */
const fusionOpen = {
  cadApplication: 'fusion360', cadFolderConfigured: true, cadFolderPath: '/workspace',
  state: 'current', processRunning: true, running: true, updatedAt: null,
  documentName: 'Speaker', documentId: 'fusion:doc-1', currentFormula: 'OSSE', fusionFormula: 'OSSE',
  link: null, wgChangesAvailable: false, fusionChangesAvailable: false, documentChanged: false,
  documentChangeDetectable: true, staleDetectionExplanation: null, observationFreshness: 'current',
  // The M1 add-in: it sends through WG's request inbox and says so.
  addinInboxTransfer: true, observationPolicy: 'command', statusTtlSeconds: 20,
  realizedDimensions: { state: 'link_unavailable', instanceId: null, exportId: null, parameters: [] },
} as unknown as FusionCadStatus;

const TEN_MINUTES = 10 * 60_000;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function pending(operationId: string, state = 'processing', updatedAt = '2026-09-21T10:00:00Z'): CadOperationSummary {
  return {
    operationId, kind: 'prepare_and_solve', state, stage: 'validating', reason: null, message: null,
    jobId: state === 'accepted' ? 'job-1' : null, attemptGeneration: 1, setupRevisionId: null, preparationId: null,
    snapshot: null, legacy: false, createdAt: '2026-09-21T10:00:00Z', updatedAt,
  };
}

describe('WG CAD coordination gate', () => {
  let host: HTMLDivElement;
  let root: Root;
  let calls: string[];
  let gate: string | null;
  let statusAnswer: FusionCadStatus;

  const polls = () => calls.filter((path) => path.endsWith('/api/cadlink/returns') || path.endsWith('/api/cadlink/fusion-status')).length;
  const operationReads = () => calls.filter((path) => path.startsWith('/api/cadlink/operations')).length;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    resetCadReturnStore();
    resetCadPreparationStore();
    resetDesignStore();
    resetDocumentStore();
    resetSolveOptionsStore();
    resetCadOperationsStore();
    resetCadCoordinationForTests();
    preferencesStore.resetForTests();
    // CAD mode and a running Fusion keep explicitly enabled polls at their
    // base rate. Neither is CAD work in flight.
    workspaceModeStore.setMode('cad');
    calls = [];
    gate = 'off';
    statusAnswer = fusionOpen;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path.endsWith('/api/cadlink/returns')) {
        // The gate rides on the listing; an older server's listing has no field.
        return json({ cadFolderConfigured: true, items: [], ...(gate === null ? {} : { coordination: gate }) });
      }
      if (path.endsWith('/api/cadlink/fusion-status')) return json(statusAnswer);
      if (path.startsWith('/api/cadlink/operations')) return json({ operations: [] });
      if (path.endsWith('/solver-selection')) return json({ engine: 'auto' });
      return json({}, 404);
    }));
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    importedMeshStore.clear();
    resetCadOperationsStore();
    resetCadCoordinationForTests();
    workspaceModeStore.setMode('parametric');
    resetCadPollIntervals();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    host.remove();
  });

  async function mount(): Promise<void> {
    await act(async () => {
      root.render(<CadLinkCoordinator/>);
      await vi.advanceTimersByTimeAsync(50);
    });
  }

  it('off, nothing in flight: no returns or Fusion-status request over ten idle minutes', async () => {
    await mount();
    const atMount = polls();
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    expect(polls()).toBe(atMount);
    // Operations are never polled either; they arrive as pushes.
    expect(operationReads()).toBeLessThanOrEqual(1);
  });

  it('off, an operation in flight: the reads run while it runs and stop once it ends (positive control)', async () => {
    await mount();
    const atMount = polls();
    act(() => { useCadOperationsStore.getState().apply(pending('op-running')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    const whileRunning = polls() - atMount;
    // Both reads at their 2.5 s base rate: about twelve each in thirty seconds.
    expect(whileRunning).toBeGreaterThanOrEqual(20);

    act(() => { useCadOperationsStore.getState().apply(pending('op-running', 'accepted', '2026-09-21T10:00:30Z')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    const settled = polls();
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    expect(polls()).toBe(settled);
  });

  it('explicit on: the same idle window keeps polling', async () => {
    gate = 'on';
    await mount();
    const atMount = polls();
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    // CAD mode with Fusion open holds both at the base rate: ~240 each.
    expect(polls() - atMount).toBeGreaterThan(400);
  });

  it.each([null, 'unexpected'])('an absent or unrecognized server answer keeps clock polls off (%s)', async (answer) => {
    gate = answer;
    await mount();
    const atMount = polls();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(polls()).toBe(atMount);
  });

  it('defaults to off before the first listing answer', () => {
    expect(cadCoordinationOff()).toBe(true);
  });

  it('learns the gate from the listing it reads at mount, with no request of its own', async () => {
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    expect(calls.filter((path) => path.includes('coordination'))).toEqual([]);
    expect(calls.filter((path) => path.endsWith('/api/cadlink/returns'))).toHaveLength(1);
  });

  it('off: entering CAD mode is an explicit event that reads once, not a clock', async () => {
    act(() => workspaceModeStore.setMode('parametric'));
    await mount();
    const atMount = polls();
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(polls() - atMount).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    expect(polls() - atMount).toBe(2);
  });

  it.each(['needs_user_input', 'recovery_required'])('off, an operation parked at %s: no clock reads (it cannot move without the user)', async (state) => {
    await mount();
    const atMount = polls();
    act(() => { useCadOperationsStore.getState().apply({ ...pending('op-parked'), state, kind: state === 'recovery_required' ? 'update_link' : 'prepare_and_solve' }); });
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    expect(polls()).toBe(atMount);
  });

  it('off: a parked operation that moves on again brings the reads back (the control)', async () => {
    await mount();
    act(() => { useCadOperationsStore.getState().apply({ ...pending('op-1'), state: 'needs_user_input' }); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    const parked = polls();
    act(() => { useCadOperationsStore.getState().apply({ ...pending('op-1', 'processing', '2026-09-21T10:00:30Z'), attemptGeneration: 2 }); });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(polls() - parked).toBeGreaterThanOrEqual(20);
  });

  it('off: a design edit reads Fusion status once, as the event it is, and no clock follows', async () => {
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    const statusReads = () => calls.filter((path) => path.endsWith('/api/cadlink/fusion-status')).length;
    const before = statusReads();
    act(() => { useDesignStore.setState({ designRevision: useDesignStore.getState().designRevision + 1 }); });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    const afterEdit = statusReads();
    expect(afterEdit - before).toBeGreaterThanOrEqual(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    expect(statusReads()).toBe(afterEdit);
  });

  it.each([
    ['the add-in is offline', { running: false, processRunning: true, state: 'addin_offline' }],
    ['Fusion already holds this design', {}],
  ])('never sends an unbound create when %s: it refuses with the reason', async (_name, overrides) => {
    statusAnswer = { ...fusionOpen, ...overrides } as FusionCadStatus;
    await mount();
    const before = calls.length;
    let refused: unknown = null;
    await act(async () => {
      await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion().catch((reason) => { refused = reason; });
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(refused).toBeInstanceOf(Error);
    // Status read on command, and nothing else: no export, no Fusion request.
    expect(calls.slice(before).filter((path) => !path.endsWith('/api/cadlink/fusion-status'))).toEqual([]);
  });

  // -- review F4: a status held without polling ages into a last report ----

  it('off: a fresh status held while idle stops claiming "Matches Fusion" once its window passes', async () => {
    statusAnswer = { ...fusionOpen, updatedAt: new Date().toISOString() } as FusionCadStatus;
    await mount();
    expect(cadSourceLine(cadLinkCoordinatorBridge.getSnapshot().fusionStatus, true)?.text).toContain('Matches Fusion');
    const reads = polls();
    // The next read would say closed, but nothing reads: no CAD work, no action.
    statusAnswer = { ...fusionOpen, state: 'closed', running: false, processRunning: false } as FusionCadStatus;
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    const line = cadSourceLine(cadLinkCoordinatorBridge.getSnapshot().fusionStatus, true)!;
    expect(line.text).not.toContain('Matches Fusion');
    expect(line.text).toMatch(/Fusion last reported .*not observed since/);
    expect(line.refresh).toBe(true);
    // Aged by the page itself: no request was made to do it.
    expect(polls()).toBe(reads);
  });

  it('on: a held status still ages when later status requests stall', async () => {
    gate = 'on';
    statusAnswer = { ...fusionOpen, updatedAt: new Date().toISOString() } as FusionCadStatus;
    await mount();
    const originalFetch = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => (
      String(input).endsWith('/fusion-status')
        ? new Promise<Response>(() => undefined)
        : originalFetch(input, init)
    )));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(cadLinkCoordinatorBridge.getSnapshot().fusionStatus?.statusObserved).toBe(false);
  });

  // -- review F5: the shipped pin's plain Send needs the listing -------------

  it('off: an add-in that does not declare the inbox transfer keeps the listing, so its plain Send is picked up', async () => {
    statusAnswer = { ...fusionOpen, addinInboxTransfer: false, observationPolicy: 'unknown' } as FusionCadStatus;
    await mount();
    let listingItems: unknown[] = [];
    const originalFetch = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/returns')) {
        calls.push(String(input));
        return json({ cadFolderConfigured: true, coordination: 'off', items: listingItems });
      }
      return originalFetch(input, init);
    }));
    const before = calls.filter((path) => path.endsWith('/returns')).length;
    // 04b2524b publishes a plain Send only as a return in the folder.
    listingItems = [{
      name: 'pin-send.wgreturn', bundlePath: 'wgreturn/pin-send.wgreturn', readable: true,
      modifiedAt: new Date().toISOString(), documentName: 'Sent by shipped pin', designIds: [], sources: [],
    }];
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    expect(calls.filter((path) => path.endsWith('/returns')).length - before).toBeGreaterThan(20);
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe('wgreturn/pin-send.wgreturn');
    // Fusion's status is still not read on a clock.
    const statusReads = calls.filter((path) => path.endsWith('/fusion-status')).length;
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    expect(calls.filter((path) => path.endsWith('/fusion-status')).length).toBe(statusReads);
  });

  it('off: Send to Fusion reads Fusion status when the user issues it', async () => {
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    const statusReads = () => calls.filter((path) => path.endsWith('/api/cadlink/fusion-status')).length;
    const before = statusReads();
    await act(async () => {
      void cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion().catch(() => undefined);
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(statusReads()).toBe(before + 1);
  });

  it('off: a heartbeat-session event invalidates cached M1 capability and discovers a replacement pin Send', async () => {
    statusAnswer = { ...fusionOpen, updatedAt: new Date().toISOString(), addinInboxTransfer: true } as FusionCadStatus;
    await mount();
    statusAnswer = { ...fusionOpen, updatedAt: new Date().toISOString(), addinInboxTransfer: false } as FusionCadStatus;
    let listingItems: unknown[] = [{
      name: 'pin-send.wgreturn', bundlePath: 'wgreturn/pin-send.wgreturn', readable: true,
      modifiedAt: new Date().toISOString(), documentName: 'New pin Send', designIds: [], sources: [],
    }];
    const originalFetch = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/returns')) {
        calls.push(String(input));
        return json({ cadFolderConfigured: true, coordination: 'off', items: listingItems });
      }
      return originalFetch(input, init);
    }));
    act(() => useCadOperationsStore.getState().noteAddinStatusChanged());
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe('wgreturn/pin-send.wgreturn');

    // The reverse transition stops the compatibility listing again.
    listingItems = [];
    statusAnswer = { ...fusionOpen, updatedAt: new Date().toISOString(), addinInboxTransfer: true } as FusionCadStatus;
    act(() => useCadOperationsStore.getState().noteAddinStatusChanged());
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    const afterM1 = calls.filter((path) => path.endsWith('/returns')).length;
    await act(async () => { await vi.advanceTimersByTimeAsync(TEN_MINUTES); });
    expect(calls.filter((path) => path.endsWith('/returns'))).toHaveLength(afterM1);
  });
});
