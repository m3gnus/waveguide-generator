import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { hydrateJobDesign, jobDesignWire, replaceWithJobDesign } from '../jobs/jobDesign';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { applyJobPreferences, usePreferences } from '../prefs/preferences';
import { JobsPreferencesSurface } from '../prefs/PreferencesSurface';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { Icon } from './icons';

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

export function canLoadDesign(job: Pick<JobItem, 'script_snapshot'>): boolean {
  return jobDesignWire(job) !== null && hydrateJobDesign(job) !== null;
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

/**
 * Selecting a run shows it: its results drive the charts and its design snapshot
 * goes back into the viewport. Both stores are set synchronously so the row
 * expands on the same frame as the click; the results payload is served from
 * the LRU cache and the geometry preview catches up behind it.
 */
export function selectJob(job: JobItem): void {
  if (job.has_results) compareSelection.setPrimary(job.id);
  // Undoable: browsing runs must not be able to discard the working design.
  if (canLoadDesign(job)) replaceWithJobDesign(job, { keepHistory: true });
}

function JobCard({ job, now, selected, run, onError, onRemove }: {
  job: JobItem;
  now: number;
  selected: boolean;
  run: (design: DesignDocument, designRevision?: number) => Promise<void>;
  onError: (message: string) => void;
  onRemove: (job: JobItem) => void;
}) {
  const currentDesign = useDesignStore((state) => state.design);
  const running = job.status === 'running' || job.status === 'queued';
  const failed = job.status === 'error';
  const snapshot = hydrateJobDesign(job);
  const rating = job.rating ?? 0;
  const retry = () => void run(snapshot ?? currentDesign, snapshot ? job.design_revision : undefined).catch((error) => onError(String(error)));
  // A run in flight or one that failed still has to show its progress or its
  // diagnostic; only finished runs collapse down to their name.
  const expanded = selected || running || failed;
  const selectable = !running && (job.has_results || canLoadDesign(job));
  const heading = <>
    <i/>
    <b>{name(job)}{expanded && <em> · {job.id.slice(0, 6)}</em>}</b>
    {/* Stars are a label here, shown only once a run has actually been rated. */}
    {!expanded && rating > 0 && <span className="job-stars" aria-label={`Rated ${rating} of 5`}>{'★'.repeat(rating)}</span>}
    <time>{running ? duration(secondsBetween(job.started_at ?? job.queued_at, null, now)) : clock(job.completed_at ?? job.created_at)}</time>
  </>;
  return <article className={`job-card ${running ? 'running' : failed ? 'failed' : 'complete'}${selected ? ' selected' : ''}${expanded ? '' : ' collapsed'}`} aria-current={selected ? 'true' : undefined}>
    <header>
      {selectable
        ? <button className="job-select" aria-pressed={selected} title={selected ? 'Showing this run' : 'Show this run in the viewport and charts'} onClick={() => selectJob(job)}>{heading}</button>
        : <span className="job-select" title={job.error_message ?? job.status}>{heading}</span>}
      {!running && <button className="job-remove" aria-label={`Remove ${name(job)}`} title="Remove this job" onClick={() => onRemove(job)}><Icon name="close"/></button>}
    </header>
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
    </> : expanded && <>
      <p>{metrics(job, now)}</p>
      <Rating job={job} onError={onError}/>
      {/* Selecting the run already loaded its design and results, so the only
          action left is running it again. */}
      <footer><button className="primary" onClick={retry}>Rerun</button><button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Log</button></footer>
    </>}
  </article>;
}

export function JobsPanel() {
  const snapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  const selection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const coordinator = useSyncExternalStore(jobsCoordinatorBridge.subscribe, jobsCoordinatorBridge.getSnapshot, jobsCoordinatorBridge.getSnapshot);
  const preferences = usePreferences();
  const [now, setNow] = useState(Date.now());
  const [preferencesOpen, setPreferencesOpen] = useState(false);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, []);

  const visibleJobs = useMemo(() => applyJobPreferences(snapshot.jobs, preferences.jobSort, preferences.minRating), [snapshot.jobs, preferences.jobSort, preferences.minRating]);
  const failedCount = visibleJobs.filter((job) => job.status === 'error').length;
  const remove = (job: JobItem) => {
    if (!window.confirm(`Remove “${name(job)}” and its saved results?`)) return;
    void jobsSocket.deleteJob(job.id).catch((error) => coordinator.reportError(String(error)));
  };

  return <div className="jobs-panel panel-scroll">
    <div className="panel-meta"><span className="pill">{visibleJobs.filter((job) => job.status === 'running' || job.status === 'queued').length} active</span><span>{snapshot.connection} · {visibleJobs.length}/{snapshot.jobs.length} shown</span><span className="spacer"/>{failedCount > 0 && <button className="panel-text-action panel-text-action--danger" onClick={() => void jobsSocket.clearFailed().catch((error) => coordinator.reportError(String(error)))}>Clear failed</button>}<button className={`panel-preferences-trigger${preferencesOpen ? ' on' : ''}`} aria-label="Job preferences" aria-expanded={preferencesOpen} title="Job preferences" onClick={() => setPreferencesOpen((value) => !value)}><Icon name="settings"/></button></div>
    {preferencesOpen && <JobsPreferencesSurface popover onClose={() => setPreferencesOpen(false)}/>}
    {(coordinator.actionError || snapshot.error) && <div className="job-error" role="alert" style={{ margin: 7 }}>{coordinator.actionError ?? snapshot.error}</div>}
    {snapshot.jobs.length === 0 && snapshot.connection === 'connected' && <div className="coming-soon"><b>NO JOBS YET</b><span>Use Solve to run the current design.</span></div>}
    {snapshot.jobs.length > 0 && visibleJobs.length === 0 && <div className="coming-soon"><b>NO MATCHING JOBS</b><span>Lower the minimum rating filter to show more jobs.</span></div>}
    {visibleJobs.map((job) => <JobCard key={job.id} job={job} now={now} selected={job.id === selection.primary} run={coordinator.run} onError={coordinator.reportError} onRemove={remove}/>)}
  </div>;
}
