import { useSyncExternalStore, type ReactNode } from 'react';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { cadJobSummary } from '../jobs/cadSolve';
import { messageBlocker } from '../jobs/solveReadiness';
import { currentModelStatus, useCurrentModel } from '../model/currentModel';
import { useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { CurrentModelRequests } from './CadOperationsSection';
import { automaticAxisNote, defaultSettingsNote } from './CadSolveInputs';
import { jobsCoordinatorBridge, useOptionalSolveControl } from './JobsCoordinator';
import { Notice } from './Notice';
import { clearSolveStageClock, MODEL_STATUS_TITLE, runStatusWord, SolveProgressView } from './solveProgress';

export function CurrentModelStatus({ record, actions = false, solveStatus = false, activeOnly = false }: { record?: CadReturnIngestRecord; actions?: boolean; solveStatus?: boolean; activeOnly?: boolean }) {
  const current = useCurrentModel();
  const model = record ? { ...current, kind: 'cad' as const, key: record.solve_model_sha256 || record.ingest_id,
    ingestId: record.ingest_id, manifestSha256: record.manifest_sha256 } : current;
  const control = useOptionalSolveControl();
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const status = currentModelStatus(model, jobs);
  const latestJob = status.job;
  const latest = latestJob?.cad_state ? cadJobSummary(latestJob) : null;
  const unavailable = !control || (!!record && !control.cadMode);
  const readiness = control?.readiness ?? latestJob?.cad_state?.blocker ?? (status.kind === 'refused' ? messageBlocker(status.message)
    : unavailable ? messageBlocker('Solve is not available here.') : null);
  const held = status.kind === 'refused' && latestJob?.cad_state?.state === 'needs_user_input'
    && latestJob.cad_state.reason === 'engine_unavailable';
  const request: ReactNode = record ? <CurrentModelRequests record={record}/> : null;
  const engineGate = held || !!readiness?.engine || control?.waitingForEngine;
  const pending = solveStatus && (control?.waitingForEngine || control?.actionError || control?.preparationError
    || (readiness && (!readiness.engine || held || control?.readinessRequested)));
  if (activeOnly && (!pending || !control) && status.kind !== 'running' && status.kind !== 'preparing') return null;
  const controls = actions || pending ? <>
    {pending && !engineGate && <SolveProgressView showReadiness readiness={readiness}/>}
    {actions && (status.kind === 'failed' && latestJob
      ? <button type="button" className="btn" onClick={() => void jobsCoordinatorBridge.getSnapshot().continueRun(latestJob.id).catch(() => undefined)}>Retry</button>
      : <button type="button" className="btn-primary" disabled={!control || control.disabled} title={control?.title ?? 'Solve is not available here.'} onClick={() => void control?.solve()}>Solve</button>)}
  </> : undefined;
  if (pending && engineGate) return <div className="current-model-status" data-model-status={status.kind}>
    <SolveProgressView showReadiness readiness={readiness} held={held}/>
    {!control?.waitingForEngine && controls}
  </div>;
  // Engine gates are described only in Results, including held Fusion requests.
  if (held && !solveStatus) return <div className="current-model-status" data-model-status={status.kind}>{controls}</div>;
  if (!latest) return <div className="current-model-status" data-model-status={status.kind}>
    <Notice tone={status.kind === 'failed' || status.kind === 'refused' ? 'error' : 'info'}
      title={actions && status.kind === 'solved' ? 'Loading results' : MODEL_STATUS_TITLE[status.kind]}
      actions={controls}>
      {latestJob && (status.kind === 'preparing' || status.kind === 'running')
        ? <SolveProgressView job={latestJob} variant="compact"/>
        : pending && (status.kind !== 'failed' || control?.actionError?.endsWith(status.message) || control?.preparationError?.endsWith(status.message)) ? null : status.message}
    </Notice>
    {request}
  </div>;
  let tone: 'ok' | 'info' | 'warn' = 'info';
  let body: ReactNode;
  if (latest.state === 'rejected') {
    body = <Notice tone="error" title={MODEL_STATUS_TITLE.refused}>{pending ? null : latest.message ?? latest.reason ?? 'No reason given.'}</Notice>;
    tone = 'warn';
  } else if (latest.state === 'cancelled') {
    body = 'Dismissed before it was solved.';
  } else if (latest.state !== 'accepted') {
    // Preparing and refused intents use the job's CAD stage and gate;
    // bound requests below use the same progress vocabulary for execution.
    if (latest.state === 'needs_user_input') tone = 'warn';
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
          title={<span className="job-stage-word">{runStatusWord(job)}</span>}>
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
    {/* WG was confident which way the model faces and solved along that axis
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
