import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { DesignAvailabilityNotice, RerunButton } from '../jobs/DesignAvailability';
import { canLoadJobDesign, hydrateJobDesign, replaceWithJobDesign } from '../jobs/jobDesign';
import { type DesignDocument } from '../stores/design';
import { applyJobPreferences, jobBaseName, nextJobNaming, nextVersionFor, preferencesStore, usePreferences } from '../prefs/preferences';
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

function meshWarnings(job: JobItem): string[] {
  const warnings = (job.mesh_stats as { warnings?: unknown } | null)?.warnings;
  return Array.isArray(warnings) ? warnings.map((warning) => String(warning)) : [];
}

export const canLoadDesign = canLoadJobDesign;

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
  if (!canLoadDesign(job)) return;
  replaceWithJobDesign(job, { keepHistory: true });
  // Reopening a config puts its design on screen, so the next solve is a new
  // take on *that* design and should be named for it -- at the next free
  // number, so it never lands back on top of the run it came from.
  const naming = nextJobNaming(job.label, jobsSocket.getSnapshot().jobs.map((item) => item.label));
  if (naming) preferencesStore.update(naming);
}

function JobCard({ job, now, selected, run, onError, onRemove }: {
  job: JobItem;
  now: number;
  selected: boolean;
  run: (design: DesignDocument, designRevision?: number) => Promise<void>;
  onError: (message: string) => void;
  onRemove: (job: JobItem) => void;
}) {
  const running = job.status === 'running' || job.status === 'queued';
  const failed = job.status === 'error';
  const snapshot = hydrateJobDesign(job);
  const rating = job.rating ?? 0;
  // Only ever this job's own design. Falling back to whatever was on screen
  // ran a *different* waveguide under this job's name and looked like it
  // worked; RerunButton refuses instead, and says why.
  const retry = () => {
    if (!snapshot) return;
    void run(snapshot, job.design_revision).catch((error) => onError(String(error)));
  };
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
      <DesignAvailabilityNotice job={job}/>
      <footer><RerunButton job={job} onRerun={retry} label="Retry" className=""/><button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Open log</button></footer>
    </> : expanded && <>
      <p>{metrics(job, now)}</p>
      {/* The mesh diagnoses that used to end the solve. The run finished, so
          these are advice about why it was slow or what it approximated --
          neither an error nor, as they were until now, silence. */}
      {meshWarnings(job).map((warning) => <div key={warning} className="job-warning" title={warning}>{warning}</div>)}
      <Rating job={job} onError={onError}/>
      {/* Selecting the run already loaded its design and results, so the only
          action left is running it again -- unless this job came from v1
          without a design v2 can read, in which case rerunning it would
          silently run whatever is on screen instead, under its name. */}
      <DesignAvailabilityNotice job={job}/>
      <footer><RerunButton job={job} onRerun={retry}/><button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Log</button></footer>
    </>}
  </article>;
}

/**
 * What the next solve will be called, and the one control that changes it.
 *
 * Naming used to live only in the preferences popover, which meant runs were
 * named by whatever was set there several sessions ago and nobody noticed until
 * the history read `horn_v09 … horn_v14`. The name belongs next to the runs it
 * names, showing the label it will actually store.
 */
function RunNameField({ jobs }: { jobs: readonly JobItem[] }) {
  const preferences = usePreferences();
  // Keyed on the joined labels: the jobs array is replaced on every progress
  // event, and only a name appearing or disappearing can change the answer.
  const key = jobs.map((job) => job.label ?? '').join(' ');
  const labels = useMemo(() => key.split(' '), [key]);
  // Committed on blur, not per keystroke: renumbering while a name is half
  // typed would settle on the version free for `hor` rather than for `horn`.
  const commit = (value: string) =>
    preferencesStore.update({ outputName: value, jobVersion: nextVersionFor(value, labels) });
  // A stored version can be stale against the history — a profile that has
  // never solved starts at v01 next to a `horn_v14`, and a browser that solved
  // elsewhere is behind. Names are not unique keys, so that would quietly
  // produce two runs called the same thing. Compared on the parsed version
  // rather than the whole label, because the date prefix moved and the runs
  // named before it are still `horn_v13`. Only ever raised: a version set
  // deliberately past the history is a choice, not a collision.
  const free = nextVersionFor(preferences.outputName, labels);
  useEffect(() => {
    if (preferences.jobVersion >= free) return;
    preferencesStore.update({ jobVersion: free });
  }, [free, preferences.jobVersion]);
  return <div className="run-name-field">
    <label className="ui-field">Run name<input
      aria-label="Run name"
      value={preferences.outputName}
      placeholder="horn"
      onChange={(event) => preferencesStore.update({ outputName: event.target.value })}
      onBlur={(event) => commit(event.target.value)}
      onKeyDown={(event) => { if (event.key === 'Enter') { commit(event.currentTarget.value); event.currentTarget.blur(); } }}
    /></label>
    <span className="run-name-preview" title="The name the next solve is stored under">next · <b>{jobBaseName(preferences)}</b></span>
  </div>;
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
    <RunNameField jobs={snapshot.jobs}/>
    {(coordinator.actionError || snapshot.error) && <div className="job-error" role="alert" style={{ margin: 7 }}>{coordinator.actionError ?? snapshot.error}</div>}
    {snapshot.jobs.length === 0 && snapshot.connection === 'connected' && <div className="coming-soon"><b>NO JOBS YET</b><span>Use Solve to run the current design.</span></div>}
    {snapshot.jobs.length > 0 && visibleJobs.length === 0 && <div className="coming-soon"><b>NO MATCHING JOBS</b><span>Lower the minimum rating filter to show more jobs.</span></div>}
    {visibleJobs.map((job) => <JobCard key={job.id} job={job} now={now} selected={job.id === selection.primary} run={coordinator.run} onError={coordinator.reportError} onRemove={remove}/>)}
  </div>;
}
