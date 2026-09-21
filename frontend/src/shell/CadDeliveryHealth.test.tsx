import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadDeliveryStatus, CadOperationSummary } from '../api/cadOperations';
import type { FusionCadStatus } from '../api/cadlink';
import { resetCadOperationsStore, useCadOperationsStore } from '../stores/cadOperations';
import { workspaceModeStore } from '../stores/workspaceMode';
import { CadDeliveryHealth, deliveryHealthLines, HUNG_PASS_MS, WAITING_READ_MS } from './CadDeliveryHealth';
import { fusionWorkflowView } from './cadWorkflowView';
import { AttentionNotices } from './TopBar';
import { bindWorkspaceNavigation, publishVisiblePanels, resetWorkspaceNavigationForTests } from './workspaceNavigation';

const NOW = Date.parse('2026-09-21T12:00:00Z');

const status = (overrides: Partial<CadDeliveryStatus> = {}): CadDeliveryStatus => ({
  consumer: 'running', declined: null,
  passStartedAt: '2026-09-21T11:59:59Z', lastPassCompletedAt: '2026-09-21T11:59:59Z',
  recentRefusals: [], variable: 'WG2_CAD_DELIVERY',
  ...overrides,
});

describe('why a request from Fusion is not moving', () => {
  it('says nothing about an idle consumer that runs (the control for every line below)', () => {
    expect(deliveryHealthLines(status(), NOW)).toEqual([]);
    expect(deliveryHealthLines(null, NOW)).toEqual([]);
  });

  it('names a consumer that is off, and the variable that turned it off', () => {
    const [line] = deliveryHealthLines(status({ consumer: 'disabled' }), NOW);
    expect(line.tone).toBe('blocked');
    expect(line.text).toContain('WG2_CAD_DELIVERY=0');
  });

  it('says why the last pass started nothing', () => {
    const [line] = deliveryHealthLines(status({ declined: 'No CAD Link folder is selected.' }), NOW);
    expect(line.text).toBe('Requests from Fusion are waiting: No CAD Link folder is selected.');
  });

  it('reports a pass that hung after earlier passes completed', () => {
    const lines = deliveryHealthLines(status({
      lastPassCompletedAt: new Date(NOW - HUNG_PASS_MS - 60_000).toISOString(),
      passStartedAt: new Date(NOW - HUNG_PASS_MS - 1_000).toISOString(),
    }), NOW);
    expect(lines.map((line) => line.text).join(' ')).toContain('has not finished a pass since');
  });

  it('believes the server when it says a pass is stuck', () => {
    const lines = deliveryHealthLines(status({ passHung: true }), NOW);
    expect(lines.map((line) => line.text).join(' ')).toContain('has not finished a pass since');
  });

  it('tells a stuck pass from an idle one', () => {
    const started = new Date(NOW - HUNG_PASS_MS - 1_000).toISOString();
    const stuck = deliveryHealthLines(status({ passStartedAt: started, lastPassCompletedAt: null }), NOW);
    expect(stuck).toHaveLength(1);
    expect(stuck[0].text).toContain('has not finished a pass since');
    // A pass under way for a moment is just a pass.
    const busy = deliveryHealthLines(status({ passStartedAt: new Date(NOW - 1_000).toISOString(), lastPassCompletedAt: '2026-09-21T11:59:00Z' }), NOW);
    expect(busy).toEqual([]);
  });
});

