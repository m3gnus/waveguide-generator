import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { getCapabilities, submitDesign, type EngineCapability } from '../jobs/actions';
import { useDesignStore, type DesignDocument } from '../stores/design';

function name(job: JobItem): string {
  return job.label || `${String(job.config_summary.formula_type ?? 'design').toLowerCase()}_${job.id.slice(0, 8)}`;
}

function clock(iso: string | null): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
}

function secondsBetween(start: string | null, end: string | null, now: number): number {
  if (!start) return 0;
  return Math.max(0, ((end ? Date.parse(end) : now) - Date.parse(start)) / 1000);
}

function duration(value: number): string {
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}:${Math.floor(value % 60).toString().padStart(2, '0')}`;
}

function metrics(job: JobItem, now: number): string {
  const elapsed = duration(secondsBetween(job.started_at ?? job.queued_at, job.completed_at, now));
  const triangles = job.mesh_stats?.triangle_count;
  const count = job.config_summary.num_frequencies;
  return [elapsed, triangles ? `${Number(triangles).toLocaleString()} el` : null, count ? `${String(count)} f` : null, String(job.config_summary.engine ?? '')]
    .filter(Boolean).join(' · ');
}

function Rating({ job, onError }: { job: JobItem; onError: (message: string) => void }) {
  const rating = job.rating ?? 0;
  return <div className="score" aria-label={`Rating ${rating} of 5`}>
    <span>{[1, 2, 3, 4, 5].map((star) => <button
      key={star}
      aria-label={`Rate ${star} stars`}
      title={`Rate ${star} stars`}
      onClick={() => void jobsSocket.patchRating(job.id, star === rating ? 0 : star).catch((error) => onError(String(error)))}
      style={{ color: star <= rating ? 'var(--amber)' : 'var(--fg4)', padding: 0, background: 'none' }}
    >★</button>)}</span>
  </div>;
}
function MiniJob({ job }: { job: JobItem }) {
  return <button className="mini-job" onClick={() => job.has_results && compareSelection.setPrimary(job.id)} title={job.has_results ? 'Show results' : job.error_message ?? job.status}>
    <i style={{ background: job.status === 'error' ? 'var(--red)' : job.status === 'cancelled' ? 'var(--fg3)' : 'var(--green)' }}/>
    <span>{name(job)}</span>
    <em>{job.rating ? `${'★'.repeat(job.rating)}${'☆'.repeat(5 - job.rating)}` : ''}</em>
    <time>{clock(job.completed_at ?? job.created_at)}</time>
  </button>;
}

function JobCard({ job, now, run, onError }: {
  job: JobItem;
  now: number;
  run: (design: DesignDocument) => Promise<void>;
  onError: (message: string) => void;
}) {
  const loadDesign = useDesignStore((state) => state.loadDesign);
  const currentDesign = useDesignStore((state) => state.design);
  const running = job.status === 'running' || job.status === 'queued';
  const failed = job.status === 'error';
  const snapshot = job.script_snapshot as unknown as DesignDocument | null;
  const retry = () => void run(snapshot?.formula ? snapshot : currentDesign).catch((error) => onError(String(error)));
  const load = () => {
    if (snapshot?.formula) loadDesign(snapshot);
    if (job.has_results) compareSelection.setPrimary(job.id);
  };
  return <article className={`job-card ${running ? 'running' : failed ? 'failed' : 'complete'}`}>
    <header><i/><b>{name(job)} <em>· {job.id.slice(0, 6)}</em></b><time>{running ? duration(secondsBetween(job.started_at ?? job.queued_at, null, now)) : clock(job.completed_at)}</time></header>
    {running ? <>
      <p>{metrics(job, now)}</p>
      <div className="job-stage"><span>{job.stage_message ?? job.stage ?? 'waiting…'}</span><b>{Math.round(job.progress * 100)}%</b></div>
      <div className="progress"><i style={{ width: `${Math.max(0, Math.min(100, job.progress * 100))}%` }}/></div>
      {job.log_tail.length > 0 && <p title={job.log_tail.at(-1)}>{job.log_tail.at(-1)}</p>}
      <footer><button disabled={job.cancellation_requested} onClick={() => void jobsSocket.stopJob(job.id).catch((error) => onError(String(error)))}>{job.cancellation_requested ? 'Stopping…' : 'Stop'}</button><button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Log</button></footer>
    </> : failed ? <>
      <p>failed after {duration(secondsBetween(job.started_at ?? job.queued_at, job.completed_at, now))} · {job.stage ?? 'solve'} stage</p>
      <div className="job-error" title={job.error_message ?? undefined}>{job.error_message ?? 'Simulation failed without a diagnostic.'}</div>
      <footer><button onClick={retry}>Retry</button><button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Open log</button></footer>
    </> : <>
      <p>{metrics(job, now)}</p>
      <Rating job={job} onError={onError}/>
      <footer><button className="primary" disabled={!snapshot?.formula && !job.has_results} onClick={load}>Load design</button><button onClick={retry}>Rerun</button><button disabled={!job.has_results} onClick={() => compareSelection.setPrimary(job.id)}>Results</button></footer>
    </>}
  </article>;
}

export function JobsPanel() {
  const snapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const [capability, setCapability] = useState<EngineCapability | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => { jobsSocket.start(); return () => jobsSocket.stop(); }, []);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let live = true;
    void getCapabilities().then((value) => {
      if (!live) return;
      setCapability(value.engines.find((engine) => engine.name.toLowerCase() === 'dryrun') ?? null);
    }).catch((error) => live && setCapabilityError(error instanceof Error ? error.message : String(error)));
    return () => { live = false; };
  }, []);

  const run = useCallback(async (nextDesign: DesignDocument) => {
    if (!capability?.available) throw new Error(capability?.reason ?? capabilityError ?? 'Dry-run engine is unavailable');
    setSubmitting(true);
    setActionError(null);
    try {
      const jobId = await submitDesign(nextDesign);
      await jobsSocket.patchMetadata(jobId, { script_snapshot: structuredClone(nextDesign) });
      await jobsSocket.refresh();
    } finally {
      setSubmitting(false);
    }
  }, [capability, capabilityError]);

  // TopBar is outside this batch's write boundary; bridge its existing control here.
  useEffect(() => {
    const button = document.querySelector<HTMLButtonElement>('.solve-button');
    if (!button) return;
    const unavailable = capability?.reason ?? capabilityError ?? 'Checking dry-run engine capability…';
    button.disabled = !capability?.available || submitting;
    button.title = capability?.available ? (submitting ? 'Submitting solve…' : 'Solve current design with dry-run engine') : unavailable;
    button.setAttribute('aria-busy', String(submitting));
    const solve = () => void run(design).catch((error) => setActionError(error instanceof Error ? error.message : String(error)));
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && capability?.available && !submitting) {
        event.preventDefault();
        solve();
      }
    };
    button.addEventListener('click', solve);
    window.addEventListener('keydown', shortcut);
    return () => {
      button.removeEventListener('click', solve);
      window.removeEventListener('keydown', shortcut);
    };
  }, [capability, capabilityError, design, revision, run, submitting]);

  const { cards, earlier } = useMemo(() => {
    const active = snapshot.jobs.filter((job) => job.status === 'running' || job.status === 'queued');
    const complete = snapshot.jobs.filter((job) => job.status === 'complete').slice(0, 2);
    const failed = snapshot.jobs.filter((job) => job.status === 'error').slice(0, 1);
    const prominent = new Set([...active, ...complete, ...failed].map((job) => job.id));
    return { cards: snapshot.jobs.filter((job) => prominent.has(job.id)), earlier: snapshot.jobs.filter((job) => !prominent.has(job.id)) };
  }, [snapshot.jobs]);
  const failedCount = snapshot.jobs.filter((job) => job.status === 'error').length;

  return <div className="jobs-panel panel-scroll">
    <div className="panel-meta"><span className="pill">{snapshot.jobs.filter((job) => job.status === 'running' || job.status === 'queued').length} active</span><span>{snapshot.connection} · cursor {snapshot.cursor ?? '—'}</span><span className="spacer"/>{failedCount > 0 && <button onClick={() => void jobsSocket.clearFailed().catch((error) => setActionError(String(error)))} style={{ color: 'var(--red)', background: 'none' }}>clear failed</button>}</div>
    {(actionError || snapshot.error) && <div className="job-error" role="alert" style={{ margin: 7 }}>{actionError ?? snapshot.error}</div>}
    {snapshot.jobs.length === 0 && snapshot.connection === 'connected' && <div className="coming-soon"><b>NO JOBS YET</b><span>Use Solve to run the current design.</span></div>}
    {cards.map((job) => <JobCard key={job.id} job={job} now={now} run={run} onError={setActionError}/>)}
    {earlier.length > 0 && <><div className="earlier"><span>Earlier today</span><i/></div>{earlier.map((job) => <MiniJob key={job.id} job={job}/>)}</>}
  </div>;
}
