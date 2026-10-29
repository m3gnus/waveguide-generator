import { useSyncExternalStore } from 'react';
import { useCurrentModelStatus } from '../model/currentModel';
import { useCadModelSelection } from '../stores/cadModelState';
import type { RegisteredMesh } from '../api/meshImport';
import { fileSymmetryLabel } from '../model/cadLabels';
import { cadModelStatusLine, type CadModelStatusLine as Line, type FileModelView } from '../stores/cadModelStatusLine';
import { cadWorkspace } from '../stores/cadWorkspace';
import { useImportedModelStore } from '../stores/importedModel';
import { meshCeilingChoice } from '../stores/meshCeilingChoice';
import { meshImportChoice, prepareMeshFile, useMeshImportStore } from '../stores/meshImport';
import { useImportedSetupSaveError } from './cadSetupPublisher';
import { cadApplicationName, usePreferences } from '../prefs/preferences';
import { heldRequestShown, openHeldRequest } from './HeldCadRequestDialog';
import { jobsCoordinatorBridge, useOptionalSolveControl } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

import './cadLinkPanel.css';

/** The selected CAD model, its request, and its one status line. Every
 * surface gets the same words: the CAD app named is the user's (Onshape
 * users never read Fusion's instruction). */
export function useCadModelStatusLine(file?: FileModelView) {
  const { status } = useCurrentModelStatus();
  const model = useCadModelSelection(status);
  const control = useOptionalSolveControl();
  const application = cadApplicationName(usePreferences().cadApplication);
  const shown = useSyncExternalStore(heldRequestShown.subscribe, heldRequestShown.getSnapshot, heldRequestShown.getSnapshot);
  useSyncExternalStore(workspaceNavigation.subscribe, workspaceNavigation.getSnapshot, workspaceNavigation.getSnapshot);
  const line = cadModelStatusLine(file ?? model, status, {
    resultsShown: workspaceNavigation.isVisible('results'),
    waitingForEngine: control?.waitingForEngine ?? false,
    application,
    choiceOpen: !!shown && shown === model.heldRequest?.id,
  });
  return { model, request: status, line };
}

/** Whether the mesh file's geometry can change: an original exists to
 * prepare it from, because this browser read it or the server kept it. */
export function useMeshFileEditable(): boolean {
  const original = useMeshImportStore(state => state.original !== null);
  const kept = useImportedModelStore(state => state.input === 'file' && state.modelId !== null && state.originalAvailable);
  return original || kept;
}

/** The imported mesh file's state, as its status line reads it. */
export function useFileModelView(): FileModelView {
  const state = useMeshImportStore();
  const model = useImportedModelStore();
  const editable = useMeshFileEditable();
  const saveError = useImportedSetupSaveError('file', model.input === 'file' ? model.modelId : null);
  const registered = model.input === 'file' ? model.prepared as RegisteredMesh | null : null;
  const summary = state.summary ?? registered?.summary ?? null;
  const draft = model.fileDraft;
  return { input: 'file', label: state.filename,
    reading: state.busy && !summary, preparing: state.busy && !!summary,
    choice: summary ? meshImportChoice(draft) : null, failureMessage: state.error ?? saveError,
    canPrepare: editable && !!summary && !saveError, prepared: !!registered, triangles: summary?.triangle_count ?? null,
    symmetryLabel: fileSymmetryLabel(draft.domainPlanes ?? [], false), notice: state.notice,
  };
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
    case 'mesh-settings': workspaceNavigation.navigate('simulation'); return;
    case 'cad-link': workspaceNavigation.reveal('cad-link'); return;
    case 'show-results': workspaceNavigation.navigate('results'); return;
    case 'none': return;
  }
}

/**
 * An imported model's one status line, shown once, in Results: what state the
 * model is in, and the one thing to do about it beside it. No other surface
 * repeats it.
 */
export function CadModelStatusLine({ actions = true, file }: { actions?: boolean; file?: FileModelView }) {
  const { line } = useCadModelStatusLine(file);
  const control = useOptionalSolveControl();
  const solveUnavailable = line.action === 'solve' && (!control?.importedGeometryActive || control.disabled);
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
      onClick={() => file && line.action === 'try-again' && !line.jobId ? void prepareMeshFile() : runCadStatusAction(line, control?.solve)}
    >{line.action === 'solve' && control?.submitting ? 'Solving…' : line.actionLabel}</button>}
  </div>;
}
