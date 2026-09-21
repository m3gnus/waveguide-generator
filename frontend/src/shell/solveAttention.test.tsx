import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadOperationSummary } from '../api/cadOperations';
import type { FusionCadStatus } from '../api/cadlink';
import { resetCadOperationsStore, useCadOperationsStore } from '../stores/cadOperations';
import { resetCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { AttentionNotices, cadSourceLine } from './TopBar';
import { operationNeedsUser, resetSolveAttentionForTests, solveAttention, useOperationAttention } from './solveAttention';
import {
  bindWorkspaceNavigation,
  navigationGeneration,
  noteExplicitNavigation,
  publishVisiblePanels,
  resetWorkspaceNavigationForTests,
  workspaceNavigation,
  type WorkspacePanel,
} from './workspaceNavigation';

function operation(operationId: string, state: string, overrides: Partial<CadOperationSummary> = {}): CadOperationSummary {
  return {
    operationId, kind: 'prepare_and_solve', state, stage: 'ready', reason: null, message: null,
    jobId: null, attemptGeneration: 1, setupRevisionId: null, preparationId: 'wgp_1',
    snapshot: { manifestSha256: `sha256:${'1'.repeat(64)}`, documentName: 'Speaker' }, legacy: false,
    createdAt: '2026-09-21T10:00:00Z', updatedAt: '2026-09-21T10:00:00Z',
    ...overrides,
  };
}

const waiting = (id: string, overrides: Partial<CadOperationSummary> = {}) => operation(id, 'needs_user_input', {
  reason: 'frame_confirmation_required',
  message: 'Confirm this model’s solver frame in WG first.',
  ...overrides,
});

function Attention() {
  useOperationAttention(useCadOperationsStore((state) => state.operations));
  return <AttentionNotices/>;
}

describe('solve attention: reveal only while the user is still waiting', () => {
  let activations: WorkspacePanel[];

  beforeEach(() => {
    resetSolveAttentionForTests();
    resetWorkspaceNavigationForTests();
    activations = [];
    bindWorkspaceNavigation((panel) => { activations.push(panel); return true; });
  });

  it('reveals Results for an armed run, once', () => {
    solveAttention.armSolve();
    solveAttention.bindRun('job-1');
    expect(solveAttention.resultsReady('job-1')).toBe('revealed');
    expect(solveAttention.resultsReady('job-1')).toBe('repeated');
    expect(activations).toEqual(['results']);
    expect(solveAttention.getReadyRun()).toBeNull();
  });

  it('only indicates the results when the user navigated after pressing Solve', () => {
    solveAttention.armSolve();
    noteExplicitNavigation();
    solveAttention.bindRun('job-1');
    expect(solveAttention.resultsReady('job-1')).toBe('indicated');
    expect(activations).toEqual([]);
    expect(solveAttention.getReadyRun()).toBe('job-1');
  });

  it('does not arm a run nobody pressed Solve for', () => {
    solveAttention.bindRun('job-unasked');
    expect(solveAttention.resultsReady('job-unasked')).toBe('indicated');
    expect(activations).toEqual([]);
  });

  it('carries the arm through the operation a Solve press created', () => {
    solveAttention.armSolve();
    solveAttention.bindOperation('op-1');
    // A second, unrelated run submitted meanwhile does not take the arm.
    solveAttention.bindRun('job-other');
    solveAttention.bindRun('job-1', 'op-1');
    expect(solveAttention.resultsReady('job-other')).toBe('indicated');
    expect(solveAttention.resultsReady('job-1')).toBe('revealed');
  });

  it('does not count the application fronting a panel as the user navigating', () => {
    solveAttention.armSolve();
    workspaceNavigation.activate('cadlink');
    expect(navigationGeneration()).toBe(0);
    workspaceNavigation.navigate('jobs');
    expect(navigationGeneration()).toBe(1);
  });

  it('clears the ready indication once Results is on screen by any route', () => {
    solveAttention.armSolve();
    noteExplicitNavigation();
    solveAttention.bindRun('job-1');
    solveAttention.resultsReady('job-1');
    expect(solveAttention.getReadyRun()).toBe('job-1');
    publishVisiblePanels(['results']);
    expect(solveAttention.getReadyRun()).toBeNull();
  });

  it('names the operations that wait for the user', () => {
    expect(operationNeedsUser(waiting('a'))).toBe(true);
    expect(operationNeedsUser(operation('b', 'processing'))).toBe(false);
    expect(operationNeedsUser(operation('c', 'recovery_required', { kind: 'update_link' }))).toBe(true);
    expect(operationNeedsUser(operation('d', 'rejected'))).toBe(false);
  });
});

describe('F8: a blocked operation reaches the user in either mode', () => {
  let host: HTMLDivElement;
  let root: Root;
  let activations: WorkspacePanel[];

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetSolveAttentionForTests();
    resetWorkspaceNavigationForTests();
    resetCadOperationsStore();
    resetCadReturnStore();
    activations = [];
    bindWorkspaceNavigation((panel) => {
      activations.push(panel);
      publishVisiblePanels([panel]);
      return true;
    });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    await act(async () => { root.render(<Attention/>); });
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    workspaceModeStore.setMode('parametric');
    vi.restoreAllMocks();
  });

  const arrive = (summary: CadOperationSummary) => act(() => { useCadOperationsStore.getState().apply(summary); });

  it('fronts the CAD Link panel in CAD mode when a request arrives needing the user', () => {
    act(() => workspaceModeStore.setMode('cad'));
    arrive(waiting('op-fusion'));
    expect(activations).toEqual(['cadlink']);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
  });

  it('fronts it again when the request moves on to the next gate', () => {
    act(() => workspaceModeStore.setMode('cad'));
    arrive(waiting('op-fusion'));
    arrive(waiting('op-fusion', { reason: 'findings_need_review', attemptGeneration: 2, updatedAt: '2026-09-21T10:00:05Z' }));
    expect(activations).toEqual(['cadlink', 'cadlink']);
  });

  it('never changes the mode or fronts anything in Parametric mode, and shows a notice that opens the request', () => {
    arrive(waiting('op-fusion'));
    expect(activations).toEqual([]);
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    const notice = host.querySelector<HTMLButtonElement>('.attention-waiting');
    expect(notice?.textContent).toContain('A solve is waiting for you');
    expect(notice?.title).toContain('solver frame');

    // Following it is the user's own choice, so it may switch the mode.
    act(() => notice!.click());
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activations.at(-1)).toBe('cadlink');
    expect(navigationGeneration()).toBeGreaterThan(0);
    expect(host.querySelector('.attention-waiting')).toBeNull();
  });

  it('does not yank a user who navigated after the request arrived; the notice is their route', () => {
    act(() => workspaceModeStore.setMode('cad'));
    arrive(operation('op-fusion', 'processing'));
    act(() => { workspaceNavigation.navigate('geometry'); });
    activations.length = 0;
    arrive(waiting('op-fusion', { updatedAt: '2026-09-21T10:00:05Z' }));
    expect(activations).toEqual([]);
    expect(host.querySelector('.attention-waiting')?.textContent).toContain('A solve is waiting for you');
  });

  it('shows no notice while nothing waits (the notice itself is the measurement)', () => {
    arrive(operation('op-running', 'processing'));
    expect(host.querySelector('.attention-waiting')).toBeNull();
    arrive(waiting('op-running', { updatedAt: '2026-09-21T10:00:05Z' }));
    expect(host.querySelector('.attention-waiting')).not.toBeNull();
  });
});

