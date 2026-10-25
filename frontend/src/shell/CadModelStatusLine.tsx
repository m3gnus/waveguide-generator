import { useSyncExternalStore } from 'react';
import { useCurrentModelStatus } from '../model/currentModel';
import { useCadModelSelection } from '../stores/cadModelState';
import { cadModelStatusLine, type CadModelStatusLine as Line } from '../stores/cadModelStatusLine';
import { cadWorkspace } from '../stores/cadWorkspace';
import { meshCeilingChoice } from '../stores/meshCeilingChoice';
import { cadApplicationName, usePreferences } from '../prefs/preferences';
import { heldRequestShown, openHeldRequest } from './HeldCadRequestDialog';
import { jobsCoordinatorBridge, useOptionalSolveControl } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

/** The selected CAD model, its request, and its one status line. Every
 * surface gets the same words: the CAD app named is the user's (Onshape
 * users never read Fusion's instruction). */
export function useCadModelStatusLine() {
  const { status } = useCurrentModelStatus();
  const model = useCadModelSelection(status);
  const control = useOptionalSolveControl();
  const application = cadApplicationName(usePreferences().cadApplication);
  const shown = useSyncExternalStore(heldRequestShown.subscribe, heldRequestShown.getSnapshot, heldRequestShown.getSnapshot);
  useSyncExternalStore(workspaceNavigation.subscribe, workspaceNavigation.getSnapshot, workspaceNavigation.getSnapshot);
  const line = cadModelStatusLine(model, status, {
    resultsShown: workspaceNavigation.isVisible('results'),
    waitingForEngine: control?.waitingForEngine ?? false,
    application,
    choiceOpen: !!shown && shown === model.heldRequest?.id,
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
    case 'show-results': workspaceNavigation.navigate('results'); return;
    case 'none': return;
  }
}

/**
 * The model card's one status line: what state the selected model is in, and
 * the one thing to do about it. No other surface repeats it with an action.
 */
export function CadModelStatusLine({ actions = true }: { actions?: boolean }) {
  const { line } = useCadModelStatusLine();
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
