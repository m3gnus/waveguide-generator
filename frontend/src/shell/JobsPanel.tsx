import { memo, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { DesignAvailabilityNotice, RerunButton } from '../jobs/DesignAvailability';
import { canLoadJobDesign, replaceWithJobDesign } from '../jobs/jobDesign';
import { showJobModel } from '../jobs/showJobModel';
import { canExportRun, RunExportControl } from '../jobs/RunExportControl';
import { nextRunLabel } from '../jobs/runNaming';
import { useRunNameSource } from '../jobs/runNameSource';
import { applyJobPreferences, preferencesStore, runDisplayName, usePreferences } from '../prefs/preferences';
import { JobsPreferencesSurface, ResultsPreferencesSurface } from '../prefs/PreferencesSurface';
import { useDocumentStore } from '../stores/document';
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
  if (job.config_summary.geometry_type === 'imported') {
    void showJobModel(job);
    return;
  }
  // Undoable: browsing runs must not be able to discard the working design.
  if (!canLoadDesign(job)) return;
  replaceWithJobDesign(job, { keepHistory: true });
}

/** The stage the passive-cardioid radiation-impedance campaign reports under. */
export const RADIATION_IMPEDANCE_STAGE = 'radiation_impedance';

/**
 * Download the port-exit radiation-impedance matrix this run produced.
 *
 * Gated on the server's own flag rather than on the run having been a cardioid
 * solve: the archive is subject to retention, so a run that once had one may
 * not have it now, and offering a download that 404s is worse than not
 * offering it.
 */
export function RadiationImpedanceButton({ job }: { job: JobItem }) {
  if (!job.has_radiation_impedance_artifact) return null;
  return <button
    title="Download the port-exit radiation-impedance matrix (NPZ) solved for this run's passive-cardioid campaign"
    onClick={() => window.open(`/api/radiation-impedance/${encodeURIComponent(job.id)}`, '_blank')}
  >Radiation Z</button>;
}

export interface JobCardProps {
  job: JobItem;
  now: number;
  selected: boolean;
  retryJob: (jobId: string) => Promise<void>;
  onError: (message: string) => void;
  onRemove: (job: JobItem) => void;
  onOpenExportSettings: () => void;
}

export function jobCardPropsEqual(previous: JobCardProps, next: JobCardProps): boolean {
  if (
    previous.job !== next.job
    || previous.selected !== next.selected
    || previous.retryJob !== next.retryJob
    || previous.onError !== next.onError
    || previous.onRemove !== next.onRemove
    || previous.onOpenExportSettings !== next.onOpenExportSettings
  ) return false;
  const active = (status: JobItem['status']) => status === 'running' || status === 'queued';
  // `now` only feeds the live elapsed clock. Finished cards use their stored
  // completion timestamp, so repainting all of them every second is pure work.
  return (!active(previous.job.status) && !active(next.job.status)) || previous.now === next.now;
}

