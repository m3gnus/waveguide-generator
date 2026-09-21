import { useEffect, useRef, useSyncExternalStore } from 'react';
import type { CadOperationSummary } from '../api/cadOperations';
import { isPendingCadOperation } from '../api/cadOperations';
import { workspaceModeStore } from '../stores/workspaceMode';
import { navigationGeneration, workspaceNavigation } from './workspaceNavigation';

/**
 * When a solve's outcome may move the dock, and when it may only say so.
 *
 * Two things finish out of sight: a solve's results, and a CAD request that
 * stops to wait for the user. Both used to be correct and invisible -- the
 * result was selected behind another tab, the waiting request sat in a panel
 * that was inactive or, in Parametric mode, not in the dock at all.
 *
 * The rule for both: **automatic behaviour may complete an intention the user
 * is still holding; it may not override one they have since replaced.** A
 * command is *armed* with the explicit-navigation count at the moment it is
 * given (see workspaceNavigation). When its outcome arrives, it moves the dock
 * only if that count has not changed since; otherwise it leaves an
 * unobtrusive indication the user can follow when they choose.
 *
 * Arming happens where the command is given, never where a result is claimed:
 * `compareSelection.awaitRun` deliberately takes nothing over at submission,
 * and the reveal happens where the claim resolves (shell/ResultsPanel).
 */

/** Explicit-navigation count captured by a Solve press, until a run or
 * operation takes it over. */
let pendingSolve: number | null = null;
/** Run id -> the count its command was given at. */
const runArms = new Map<string, number>();
/** Operation id -> the count its command (or arrival) was given at. */
const operationArms = new Map<string, number>();
/** Runs whose results have already been handed to the user one way or the other. */
const resolvedRuns = new Set<string>();

let readyRun: string | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());

function setReadyRun(jobId: string | null): void {
  if (readyRun === jobId) return;
  readyRun = jobId;
  emit();
}

// Seeing Results by any route answers "where did my solve go?".
workspaceNavigation.subscribe(() => {
  if (readyRun !== null && workspaceNavigation.isVisible('results')) setReadyRun(null);
});

export const solveAttention = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  /** A finished run the user asked for, whose Results were not revealed. */
  getReadyRun: (): string | null => readyRun,

  /** The user pressed Solve (button, shortcut, palette, or a pull and solve). */
  armSolve(): void {
    pendingSolve = navigationGeneration();
    setReadyRun(null);
  },
  /** The operation a Solve press created or recovered carries its arm. */
  bindOperation(operationId: string): void {
    if (pendingSolve === null) return;
    operationArms.set(operationId, pendingSolve);
    pendingSolve = null;
  },
  /** A command given on an operation now: an action on its card. */
  armOperation(operationId: string): void {
    operationArms.set(operationId, navigationGeneration());
    setReadyRun(null);
  },
  /** First sight of an operation that has not been armed by a command here --
   * a request Fusion sent. Its arrival is when the user asked for it. */
  noticeOperation(operationId: string): void {
    if (!operationArms.has(operationId)) operationArms.set(operationId, navigationGeneration());
  },
  operationArmed(operationId: string): boolean {
    return operationArms.get(operationId) === navigationGeneration();
  },
  /** A run was claimed for the user: the arm follows it from its operation, or
   * from the Solve press that submitted it. A run nobody armed is claimed
   * without one, and its results are indicated rather than revealed. */
  bindRun(jobId: string, operationId?: string): void {
    const fromOperation = operationId !== undefined ? operationArms.get(operationId) : undefined;
    const armed = fromOperation ?? pendingSolve;
    if (fromOperation === undefined) pendingSolve = null;
    if (armed !== null && armed !== undefined && !runArms.has(jobId)) runArms.set(jobId, armed);
  },
  /**
   * The claim on `jobId` resolved: its results exist and it is the primary run.
   * Reveal Results if the command that asked for it is still the user's latest
   * intention; otherwise say the results are ready. Once per run, so a
   * completion delivered twice never reveals twice.
   */
  resultsReady(jobId: string): 'revealed' | 'indicated' | 'repeated' {
    if (resolvedRuns.has(jobId)) return 'repeated';
    resolvedRuns.add(jobId);
    const armed = runArms.get(jobId);
    runArms.delete(jobId);
    if (armed !== undefined && armed === navigationGeneration()) {
      setReadyRun(null);
      workspaceNavigation.activate('results');
      return 'revealed';
    }
    if (!workspaceNavigation.isVisible('results')) setReadyRun(jobId);
    return 'indicated';
  },
  dismissReady(): void {
    setReadyRun(null);
  },
};

/** Tests only. */
export function resetSolveAttentionForTests(): void {
  pendingSolve = null;
  runArms.clear();
  operationArms.clear();
  resolvedRuns.clear();
  readyRun = null;
  emit();
}

export function useReadyRun(): string | null {
  return useSyncExternalStore(solveAttention.subscribe, solveAttention.getReadyRun, solveAttention.getReadyRun);
}

/** An operation that cannot move until the user acts: a solve waiting at one of
 * its gates, or a Fusion edit that stopped part-way. The CAD Link panel shows
 * exactly these (CadOperationsSection). */
export function operationNeedsUser(operation: CadOperationSummary): boolean {
  if (operation.kind === 'prepare_and_solve') return operation.state === 'needs_user_input';
  return operation.state === 'recovery_required'
    && (operation.kind === 'insert_link' || operation.kind === 'update_link');
}

/** What about an operation needs the user, so a new gate is a new event. */
function attentionKey(operation: CadOperationSummary): string | null {
  if (!operationNeedsUser(operation)) return null;
  return `${operation.state}:${operation.reason ?? ''}:${operation.attemptGeneration}:${operation.preparationId ?? ''}`;
}

/**
 * Front the CAD Link panel when an operation arrives needing the user, or
 * moves to needing them, while its command is still the user's latest intention.
 *
 * Only in CAD mode: the panel exists only there, and this never changes the
 * workspace mode. Everywhere else -- Parametric mode, or a user who has
 * navigated since -- the top bar's notice is the route to it.
 */
export function useOperationAttention(operations: Record<string, CadOperationSummary>): void {
  const seen = useRef(new Map<string, string | null>());
  useEffect(() => {
    let front = false;
    for (const operation of Object.values(operations)) {
      if (isPendingCadOperation(operation)) solveAttention.noticeOperation(operation.operationId);
      const key = attentionKey(operation);
      const previous = seen.current.get(operation.operationId);
      seen.current.set(operation.operationId, key);
      if (key === null || key === previous) continue;
      if (solveAttention.operationArmed(operation.operationId)) front = true;
    }
    if (front && workspaceModeStore.getSnapshot().mode === 'cad') workspaceNavigation.activate('cadlink');
  }, [operations]);
}