describe('V2: the source line consumes observationFreshness honestly', () => {
  const status = (overrides: Partial<FusionCadStatus>) => ({
    running: true, state: 'current', fusionChangesAvailable: false,
    observationFreshness: 'current', documentChangeDetectable: true, ...overrides,
  }) as FusionCadStatus;

  it('says nothing about a model that is not on screen', () => {
    expect(cadSourceLine(status({}), false)).toBeNull();
  });

  it('reports newer CAD changes, with a refresh, whatever the observation age', () => {
    expect(cadSourceLine(status({ state: 'stale', fusionChangesAvailable: true, observationFreshness: 'stale' }), true))
      .toEqual({ text: 'Model loaded from Fusion · Newer CAD changes available', tone: 'changed', refresh: true });
  });

  it.each(['stale', 'none'] as const)('never claims a match from a %s observation', (freshness) => {
    expect(cadSourceLine(status({ observationFreshness: freshness }), true)?.text)
      .toBe('Model loaded from Fusion · Live CAD freshness not verified');
  });

  it('treats an unknown observation as a real measurement, not an incompatible add-in', () => {
    const line = cadSourceLine(status({ observationFreshness: 'unknown' }), true)!;
    expect(line.text).toBe('Model loaded from Fusion · Matches Fusion');
    expect(line.text).not.toMatch(/incompatible|outdated|older/i);
  });

  it('does not claim a match when the document cannot be compared', () => {
    expect(cadSourceLine(status({ documentChangeDetectable: false }), true)?.text)
      .toBe('Model loaded from Fusion · Live CAD freshness not verified');
  });

  it('says only where the model came from while Fusion is not reporting', () => {
    expect(cadSourceLine(status({ running: false, state: 'closed' as FusionCadStatus['state'] }), true))
      .toEqual({ text: 'Model loaded from Fusion', tone: 'info', refresh: false });
    expect(cadSourceLine(null, true)?.text).toBe('Model loaded from Fusion');
  });
});
