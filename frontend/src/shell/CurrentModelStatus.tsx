import { meshChoiceLine, useCadModelState } from '../stores/cadModelState';
import { meshCeilingChoice } from '../stores/meshCeilingChoice';
import { cadWorkspace } from '../stores/cadWorkspace';
import type { ReactNode } from 'react';
import type { CadReturnIngestRecord } from '../api/cadlink';
import type { JobItem } from '../api/jobsSocket';
import { cadJobSummary, isReadyCadJob } from '../jobs/cadSolve';
import { messageBlocker } from '../jobs/solveReadiness';
import { useCurrentModelStatus } from '../model/currentModel';
import { useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { openHeldRequest } from './HeldCadRequestDialog';
import { CurrentModelRequests } from './CadOperationsSection';
import { automaticAxisNote, defaultSettingsNote } from './CadSolveInputs';
import { jobsCoordinatorBridge, useOptionalSolveControl } from './JobsCoordinator';
import { Notice } from './Notice';
import { clearSolveStageClock, MODEL_STATUS_TITLE, runStatusWord, SolveProgressView } from './solveProgress';

export function CurrentModelStatus({ record, actions = false, solveStatus = false, activeOnly = false }: { record?: CadReturnIngestRecord; actions?: boolean; solveStatus?: boolean; activeOnly?: boolean }) {
  const { model, status } = useCurrentModelStatus(record);
  const cad = useCadModelState();
  const control = useOptionalSolveControl();
  const latestJob = status.job;
  const latest = latestJob?.cad_state ? cadJobSummary(latestJob) : null;
  const unavailable = !control || (!!record && !control.cadMode);
  // A prepared request blocks nothing: its sentence is never a readiness blocker.
  const ready = status.kind === 'waiting' && !!latestJob && isReadyCadJob(latestJob);
  const readiness = control?.readiness ?? latestJob?.cad_state?.blocker ?? (status.kind === 'refused' || (status.kind === 'waiting' && !ready) ? messageBlocker(latestJob?.cad_state?.message || status.message)
    : unavailable ? messageBlocker('Solve is not available here.') : null);
  const heldReason = status.kind === 'waiting' && latestJob?.cad_state?.state === 'needs_user_input' ? latestJob.cad_state.reason : null;
  const held = heldReason === 'engine_unavailable';
  // Every engine gate keeps its own readiness view, never "Needs a change".
  const engineHold = held || heldReason === 'engine_cannot_solve_return';
  const request: ReactNode = record ? <CurrentModelRequests record={record}/> : null;
  // The pop-up owns the choice; the request's card stays, as Review reveals it.
  // Results is the one home of the Waiting notice; the CAD Solve card keeps its
  // run line and the request's card.
  if (solveStatus && cad.phase === 'waiting' && cad.heldRequest === latestJob && latestJob) return <div className="current-model-status" data-model-status="waiting">
    <Notice tone="info" title="Waiting for you" actions={<button type="button" className="btn" onClick={() => openHeldRequest(latestJob.id)}>Review request</button>}>
      {status.message}
    </Notice>
    {request}
  </div>;
  // A preparation the triangle ceiling stopped in this window, put off with
  // Later: the model waits for the mesh-size choice, which Choose reopens.
  if (solveStatus && cad.phase === 'waiting' && cad.meshChoice && !cad.heldRequest) return <div className="current-model-status" data-model-status="waiting">
    <Notice tone="info" title="Waiting for you" actions={cad.meshChoice.deferred
      ? <button type="button" className="btn" onClick={() => meshCeilingChoice.reopen()}>Choose</button> : undefined}>
      {meshChoiceLine(cad.label)}
    </Notice>
  </div>;
  // A failed preparation: WG's calm sentence and Try again where it can work.
  if (status.kind === 'prepare-failed' && latestJob) {
    const retry = latestJob.cad_state?.options?.find(option => option.id === 'retry');
    return <div className="current-model-status" data-model-status="prepare-failed">
      <Notice tone="info" title={MODEL_STATUS_TITLE['prepare-failed']} actions={actions && retry
        ? <button type="button" className="btn" onClick={() => void jobsCoordinatorBridge.getSnapshot().continueRun(latestJob.id).catch(() => undefined)}>{retry.label}</button>
        : undefined}>
        {status.message}
      </Notice>
      {request}
    </div>;
  }
  const engineGate = engineHold || !!readiness?.engine || control?.waitingForEngine;
  const pending = solveStatus && (control?.waitingForEngine || control?.actionError || control?.preparationError
    || (readiness && (engineHold || control?.readinessRequested)));
  const terminalFailure = status.kind === 'failed' || (status.kind === 'refused' && !held);
  const latestAttempt = activeOnly && terminalFailure;
  const readinessVisible = held || control?.readinessRequested;
  const diagnosticCovered = pending && ((!control?.waitingForEngine && readinessVisible && readiness?.message === status.message)
    || control?.actionError?.endsWith(status.message) || control?.preparationError?.endsWith(status.message));
  const solveButton = <button type="button" className="btn-primary" disabled={!control || control.disabled} title={control?.title ?? 'Solve is not available here.'} onClick={() => void control?.solve()}>Solve</button>;
  // A kept request that is prepared, or names something to change (a frame
  // changed elsewhere): calm, in its next step, never a red refusal, with the
  // one Solve, which solves the settings and frame on screen (the server
  // resumes this request's preparation when they are unchanged). A press in
  // progress or failed comes first: its progress, readiness and error.
  if (solveStatus && latestJob && (ready || (status.kind === 'waiting' && !engineHold && latestJob.cad_state?.attention === 'action'))
    && !(pending && engineGate)) {
    if (pending) return <div className="current-model-status" data-model-status={status.kind}>
      <SolveProgressView showReadiness readiness={readiness}/>
      {actions && solveButton}
      {request}
    </div>;
    return <div className="current-model-status" data-model-status={ready ? 'ready' : 'change'}>
      <Notice tone="info" title={MODEL_STATUS_TITLE[ready ? 'ready' : 'change']} actions={actions ? solveButton : undefined}>
        {status.message}
      </Notice>
      {request}
    </div>;
  }
  if (actions && status.kind === 'never-solved' && model.kind === 'cad' && cad.selected
    && (cad.needsPreparation || cad.phase === 'preparing')) return <div className="current-model-status" data-model-status="not-prepared">
    <Notice title={cad.phase === 'preparing' ? `Preparing ${cad.label}…` : 'Not prepared yet'}
      actions={<button type="button" className="btn-primary" disabled={!cad.canPrepare}
        onClick={() => { void cadWorkspace.getSnapshot().ingest().catch(() => undefined); }}>Prepare simulation</button>}>
      {cad.phase === 'preparing' ? 'CAD Link is preparing this model.' : 'Prepare this model in CAD Link before solving.'}
    </Notice>
  </div>;
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
  // Engine gates are described only in Results, including held Fusion requests.
  if (held && !solveStatus) return <div className="current-model-status" data-model-status={status.kind}>{controls}</div>;
  if (!latest) return <div className="current-model-status" data-model-status={status.kind}>
    <Notice tone={status.kind === 'failed' || status.kind === 'refused' ? 'error' : 'info'}
      title={latestAttempt ? `Latest attempt · ${MODEL_STATUS_TITLE[status.kind]}` : actions && status.kind === 'solved' ? 'Loading results' : MODEL_STATUS_TITLE[status.kind]}
      actions={controls}>
      {latestJob && (status.kind === 'preparing' || status.kind === 'running')
        ? <SolveProgressView job={latestJob} variant="compact"/>
        : diagnosticCovered || (pending && !terminalFailure) ? null : status.message}
    </Notice>
    {request}
  </div>;
  let tone: 'ok' | 'info' | 'warn' = 'info';
  let body: ReactNode;
  if (latest.state === 'rejected' || (latest.state === 'needs_user_input' && solveStatus && !held)) {
    body = <Notice tone="error" title={latestAttempt ? `Latest attempt · ${MODEL_STATUS_TITLE.refused}` : MODEL_STATUS_TITLE.refused}>{diagnosticCovered ? null : latest.message ?? latest.reason ?? 'No reason given.'}</Notice>;
    tone = 'warn';
  } else if (latest.state === 'cancelled') {
    body = 'Dismissed before it was solved.';
  } else if (latest.state !== 'accepted') {
    // Preparing and refused intents use the job's CAD stage and gate;
    // bound requests below use the same progress vocabulary for execution.
    if (latest.state === 'needs_user_input' && latest.attention !== 'ready') tone = 'warn';
    body = <SolveProgressView operation={latest} variant="compact"/>;
  } else {
    const job: JobItem = latestJob!;
    switch (job.status) {
      case 'preparing':
      case 'queued':
        body = <SolveProgressView job={job} variant="compact"/>;
        break;
      case 'running':
        // The same progress component JobsPanel's run cards use, in its
        // compact form -- one design for both CAD Link and parametric mode,
        // fed by nothing but this job's own state (works the same whether
        // this browser pressed Solve or a waiting Fusion request advanced).
        body = <SolveProgressView job={job} variant="compact"/>;
        break;
      case 'complete':
      case 'error':
      case 'cancelled':
        clearSolveStageClock(job.id);
        body = <Notice tone={job.status === 'error' ? 'error' : 'info'}
          title={<span className="job-stage-word">{latestAttempt ? `Latest attempt · ${MODEL_STATUS_TITLE[status.kind]}` : runStatusWord(job)}</span>}>
          {job.status === 'complete' ? 'Results are in Results' : job.error_message}
        </Notice>;
        tone = job.status === 'complete' ? 'ok' : job.status === 'error' ? 'warn' : 'info';
        break;
    }
  }
  return <div className="current-model-status" data-model-status={status.kind}>
    {!(held && readiness?.code === 'engine_unavailable' && readiness.remedies?.length)
      && <div className={`cad-solve-run cad-solve-run-${tone}`} role="status" data-run-operation-id={latest.operationId}>{body}</div>}
    {controls}{request}
    {/* No settings were recorded for this model, so WG solved it with its
        defaults; the backend's own words say so and where to change them. */}
    {latest.state === 'accepted' && latest.setupDefaults
      && <p className="cad-detail cad-solve-defaults" data-setup-defaults="true">
        {defaultSettingsNote(latest.message, latestJob?.status === 'complete')}
      </p>}
    {/* WG decided which way the model faces (confident or by its fallback) and solved along that axis
        without asking; the frame card above has the Change. */}
    {latest.state === 'accepted' && latest.frameAxisAutomatic
      && <p className="cad-detail cad-solve-frame-automatic" data-frame-axis-automatic={latest.frameAxisAutomatic}>
        {automaticAxisNote(latest.frameAxisAutomatic, latestJob?.status)}
        {' \u00b7 '}<button
          className="btn-link"
          data-action="change-automatic-frame"
          onClick={() => { if (model.ingestId) useCadSolverFrameStore.getState().requestChange(model.ingestId); }}
        >Change</button>
      </p>}
  </div>;
}
