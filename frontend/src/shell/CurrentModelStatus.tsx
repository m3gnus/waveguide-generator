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
import { clearSolveStageClock, SolveProgressView } from './solveProgress';
import { SolveReadiness } from './SolveReadiness';

const STATUS_COPY = { solved: 'Solved', running: 'Solving', preparing: 'Preparing', refused: 'Solve refused', failed: 'Solve failed', 'never-solved': 'Not solved yet' };

export function CurrentModelStatus({ record, actions = false }: { record?: CadReturnIngestRecord; actions?: boolean }) {
  const current = useCurrentModel();
  const model = record ? { ...current, kind: 'cad' as const, key: record.solve_model_sha256 || record.ingest_id,
    ingestId: record.ingest_id, manifestSha256: record.manifest_sha256 } : current;
  const control = useOptionalSolveControl();
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const status = currentModelStatus(model, jobs, control?.disabled ? control.title : null);
  const latestJob = status.job;
  const latest = latestJob?.cad_state ? cadJobSummary(latestJob) : null;
  const unavailable = !control || (!!record && !control.cadMode);
  const readiness = control?.readiness ?? (status.kind === 'refused' ? messageBlocker(status.message)
    : unavailable ? messageBlocker('Solve is not available here.') : null);
  const held = status.kind === 'refused' && latestJob?.cad_state?.state === 'needs_user_input'
    && latestJob.cad_state.reason === 'engine_unavailable';
  const request: ReactNode = record ? <CurrentModelRequests record={record}/> : null;
  const controls = <>
    <SolveReadiness readiness={readiness} held={held}/>
    {actions && (status.kind === 'failed' && latestJob
      ? <button type="button" onClick={() => void jobsCoordinatorBridge.getSnapshot().continueRun(latestJob.id).catch(() => undefined)}>Retry</button>
      : <button type="button" className="solve-button" disabled={!control || control.disabled} title={control?.title ?? 'Solve is not available here.'} onClick={() => void control?.solve()}>Solve</button>)}
  </>;
  if (!latest) return <div className="current-model-status" data-model-status={status.kind}>
    <b>{actions && status.kind === 'solved' ? 'Loading results' : STATUS_COPY[status.kind]}</b>
    {latestJob ? <SolveProgressView job={latestJob} variant="compact"/> : !readiness && !control?.disabled && <span>{status.message}</span>}
    {controls}{request}
  </div>;
  let tone: 'ok' | 'info' | 'warn' = 'info';
  let body: ReactNode;
  if (latest.state === 'rejected') {
    body = `Refused: ${latest.message ?? latest.reason ?? 'no reason given'}`;
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
      case 'complete': clearSolveStageClock(job.id); body = <SolveProgressView job={job} variant="compact"/>; tone = 'ok'; break;
      case 'error': clearSolveStageClock(job.id); body = <SolveProgressView job={job} variant="compact"/>; tone = 'warn'; break;
      case 'cancelled': clearSolveStageClock(job.id); body = <SolveProgressView job={job} variant="compact"/>; tone = 'warn'; break;
    }
  }
  return <div className="current-model-status" data-model-status={status.kind}>
    {status.kind === 'refused' && <b>{STATUS_COPY[status.kind]}</b>}
    <p className={`cad-solve-run cad-solve-run-${tone}`} role="status" data-run-operation-id={latest.operationId}>{body}</p>
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
          className="link-button"
          data-action="change-automatic-frame"
          onClick={() => { if (model.ingestId) useCadSolverFrameStore.getState().requestChange(model.ingestId); }}
        >Change</button>
      </p>}
  </div>;
}
