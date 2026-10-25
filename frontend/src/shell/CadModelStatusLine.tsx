import { useSyncExternalStore } from 'react';
import { useCurrentModelStatus } from '../model/currentModel';
import { useCadModelSelection } from '../stores/cadModelState';
import { cadModelStatusLine, type CadModelStatusLine as Line } from '../stores/cadModelStatusLine';
import { cadWorkspace } from '../stores/cadWorkspace';
import { meshCeilingChoice } from '../stores/meshCeilingChoice';
import { openHeldRequest } from './HeldCadRequestDialog';
import { jobsCoordinatorBridge, useOptionalSolveControl } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

/** The selected CAD model, its request, and its one status line. */
export function useCadModelStatusLine(application?: string) {
  const { status } = useCurrentModelStatus();
  const model = useCadModelSelection(status);
  const control = useOptionalSolveControl();
  useSyncExternalStore(workspaceNavigation.subscribe, workspaceNavigation.getSnapshot, workspaceNavigation.getSnapshot);
  const line = cadModelStatusLine(model, status, {
    resultsShown: workspaceNavigation.isVisible('results'),
    waitingForEngine: control?.waitingForEngine ?? false,
    application,
  });
  return { model, request: status, line };
}

/** Runs a status line's action. Solve is the top bar's own command. */
export function runCadStatusAction(line: Line, solve: (() => unknown) | undefined): void {
  switch (line.action) {
    case 'solve': void solve?.(); return;
    case 'choose':
      if (line.jobId) openHeldRequest(line.jobId); else meshCeilingChoice.reopen();
      return;
    case 'try-again':
      if (line.jobId) void jobsCoordinatorBridge.getSnapshot().continueRun(line.jobId).catch(() => undefined);
      else void cadWorkspace.getSnapshot().ingest().catch(() => undefined);
      return;
    case 'prepare': void cadWorkspace.getSnapshot().ingest().catch(() => undefined); return;
    case 'show-results': workspaceNavigation.navigate('results'); return;
    case 'none': return;
  }
}

/**
 * The model card's one status line: what state the selected model is in, and
 * the one thing to do about it. No other surface repeats it with an action.
 */
export function CadModelStatusLine({ application, actions = true }: { application?: string; actions?: boolean }) {
  const { line } = useCadModelStatusLine(application);
  const control = useOptionalSolveControl();
  const solveUnavailable = line.action === 'solve' && (!control?.cadMode || control.disabled);
  return <div className={`cad-status-line cad-status-line-${line.tone}`} role="status" data-status-action={line.action}>
    <span className="cad-status-dot" aria-hidden="true"/>
    <span className="cad-status-text">{line.text}</span>
    {actions && line.actionLabel && <button
      type="button"
      className={line.action === 'solve' || line.action === 'choose' ? 'btn-primary cad-status-action' : 'btn cad-status-action'}
      data-action={line.action}
      disabled={solveUnavailable}
      aria-busy={line.action === 'solve' ? control?.submitting ?? false : undefined}
      title={line.action === 'solve' ? control?.title ?? 'Solve is not available here.' : undefined}
      onClick={() => runCadStatusAction(line, control?.solve)}
    >{line.action === 'solve' && control?.submitting ? 'Solving…' : line.actionLabel}</button>}
  </div>;
}
