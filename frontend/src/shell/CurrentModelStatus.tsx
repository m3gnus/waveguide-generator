import type { ReactNode } from 'react';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { isActiveJobStatus } from '../api/jobsSocket';
import { cadJobSummary, isReadyCadJob } from '../jobs/cadSolve';
import { messageBlocker } from '../jobs/solveReadiness';
import { useCurrentModelStatus } from '../model/currentModel';
import { CadModelStatusLine, useCadModelStatusLine, useFileModelView } from './CadModelStatusLine';
import { ENGINE_HOLDS, isQuietStatusLine, type CadStatusTone } from '../stores/cadModelStatusLine';
import { jobsCoordinatorBridge, useOptionalSolveControl } from './JobsCoordinator';
import { ResultsMessageArea, type ResultsMessage, type ResultsMessageSeverity } from './ResultsMessages';
import { clearSolveStageClock, MODEL_STATUS_TITLE, SolveProgressView, solveStageWord } from './solveProgress';

export interface CurrentModelStatusOptions { record?: CadReturnIngestRecord; actions?: boolean; solveStatus?: boolean; activeOnly?: boolean }

const LINE_SEVERITY: Record<CadStatusTone, ResultsMessageSeverity> = { attention: 'warning', busy: 'progress', idle: 'info', ready: 'info', done: 'info' };

/**
 * The current model's run status as Results messages (and, given a `record`,
 * for one CAD preparation).
 *
 * Results is the one home of an imported model's status (a CAD model or a
 * mesh file): its status line (`cadModelStatusLine`), shown once with its one
 * action beside it. No card, subtitle or overlay repeats it. Results also
 * keeps run progress, and the solve readiness view with its engine remedies.
 * Each is one row of the Results message area (`ResultsMessages.tsx`).
 */
export function useCurrentModelMessages({ record, actions = false, solveStatus = false, activeOnly = false }: CurrentModelStatusOptions): ResultsMessage[] {
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
  const failedAction = !!(control?.actionError || control?.preparationError);
  const pending = solveStatus && (control?.waitingForEngine || failedAction
    || (readiness && (engineHold || control?.readinessRequested)));
  const terminalFailure = status.kind === 'failed' || (status.kind === 'refused' && !held);
  // A readiness view is a solve that cannot start yet: an error when a press
  // failed, progress while an engine warms up, otherwise a warning.
  const readinessSeverity: ResultsMessageSeverity = failedAction || terminalFailure ? 'error'
    : control?.waitingForEngine ? 'progress' : 'warning';
  const row = (severity: ResultsMessageSeverity, content: ReactNode, rowActions?: ReactNode): ResultsMessage => ({
    key: 'current-model-status',
    severity,
    role: null,
    content: <div className="current-model-status" data-model-status={status.kind}>{content}</div>,
    actions: rowActions,
  });
  const messages: ResultsMessage[] = [];
  // What an accepted solve does differently from its setup, said with it.
  const notice = solveStatus ? control?.solveNotice : null;
  if (notice) messages.push({ key: `solve-notice:${notice.id}`, severity: 'info', dismissible: true, content: notice.message });

  if (model.kind === 'cad' || model.kind === 'file') {
    // A Solve press in progress or refused, or an engine gate: the readiness
    // view and its remedies, never a second Solve.
    // A latest attempt that failed or was refused is still said beside it.
    if (pending) return [row(readinessSeverity, <>
      <SolveProgressView showReadiness readiness={readiness} held={engineHold}/>
      {terminalFailure && <CadModelStatusLine file={file} actions={false}/>}
    </>), ...messages];
    // Run progress: a request still being prepared reads in the operation's
    // own stages; once bound, in its job's.
    // A refusal an older row keeps on a preparing job is said, not staged;
    // an update-restart hold resumes by itself, so its stage stays.
    const restartHold = latestJob?.cad_state?.reason === 'update_restart_pending';
    if (latestJob && isActiveJobStatus(latestJob.status) && (status.kind !== 'refused' || restartHold)) return [row('progress',
      latestJob.cad_state && latestJob.cad_state.state !== 'accepted'
        ? <SolveProgressView operation={cadJobSummary(latestJob)} variant="compact"/>
        : <SolveProgressView job={latestJob} variant="compact"/>), ...messages];
    // A finished run's clock goes with it, so a later sample under the same id starts afresh.
    if (latestJob) clearSolveStageClock(latestJob.id);
    // Beside open charts only a quiet line is hidden; a model-side problem or
    // question (and its action) is still said.
    if (activeOnly && !terminalFailure && isQuietStatusLine(line)) return messages;
    return [row(terminalFailure ? 'error' : LINE_SEVERITY[line.tone], <CadModelStatusLine file={file}/>), ...messages];
  }

  const latestAttempt = activeOnly && terminalFailure;
  const readinessVisible = held || control?.readinessRequested;
  const diagnosticCovered = pending && ((!control?.waitingForEngine && readinessVisible && readiness?.message === status.message)
    || control?.actionError?.endsWith(status.message) || control?.preparationError?.endsWith(status.message));
  const solveButton = <button type="button" className="btn-primary" disabled={!control || control.disabled} title={control?.title ?? 'Solve is not available here.'} onClick={() => void control?.solve()}>Solve</button>;
  if (activeOnly && (!pending || !control) && !terminalFailure && status.kind !== 'running' && status.kind !== 'preparing') return messages;
  // A disabled Solve always says why, before anyone presses it.
  const solveRefusal = actions && control?.disabled && !control.submitting && !pending ? control.title : null;
  const action = actions ? status.kind === 'failed' && latestJob
    ? <button type="button" className="btn" onClick={() => void jobsCoordinatorBridge.getSnapshot().continueRun(latestJob.id).catch(() => undefined)}>Retry</button>
    : solveButton : undefined;
  const readinessView = pending && (!engineGate || terminalFailure) ? <SolveProgressView showReadiness readiness={readiness}/> : null;
  if (pending && engineGate && !terminalFailure) return [row(readinessSeverity,
    <SolveProgressView showReadiness readiness={readiness} held={engineHold}/>,
    !control?.waitingForEngine ? action : undefined), ...messages];
  const progress = latestJob && (status.kind === 'preparing' || status.kind === 'running');
  const title = latestAttempt ? `Latest attempt · ${MODEL_STATUS_TITLE[status.kind]}` : actions && status.kind === 'solved' ? 'Loading results' : MODEL_STATUS_TITLE[status.kind];
  const body = diagnosticCovered || (pending && !terminalFailure) ? null : status.message;
  const severity: ResultsMessageSeverity = status.kind === 'failed' || status.kind === 'refused' || readinessView && failedAction ? 'error'
    : progress ? 'progress' : 'info';
  // The progress line names its own stage: a title that only repeats it goes.
  const showTitle = !progress || !latestJob || solveStageWord(latestJob) !== title;
  return [row(severity, <>
    {showTitle && <span className="results-message-title">{title}</span>}
    {progress ? <SolveProgressView job={latestJob} variant="compact"/> : body && <span className="results-message-detail">{body}</span>}
    {readinessView}
    {solveRefusal && <span className="disabled-reason">{solveRefusal}</span>}
  </>, action), ...messages];
}

/** Results' message area: the panel's own messages (`messages`) with the
 * current model's status. Its own component, so a status change redraws the
 * area and never the charts beside it. */
export function CurrentModelStatus({ messages = [], ...options }: CurrentModelStatusOptions & { messages?: readonly ResultsMessage[] }) {
  return <ResultsMessageArea messages={[...messages, ...useCurrentModelMessages(options)]}/>;
}