describe('the CAD Link panel section', () => {
  let host: HTMLDivElement;
  let root: Root;
  let reads: number;
  let answer: CadDeliveryStatus;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    resetCadOperationsStore();
    reads = 0;
    answer = status();
    vi.stubGlobal('fetch', vi.fn(async () => {
      reads += 1;
      return new Response(JSON.stringify(answer), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    resetCadOperationsStore();
  });

  const mount = async () => {
    await act(async () => { root.render(<CadDeliveryHealth now={() => NOW}/>); await vi.advanceTimersByTimeAsync(10); });
  };

  it('reads once while nothing waits, and not on a clock', async () => {
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10 * WAITING_READ_MS); });
    expect(reads).toBe(1);
    expect(host.textContent).toBe('');
  });

  it('keeps reading while a request waits to be taken, and shows why (the positive control)', async () => {
    answer = status({ declined: 'Waveguide Generator is about to restart to install 0.3.4.' });
    const waiting: CadOperationSummary = {
      operationId: 'op-1', kind: 'prepare_and_solve', state: 'received', stage: 'received', reason: null, message: null,
      jobId: null, attemptGeneration: 0, setupRevisionId: null, preparationId: null, snapshot: null, legacy: false,
      createdAt: null, updatedAt: null,
    };
    act(() => { useCadOperationsStore.getState().apply(waiting); });
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * WAITING_READ_MS); });
    expect(reads).toBeGreaterThanOrEqual(3);
    expect(host.textContent).toContain('about to restart');
  });

  it('shows a pass that hangs after the panel mounted, from the server\'s push', async () => {
    await mount();
    expect(host.textContent).toBe('');
    await act(async () => {
      useCadOperationsStore.getState().setDeliveryStatus(status({
        passStartedAt: '2026-09-21T11:59:40Z', lastPassCompletedAt: '2026-09-21T11:59:39Z', passHung: true,
      }));
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(host.textContent).toContain('has not finished a pass since');
    // The push was enough: no clock was needed to learn it.
    expect(reads).toBe(1);
  });

  it('lists refusals the server kept for a page that connected late', async () => {
    answer = status({ recentRefusals: [{ operationId: 'e5f7', file: 'e5f7.json', reason: 'It does not name its kind.', at: '2026-09-21T11:59:00Z' }] });
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(host.textContent).toContain('A request from Fusion was refused');
    expect(host.textContent).toContain('It does not name its kind.');
  });
});

describe('a refused request reaches the user in either mode', () => {
  let host: HTMLDivElement;
  let root: Root;
  let activations: string[];

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetCadOperationsStore();
    resetWorkspaceNavigationForTests();
    activations = [];
    bindWorkspaceNavigation((panel) => { activations.push(panel); publishVisiblePanels([panel]); return true; });
    workspaceModeStore.setMode('parametric');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    await act(async () => { root.render(<AttentionNotices/>); });
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    workspaceModeStore.setMode('parametric');
  });

  it('shows a notice that opens the CAD Link panel, and none before a refusal', () => {
    expect(host.querySelector('.attention-refused')).toBeNull();
    act(() => useCadOperationsStore.getState().recordRefusal({ operationId: null, file: 'x.json', reason: 'r', at: '2026-09-21T12:00:00Z' }));
    const notice = host.querySelector<HTMLButtonElement>('.attention-refused')!;
    expect(notice.textContent).toContain('A CAD request was refused');
    act(() => notice.click());
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activations.at(-1)).toBe('cadlink');
  });
});

describe('WGLink that reports only on command', () => {
  const reported = {
    cadApplication: 'fusion360', cadFolderConfigured: true, state: 'stale', running: true, processRunning: true,
    documentName: 'Speaker', currentFormula: 'OSSE', fusionFormula: 'OSSE', wgChangesAvailable: false,
    fusionChangesAvailable: false, documentChanged: false, documentChangeDetectable: false,
    observationFreshness: 'stale', staleDetectionExplanation: 'stale detection unavailable',
    link: { instanceId: 'i', parameterCount: 3, parameterDriftCount: 0, localBodyState: 'unmodified', configPresent: true },
    realizedDimensions: { state: 'link_unavailable', instanceId: null, exportId: null, parameters: [] },
  } as unknown as FusionCadStatus;

  it('says when Fusion was last reported, never that the add-in is offline or that Fusion matches', () => {
    const view = fusionWorkflowView({ ...reported, statusObserved: false, observedAt: '2026-09-21T11:50:00Z' });
    expect(view.headline).toMatch(/^Fusion 360 · last reported/);
    expect(view.detail).toContain('reports only when it runs a command');
    expect(`${view.headline} ${view.detail}`).not.toMatch(/offline|closed|Up to date/i);
    expect(view.state).not.toBe('current');
  });

  it('leaves an observed status as it was (the control)', () => {
    expect(fusionWorkflowView(reported).headline).not.toMatch(/last reported/);
  });
});
