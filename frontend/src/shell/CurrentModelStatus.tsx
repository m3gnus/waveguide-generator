import type { CadReturnIngestRecord } from '../api/cadlink';
import { isActiveJobStatus } from '../api/jobsSocket';
import { cadJobSummary, isReadyCadJob } from '../jobs/cadSolve';
import { messageBlocker } from '../jobs/solveReadiness';
import { useCurrentModelStatus } from '../model/currentModel';
import { CadModelStatusLine, useCadModelStatusLine, useFileModelView } from './CadModelStatusLine';
import { ENGINE_HOLDS, isQuietStatusLine } from '../stores/cadModelStatusLine';
import { jobsCoordinatorBridge, useOptionalSolveControl } from './JobsCoordinator';
import { Notice } from './Notice';
import { clearSolveStageClock, MODEL_STATUS_TITLE, SolveProgressView } from './solveProgress';

/**
 * The current model's run status in Results (and, given a `record`, for one
 * CAD preparation).
 *
 * Results is the one home of an imported model's status (a CAD model or a
 * mesh file): its status line (`cadModelStatusLine`), shown once with its one
 * action beside it. No card, subtitle or overlay repeats it. Results also
 * keeps run progress, and the solve readiness view with its engine remedies.
 */
export function CurrentModelStatus({ record, actions = false, solveStatus = false, activeOnly = false }: { record?: CadReturnIngestRecord; actions?: boolean; solveStatus?: boolean; activeOnly?: boolean }) {
  const { model, status } = useCurrentModelStatus(record);
  const fileView = useFileModelView();
  const file = model.kind === 'file' ? fileView : undefined;
  const control = useOptionalSolveControl();
  const { line } = useCadModelStatusLine(file);
  const latestJob = status.job;
  const unavailable = !control || (!!record && !control.cadMode);
  // A prepared request blocks nothing: its sentence is never a readiness blocker.
  const ready = status.kind === 'waiting' && !!latestJob && isReadyCadJob(latestJob);
  const readiness = control?.readiness ?? latestJob?.cad_state?.blocker ?? (status.kind === 'refused' || (status.kind === 'waiting' && !ready) ? messageBlocker(latestJob?.cad_state?.message || status.message)
    : unavailable ? messageBlocker('Solve is not available here.') : null);
  const heldReason = status.kind === 'waiting' && latestJob?.cad_state?.state === 'needs_user_input' ? latestJob.cad_state.reason : null;
  const held = heldReason === 'engine_unavailable';
  // Every engine gate keeps its own readiness view, never "Needs a change".
  const engineHold = !!heldReason && ENGINE_HOLDS.has(heldReason);
  const engineGate = engineHold || !!readiness?.engine || control?.waitingForEngine;
  const pending = solveStatus && (control?.waitingForEngine || control?.actionError || control?.preparationError
    || (readiness && (engineHold || control?.readinessRequested)));
  const terminalFailure = status.kind === 'failed' || (status.kind === 'refused' && !held);

  if (model.kind === 'cad' || model.kind === 'file') {
    // A Solve press in progress or refused, or an engine gate: the readiness
    // view and its remedies, never a second Solve.
    // A latest attempt that failed or was refused is still said beside it.
    if (pending) return <div className="current-model-status" data-model-status={status.kind}>
      <SolveProgressView showReadiness readiness={readiness} held={engineHold}/>
      {terminalFailure && <CadModelStatusLine file={file} actions={false}/>}
    </div>;
    // Run progress: a request still being prepared reads in the operation's
    // own stages; once bound, in its job's.
    // A refusal an older row keeps on a preparing job is said, not staged;
    // an update-restart hold resumes by itself, so its stage stays.
    const restartHold = latestJob?.cad_state?.reason === 'update_restart_pending';
    if (latestJob && isActiveJobStatus(latestJob.status) && (status.kind !== 'refused' || restartHold)) return <div className="current-model-status" data-model-status={status.kind}>
      {latestJob.cad_state && latestJob.cad_state.state !== 'accepted'
        ? <SolveProgressView operation={cadJobSummary(latestJob)} variant="compact"/>
        : <SolveProgressView job={latestJob} variant="compact"/>}
    </div>;
    // A finished run's clock goes with it, so a later sample under the same id starts afresh.
    if (latestJob) clearSolveStageClock(latestJob.id);
    // Beside open charts only a quiet line is hidden; a model-side problem or
    // question (and its action) is still said.
    if (activeOnly && !terminalFailure && isQuietStatusLine(line)) return null;
    return <div className="current-model-status" data-model-status={status.kind}>
      <CadModelStatusLine file={file}/>
    </div>;
  }

  const latestAttempt = activeOnly && terminalFailure;
  const readinessVisible = held || control?.readinessRequested;
  const diagnosticCovered = pending && ((!control?.waitingForEngine && readinessVisible && readiness?.message === status.message)
    || control?.actionError?.endsWith(status.message) || control?.preparationError?.endsWith(status.message));
  const solveButton = <button type="button" className="btn-primary" disabled={!control || control.disabled} title={control?.title ?? 'Solve is not available here.'} onClick={() => void control?.solve()}>Solve</button>;
  if (activeOnly && (!pending || !control) && !terminalFailure && status.kind !== 'running' && status.kind !== 'preparing') return null;
  const controls = actions || pending ? <>
    {pending && (!engineGate || terminalFailure) && <SolveProgressView showReadiness readiness={readiness}/>}
    {actions && (status.kind === 'failed' && latestJob
      ? <button type="button" className="btn" onClick={() => void jobsCoordinatorBridge.getSnapshot().continueRun(latestJob.id).catch(() => undefined)}>Retry</button>
      : solveButton)}
  </> : undefined;
  if (pending && engineGate && !terminalFailure) return <div className="current-model-status" data-model-status={status.kind}>
    <SolveProgressView showReadiness readiness={readiness} held={engineHold}/>
    {!control?.waitingForEngine && controls}
  </div>;
  return <div className="current-model-status" data-model-status={status.kind}>
    <Notice tone={status.kind === 'failed' || status.kind === 'refused' ? 'error' : 'info'}
      title={latestAttempt ? `Latest attempt · ${MODEL_STATUS_TITLE[status.kind]}` : actions && status.kind === 'solved' ? 'Loading results' : MODEL_STATUS_TITLE[status.kind]}
      actions={controls}>
      {latestJob && (status.kind === 'preparing' || status.kind === 'running')
        ? <SolveProgressView job={latestJob} variant="compact"/>
        : diagnosticCovered || (pending && !terminalFailure) ? null : status.message}
    </Notice>
  </div>;
}