const JobCard = memo(function JobCard({ job, now, selected, retryJob, onError, onRemove, onOpenExportSettings }: JobCardProps) {
  const running = job.status === 'running' || job.status === 'queued';
  const failed = job.status === 'error';
  const cancelled = job.status === 'cancelled';
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
  const retry = () => { void retryJob(job.id).catch((error) => onError(String(error))); };
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
    {job.config_summary.geometry_type === 'imported' && <span className="pill accent" title="Solved from a CAD-return ingestion">CAD import</span>}
    {/* Stars are a label here, shown only once a run has actually been rated. */}
    {!expanded && rating > 0 && <span className="job-stars" aria-label={`Kept, rated ${rating} of 5`} title="Kept: rated runs are never cleaned up">{'★'.repeat(rating)}</span>}
  </>;
  return <article role="listitem" className={`job-card ${running ? 'running' : failed ? 'failed' : cancelled ? 'cancelled' : 'complete'}${selected ? ' selected' : ''}${expanded ? '' : ' collapsed'}`} aria-current={selected ? 'true' : undefined}>
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
      {/* A run title is an annotation on history -- "the one with the deeper
          throat" -- and deliberately does not rename the design. The Design
          name field above does that. */}
      {selected && !running && !editing && <button className="job-rename" aria-label={`Rename ${displayName}`} title="Rename this run (does not rename the design)" onClick={() => { setRenameError(null); setEditing(true); }}>✎</button>}
      <time>{running ? duration(secondsBetween(job.started_at ?? job.queued_at, null, now)) : clock(job.completed_at ?? job.created_at)}</time>
      {!running && <button className="job-remove" aria-label={`Remove ${displayName}`} title="Remove this job" onClick={() => onRemove(job)}><Icon name="close"/></button>}
    </header>
    {renameError && <div className="job-error job-rename-error" role="alert">{renameError}</div>}
    {job.results_discarded_at && <div className="job-retention-note">Results were cleaned up to save space.</div>}
    {running ? <>
      <p>{metrics(job, now)}</p>
      <div className="job-stage"><span>{job.stage_message ?? job.stage ?? 'waiting…'}</span><b>{Math.round(job.progress * 100)}%</b></div>
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(job.progress * 100)} aria-valuetext={`${Math.round(job.progress * 100)}% -- ${job.stage_message ?? job.stage ?? 'waiting'}`}><i style={{ width: `${Math.max(0, Math.min(100, job.progress * 100))}%` }}/></div>
      {/* The stage message names the campaign; this says why the run has
          stopped on it. Without that, a passive-cardioid solve looks like a
          20-second stall in the middle of an otherwise familiar progress bar. */}
      {job.stage === RADIATION_IMPEDANCE_STAGE && <p className="job-stage-note">Extra pass before the main solve: the passive-cardioid port needs its own radiation-impedance matrix over the port aperture.</p>}
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
      <footer><RerunButton job={job} onRerun={retry}/>{selected && canExportRun(job) && <RunExportControl job={job} onOpenExportSettings={onOpenExportSettings}/>}<RadiationImpedanceButton job={job}/><button onClick={() => window.open(`/api/jobs/${encodeURIComponent(job.id)}/log`, '_blank')}>Log</button></footer>
    </>}
  </article>;
}, jobCardPropsEqual);

/**
 * The design's name, and the label the next solve will be stored under.
 *
 * This is the one editable name in WG. It renames the document, the file it
 * saves as, the `Report.Title` inside that file, and every run and export made
 * from it -- which is what stopped the run list, the file chip, and the `.cfg`
 * from each answering to a different name.
 */
function RunNameField({ actions, now = new Date() }: { actions?: ReactNode; now?: Date }) {
  const preferences = usePreferences();
  const setDesignName = useDocumentStore((state) => state.setDesignName);
  // In CAD mode the name belongs to the Fusion document, so the field reports
  // it instead of offering an edit WG could not write back to CAD.
  const { name, origin } = useRunNameSource();
  const displayedLabel = nextRunLabel(name, preferences, now);
  const [draft, setDraft] = useState(name);
  const [editing, setEditing] = useState(false);
  useEffect(() => { if (!editing) setDraft(name); }, [name, editing]);
  const commit = (value: string) => {
    setDesignName(value);
    setEditing(false);
  };
  const fromCad = origin === 'cad';
  return <div className="run-name-field">
    <label className="ui-field">{fromCad ? 'CAD document' : 'Design name'}<input
      aria-label={fromCad ? 'CAD document name' : 'Design name'}
      value={fromCad ? name : draft}
      readOnly={fromCad}
      placeholder={fromCad ? 'No CAD return selected' : 'Untitled'}
      title={fromCad
        ? 'CAD Link runs are named by the Fusion document. Rename the document in Fusion 360 and send it again to change it.'
        : 'Names the design, its Download a copy file, and every run and export made from it'}
      onChange={(event) => setDraft(event.target.value)}
      onFocus={() => { if (!fromCad) setEditing(true); }}
      onBlur={(event) => { if (!fromCad) commit(event.target.value); }}
      onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
    /></label>
    <div className="run-name-actions">{actions}</div>
    <span className="run-name-preview" title="The name the next solve is stored under">next · <b>{displayedLabel}</b></span>
  </div>;
}

