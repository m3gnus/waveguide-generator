import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { DesignAvailabilityNotice, RerunButton } from '../jobs/DesignAvailability';
import { canLoadJobDesign, hydrateJobDesign, replaceWithJobDesign } from '../jobs/jobDesign';
import { canExportRun, RunExportControl } from '../jobs/RunExportControl';
import { applyJobPreferences, jobBaseName, nextVersionFor, preferencesStore, runDisplayName, usePreferences } from '../prefs/preferences';
import { JobsPreferencesSurface, ResultsPreferencesSurface } from '../prefs/PreferencesSurface';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { Icon } from './icons';
import { middleEllipsis } from './ResultsPanel';

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

export function meshWarnings(job: Pick<JobItem, 'mesh_stats'>): string[] {
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
}

function JobCard({ job, now, selected, retryJob, onError, onRemove, onOpenExportSettings }: {
  job: JobItem;
  now: number;
  selected: boolean;
  retryJob: (jobId: string) => Promise<void>;
  onError: (message: string) => void;
  onRemove: (job: JobItem) => void;
  onOpenExportSettings: () => void;
}) {
  const running = job.status === 'running' || job.status === 'queued';
  const failed = job.status === 'error';
  const cancelled = job.status === 'cancelled';
  const snapshot = hydrateJobDesign(job);
  const rating = job.rating ?? 0;
  const [editing, setEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState(job.label ?? '');
  const [displayLabel, setDisplayLabel] = useState(job.label);
  const [renameError, setRenameError] = useState<string | null>(null);
  const cancelRename = useRef(false);
  useEffect(() => {
    setDisplayLabel(job.label);
    if (!editing) setTitleDraft(job.label ?? '');
  }, [job.label]);
  const commitRename = async () => {
    if (cancelRename.current) {
      cancelRename.current = false;
      return;
    }
    const label = titleDraft.trim() ? titleDraft : null;
    if (label === job.label) {
      setEditing(false);
      return;
    }
    try {
      await jobsSocket.patchMetadata(job.id, { label });
      setDisplayLabel(label);
      setEditing(false);
      setRenameError(null);
    } catch (error) {
      setTitleDraft(job.label ?? '');
      setEditing(false);
      setRenameError(`Could not rename run: ${String(error)}`);
    }
  };
  // autoFocus alone only places the caret, and it lands after the existing
  // title, so the first character typed appends to the old name rather than
  // replacing it. Selecting has to happen once the node exists: React focuses
  // an autoFocus input during commit, ahead of the delegated onFocus handler,
  // so an onFocus prop never runs for that first focus.
  const titleInput = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (!editing) return;
    const node = titleInput.current;
    if (!node) return;
    node.focus();
    node.select();
  }, [editing]);
  // Only ever this job's own design. Falling back to whatever was on screen
  // ran a *different* waveguide under this job's name and looked like it
  // worked; RerunButton refuses instead, and says why.
  const retry = () => {
    if (!snapshot) return;
    void retryJob(job.id).catch((error) => onError(String(error)));
  };
  // A run in flight or one that failed still has to show its progress or its
  // diagnostic; only finished runs collapse down to their name.
  const expanded = selected || running || failed || cancelled || editing;
  const selectable = !running && (job.has_results || canLoadDesign(job));
  const statusWord = running ? 'Running' : failed ? 'Failed' : cancelled ? 'Cancelled' : 'Completed';
  const displayName = runDisplayName({ ...job, label: displayLabel });
  const heading = <>
    {/* The dot is hue-only, and DESIGN.md's Signal Rule requires every state to
        carry a word or a glyph as well. The word is the alternative; it costs
        no width because it is only ever read aloud. */}
    <i/><span className="sr-only">{statusWord}. </span>
    {!editing && <b className="job-title" title={displayName}>{middleEllipsis(displayName, 22)}</b>}
    {/* Stars are a label here, shown only once a run has actually been rated. */}
    {!expanded && rating > 0 && <span className="job-stars" aria-label={`Kept, rated ${rating} of 5`} title="Kept: rated runs are never cleaned up">{'★'.repeat(rating)}</span>}
  </>;
  return <article className={`job-card ${running ? 'running' : failed ? 'failed' : cancelled ? 'cancelled' : 'complete'}${selected ? ' selected' : ''}${expanded ? '' : ' collapsed'}`} aria-current={selected ? 'true' : undefined}>
    <header>
      {selectable
        ? <button className={`job-select${editing ? ' editing' : ''}`} aria-label={`Select ${displayName}`} aria-pressed={selected} title={selected ? 'Showing this run' : job.has_results ? 'Show this run in the viewport and charts' : 'Show this run design in the viewport'} onClick={() => selectJob(job)}>{heading}</button>
        : <span className={`job-select${editing ? ' editing' : ''}`} title={job.error_message ?? job.status}>{heading}</span>}
      {editing ? <input
        className="job-title-input"
        aria-label={`Title for run #${job.run_number}`}
        ref={titleInput}
        value={titleDraft}
        onChange={(event) => setTitleDraft(event.target.value)}
        onBlur={() => void commitRename()}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur();
          if (event.key === 'Escape') {
            cancelRename.current = true;
            setTitleDraft(job.label ?? '');
            setEditing(false);
          }
        }}
      /> : null}
      {!running && !editing && <button className="job-rename" aria-label={`Rename ${displayName}`} title="Rename run" onClick={() => { setRenameError(null); setEditing(true); }}>✎</button>}
      {!expanded && canExportRun(job) && <RunExportControl job={job} compact onOpenExportSettings={onOpenExportSettings}/>}
      <time>{running ? duration(secondsBetween(job.started_at ?? job.queued_at, null, now)) : clock(job.completed_at ?? job.created_at)}</time>
      {!running && <button className="job-remove" aria-label={`Remove ${displayName}`} title="Remove this job" onClick={() => onRemove(job)}><Icon name="close"/></button>}
    </header>
    {renameError && <div className="job-error job-rename-error" role="alert">{renameError}</div>}
    {job.results_discarded_at && <div className="job-retention-note">Results were cleaned up to save space.</div>}
    {running ? <>
      <p>{metrics(job, now)}</p>
      <div className="job-stage"><span>{job.stage_message ?? job.stage ?? 'waiting…'}</span><b>{Math.round(job.progress * 100)}%</b></div>
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(job.progress * 100)} aria-valuetext={`${Math.round(job.progress * 100)}% -- ${job.stage_message ?? job.stage ?? 'waiting'}`}><i style={{ width: `${Math.max(0, Math.min(100, job.progress * 100))}%` }}/></div>
      {job.log_tail.length > 0 && <p title={job.log_tail.at(-1)}>{job.log_tail.at(-1)}</p>}
      <footer><button disabled={job.cancellation_requested} onClick={() => void jobsSocket.stopJob(job.id).catch((error) => onError(String(error)))}>{job.cancellation_requested ? 'Stopping…' : 'Stop'}</button><button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Log</button></footer>
    </> : failed ? <>
      <p>failed after {duration(secondsBetween(job.started_at ?? job.queued_at, job.completed_at, now))} · {job.stage ?? 'solve'} stage</p>
      <div className="job-error" title={job.error_message ?? undefined}>{job.error_message ?? 'Simulation failed without a diagnostic.'}</div>
      <DesignAvailabilityNotice job={job}/>
      <footer><RerunButton job={job} onRerun={retry} label="Retry" className=""/><button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Open log</button></footer>
    </> : cancelled ? <>
      <p>cancelled after {duration(secondsBetween(job.started_at ?? job.queued_at, job.completed_at, now))}</p>
      <footer><RerunButton job={job} onRerun={retry}/><button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Log</button></footer>
    </> : expanded && <>
      <p>{metrics(job, now)}</p>
      {/* The mesh diagnoses that used to end the solve. The run finished, so
          these are advice about why it was slow or what it approximated --
          neither an error nor, as they were until now, silence. */}
      {meshWarnings(job).map((warning) => <div key={warning} className="job-warning" title={warning}>{warning}</div>)}
      <Rating job={job} onError={onError}/>
      {/* Selecting the run already loaded its design and results, so the only
          action left is running it again -- unless this job came from v1
          without a design Waveguide Generator can read, in which case rerunning it would
          silently run whatever is on screen instead, under its name. */}
      <DesignAvailabilityNotice job={job}/>
      <footer><RerunButton job={job} onRerun={retry}/>{canExportRun(job) && <RunExportControl job={job} onOpenExportSettings={onOpenExportSettings}/>}<button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Log</button></footer>
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
function RunNameField({ jobs, preferencesTrigger }: { jobs: readonly JobItem[]; preferencesTrigger?: ReactNode }) {
  const preferences = usePreferences();
  const [draft, setDraft] = useState(preferences.outputName);
  useEffect(() => { setDraft(preferences.outputName); }, [preferences.outputName]);
  // Keyed on the joined labels: the jobs array is replaced on every progress
  // event, and only a name appearing or disappearing can change the answer.
  const key = jobs.map((job) => job.label ?? '').join('\u0000');
  const labels = useMemo(() => key.split('\u0000'), [key]);
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
      value={draft}
      placeholder="horn"
      onChange={(event) => setDraft(event.target.value)}
      onBlur={(event) => commit(event.target.value)}
      onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
    /></label>
    {preferencesTrigger}
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
  const [exportPreferencesOpen, setExportPreferencesOpen] = useState(false);
  const preferencesAnchor = useRef<HTMLButtonElement | null>(null);
  const [query, setQuery] = useState('');
  const lastMinimumRating = useRef(preferences.minRating > 0 ? preferences.minRating : 1);
  useEffect(() => {
    if (preferences.minRating > 0) lastMinimumRating.current = preferences.minRating;
  }, [preferences.minRating]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, []);

  const preferenceJobs = useMemo(() => applyJobPreferences(snapshot.jobs, preferences.jobSort, preferences.minRating), [snapshot.jobs, preferences.jobSort, preferences.minRating]);
  const visibleJobs = useMemo(() => {
    const wanted = query.trim().toLocaleLowerCase();
    if (!wanted) return preferenceJobs;
    const numberQuery = wanted.replace(/^#/, '');
    return preferenceJobs.filter((job) => {
      const formula = String(job.config_summary.formula_type ?? '').toLocaleLowerCase();
      const title = runDisplayName(job).toLocaleLowerCase();
      const runNumberMatches = /^#?\d+$/.test(wanted) && String(job.run_number).includes(numberQuery);
      return title.includes(wanted) || runNumberMatches || formula.includes(wanted);
    });
  }, [preferenceJobs, query]);
  const visibleFailedCount = visibleJobs.filter((job) => job.status === 'error').length;
  const failedCount = snapshot.jobs.filter((job) => job.status === 'error').length;
  const activeCount = snapshot.jobs.filter((job) => job.status === 'running' || job.status === 'queued').length;
  const hiddenByFilter = snapshot.jobs.length - visibleJobs.length;
  const notConnected = snapshot.connection !== 'connected';
  const remove = (job: JobItem) => {
    if (!window.confirm(`Remove “${runDisplayName(job)}” and its saved results?`)) return;
    void jobsSocket.deleteJob(job.id).catch((error) => coordinator.reportError(String(error)));
  };
  const clearFailed = () => {
    const hiddenFailed = failedCount - visibleFailedCount;
    const hiddenCopy = hiddenFailed > 0 ? ` This includes ${hiddenFailed} failed run${hiddenFailed === 1 ? '' : 's'} hidden by the current filter.` : '';
    if (!window.confirm(`Remove all ${failedCount} failed run${failedCount === 1 ? '' : 's'} and their saved logs/designs?${hiddenCopy}`)) return;
    void jobsSocket.clearFailed().catch((error) => coordinator.reportError(String(error)));
  };

  return <div className="jobs-panel panel-scroll">
    {/* Only when there is something to report. This rail used to carry a
        permanent "0 active · connected · 31/31 shown" strip: a zero, a state
        that is almost always "connected", and a ratio that is almost always
        n/n — three readouts telling the user nothing, in the densest column of
        the application. What is left is conditional and every part of it is
        news: runs in flight, the jobs socket when it is NOT connected, the
        count only while the rating filter is actually hiding runs, and Clear
        failed only when there is something to clear. */}
    {(notConnected || hiddenByFilter > 0 || activeCount > 0 || failedCount > 0) && <div className="panel-meta">
      {activeCount > 0 && <span className="pill accent">{activeCount} running</span>}
      {notConnected && <span className="panel-meta-warn">jobs {snapshot.connection}</span>}
      {hiddenByFilter > 0 && <span title="Clear the search or turn off the kept-only filter to show these runs.">{hiddenByFilter} hidden by filter</span>}
      <span className="spacer"/>
      {failedCount > 0 && <button className="panel-text-action panel-text-action--danger" onClick={clearFailed}>Clear failed</button>}
    </div>}
    {preferencesOpen && <JobsPreferencesSurface popover anchorRef={preferencesAnchor} onClose={() => setPreferencesOpen(false)}/>}
    {exportPreferencesOpen && <ResultsPreferencesSurface popover anchorRef={preferencesAnchor} onClose={() => setExportPreferencesOpen(false)}/>}
    <RunNameField jobs={snapshot.jobs} preferencesTrigger={<button ref={preferencesAnchor} className={`panel-preferences-trigger${preferencesOpen ? ' on' : ''}`} aria-label="Job preferences" aria-expanded={preferencesOpen} title="Job preferences" onClick={() => { setExportPreferencesOpen(false); setPreferencesOpen((value) => !value); }}><Icon name="settings"/></button>}/>
    <div className="jobs-filter"><Icon name="search"/><input aria-label="Filter runs" placeholder="Filter runs" value={query} onChange={(event) => setQuery(event.target.value)}/><button className={`jobs-kept-toggle${preferences.minRating > 0 ? ' on' : ''}`} aria-label="Show kept runs only" aria-pressed={preferences.minRating > 0} title="Show kept runs only" onClick={() => preferencesStore.update({ minRating: preferences.minRating > 0 ? 0 : lastMinimumRating.current })}>★</button></div>
    {(coordinator.actionError || snapshot.error) && <div className="job-error" role="alert" style={{ margin: 7 }}>{coordinator.actionError ?? snapshot.error}</div>}
    {snapshot.jobs.length === 0 && snapshot.connection === 'connected' && <div className="empty-state"><b>No runs yet</b><span>Solve the current design to start one. Every run is kept here with its results, so you can compare and re-run it later.</span></div>}
    {snapshot.jobs.length > 0 && visibleJobs.length === 0 && <div className="empty-state"><b>No runs match the filter</b><span>{query.trim() && preferences.minRating > 0 ? 'Clear the search or turn off the kept-only filter.' : query.trim() ? 'Clear the search to show runs.' : 'Turn off the kept-only filter to show more.'}</span></div>}
    {visibleJobs.map((job) => <JobCard key={job.id} job={job} now={now} selected={job.id === selection.primary} retryJob={coordinator.retry} onError={coordinator.reportError} onRemove={remove} onOpenExportSettings={() => { setPreferencesOpen(false); setExportPreferencesOpen(true); }}/>)}
  </div>;
}