export function JobsPanel({ namingNow = new Date() }: { namingNow?: Date } = {}) {
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
  const remove = useCallback((job: JobItem) => {
    if (!window.confirm(`Remove “${runDisplayName(job)}” and its saved results?`)) return;
    void jobsSocket.deleteJob(job.id).catch((error) => coordinator.reportError(String(error)));
  }, [coordinator.reportError]);
  const openExportSettings = useCallback(() => {
    setPreferencesOpen(false);
    setExportPreferencesOpen(true);
  }, []);
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
        count only while the rating filter is actually hiding runs. Clear
        failed lives beside the run name below, where history actions are. */}
    {(notConnected || hiddenByFilter > 0 || activeCount > 0) && <div className="panel-meta">
      {activeCount > 0 && <span className="pill accent">{activeCount} running</span>}
      {notConnected && <span className="panel-meta-warn">jobs {snapshot.connection}</span>}
      {hiddenByFilter > 0 && <span title="Clear the search or turn off the kept-only filter to show these runs.">{hiddenByFilter} hidden by filter</span>}
    </div>}
    {preferencesOpen && <JobsPreferencesSurface
      popover
      anchorRef={preferencesAnchor}
      onClose={() => setPreferencesOpen(false)}
      now={namingNow}
    />}
    {exportPreferencesOpen && <ResultsPreferencesSurface popover anchorRef={preferencesAnchor} onClose={() => setExportPreferencesOpen(false)}/>}
    <RunNameField now={namingNow} actions={<>
      {failedCount > 0 && <button className="panel-text-action panel-text-action--danger" title={`Remove ${failedCount} failed run${failedCount === 1 ? '' : 's'}`} onClick={clearFailed}>Clear failed</button>}
      <button ref={preferencesAnchor} className={`panel-preferences-trigger${preferencesOpen ? ' on' : ''}`} aria-label="Job preferences" aria-expanded={preferencesOpen} title="Job preferences" onClick={() => { setExportPreferencesOpen(false); setPreferencesOpen((value) => !value); }}><Icon name="settings"/></button>
    </>}/>
    <div className="jobs-filter"><Icon name="search"/><input aria-label="Filter runs" placeholder="Filter runs" value={query} onChange={(event) => setQuery(event.target.value)}/><button className={`jobs-kept-toggle${preferences.minRating > 0 ? ' on' : ''}`} aria-label="Show kept runs only" aria-pressed={preferences.minRating > 0} title="Show kept runs only" onClick={() => preferencesStore.update({ minRating: preferences.minRating > 0 ? 0 : lastMinimumRating.current })}>★</button></div>
    {(coordinator.actionError || snapshot.error) && <div className="job-error" role="alert" style={{ margin: 7 }}>{coordinator.actionError ?? snapshot.error}</div>}
    {snapshot.jobs.length === 0 && snapshot.connection === 'connected' && <div className="empty-state"><b>No runs yet</b><span>Solve the current design to start one. Every run is kept here with its results, so you can compare and re-run it later.</span></div>}
    {snapshot.jobs.length > 0 && visibleJobs.length === 0 && <div className="empty-state"><b>No runs match the filter</b><span>{query.trim() && preferences.minRating > 0 ? 'Clear the search or turn off the kept-only filter.' : query.trim() ? 'Clear the search to show runs.' : 'Turn off the kept-only filter to show more.'}</span></div>}
    {visibleJobs.length > 0 && <div className="job-list" role="list" aria-label="Run history">
      {visibleJobs.map((job) => <JobCard key={job.id} job={job} now={now} selected={job.id === selection.primary} retryJob={coordinator.retry} onError={coordinator.reportError} onRemove={remove} onOpenExportSettings={openExportSettings}/>)}
    </div>}
  </div>;
}
