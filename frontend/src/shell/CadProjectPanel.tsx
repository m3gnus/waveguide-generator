import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  cadProjectName,
  cadProjectReference,
  groupRunsByModelState,
  listCadProjects,
  listProjectDocuments,
  projectDocumentUrl,
  revealProjectFolder,
  runsForProject,
  type CadProject,
  type CadProjectDocument,
  type RunGroup,
  newestReturnForProject,
} from '../api/cadProjects';
import { listReturns } from '../api/cadlink';
import { compareSelection } from '../api/results';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { openCadLinkedProject } from '../design/openCadProject';
import { selectJob } from './JobsPanel';
import { runDisplayName } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { useDocumentStore } from '../stores/document';
import { useUnsavedChanges } from '../stores/unsavedChanges';
import { fullTime, pluralized, relativeTime } from './cadTime';
import { Icon } from './icons';
import { middleEllipsis } from './ResultsPanel';
import { useWorkspaceFolder } from './WorkspaceFolderControls';

export { groupRunsByModelState, newestReturnForProject } from '../api/cadProjects';

/**
 * What a project is called: what CAD calls it, else the folder it owns, else
 * the design file. One rule, in `cadProjectName`, so the panel heading and the
 * file menu cannot disagree about the same project.
 */
export function projectName(
  project: Pick<CadProject, 'documentName' | 'archiveStem' | 'filename'>,
): string {
  return cadProjectName(project);
}

/**
 * Which project the CAD workspace is on, and what to call it.
 *
 * The ingested return decides, because that is the geometry on screen -- and
 * for geometry authored in CAD it is the only thing that knows the project at
 * all. Falling back to the open design's lineage keeps a WG-originated project
 * named before any return has arrived.
 *
 * The live Fusion document is deliberately not consulted. Naming the panel
 * after whatever Fusion happens to have open is what let the heading and the
 * rest of the panel disagree about which project this is.
 */
export function useCurrentCadProjectLineage(): string | null {
  const identity = useDocumentStore((state) => state.identity);
  const returned = useCadReturnStore((state) => state.ingestRecord?.project ?? null);
  return returned?.lineage_id ?? identity?.lineageId ?? null;
}

export function useCurrentCadProject(): { lineageId: string; name: string } | null {
  const returned = useCadReturnStore((state) => state.ingestRecord?.project ?? null);
  const lineageId = useCurrentCadProjectLineage();
  const [projects, setProjects] = useState<CadProject[]>([]);
  const generation = useRef(0);

  useEffect(() => {
    if (!lineageId) return;
    const request = ++generation.current;
    listCadProjects()
      .then((items) => { if (request === generation.current) setProjects(items); })
      // Advisory: a name that cannot be read leaves the project unnamed, it
      // does not take the panel down.
      .catch(() => undefined);
  }, [lineageId]);
  useEffect(() => () => { generation.current += 1; }, []);

  if (!lineageId) return null;
  const known = projects.find((project) => project.lineageId === lineageId);
  const name = known
    ? projectName(known)
    : returned?.document_name?.trim() || returned?.archive_stem?.trim() || 'Untitled project';
  return { lineageId, name };
}

/**
 * The one sentence a divider says about a stretch of runs.
 *
 * No ordinal. These groups are discovered from the runs that still exist, and
 * runs age out of the database, so "Version 3" would quietly become a different
 * geometry over time. A capture time cannot drift like that.
 */
export function modelStateLabel(group: RunGroup): string {
  const captured = group.document?.capturedAt;
  if (captured) return `Model from ${relativeTime(captured)}`;
  if (!group.returnStateHash) return 'Model state not recorded';
  return 'Earlier model';
}

function documentReason(document: CadProjectDocument | null, hash: string | null): string {
  if (!hash) return 'This run predates model capture, so no Fusion file was kept.';
  if (!document) return 'No Fusion file was archived for this model. Capture was off when it arrived, or the file has been removed.';
  if (!document.filename) return 'The record of this model survived but its Fusion file did not.';
  return '';
}

async function openCadOnlyProject(project: CadProject): Promise<string> {
  // Asked of the server, not the coordinator's snapshot: that snapshot is
  // React state and may still be the pre-reload empty list while a poll is
  // in flight.
  const response = await listReturns();
  const bundle = newestReturnForProject(response.items, project);
  if (!bundle) {
    throw new Error(`No return from ${projectName(project)} is available to open. Send the document from Fusion again.`);
  }
  cadLinkCoordinatorBridge.getSnapshot().selectBundle(bundle);
  return projectName(project);
}

function ProjectSwitcher({ current, label, onOpened, onError }: {
  /** The current project's lineage: the one identity every project has. */
  current: string | null;
  /** The trigger is the project name itself: what you would click to change it. */
  label: string;
  onOpened: (name: string) => void;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<CadProject[] | null>(null);
  const [busy, setBusy] = useState(false);
  const unsaved = useUnsavedChanges();
  const generation = useRef(0);

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    const request = ++generation.current;
    setOpen(true);
    try {
      const items = await listCadProjects();
      if (request === generation.current) setProjects(items);
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    }
  };
  useEffect(() => () => { generation.current += 1; }, []);

  const open_ = async (project: CadProject) => {
    if (unsaved && !window.confirm('Discard unsaved changes and open this CAD-linked project?')) return;
    setBusy(true);
    try {
      if (project.designId) {
        const opened = await openCadLinkedProject(
          project.designId, fetch, 'cad-project-switch',
        );
        setOpen(false);
        onOpened(opened.filename);
      } else {
        const name = await openCadOnlyProject(project);
        setOpen(false);
        onOpened(name);
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return <div className="cad-project-switcher">
    <button
      className="cad-project-trigger"
      aria-expanded={open}
      aria-haspopup="menu"
      title="Switch to a different CAD-linked project"
      onClick={() => void toggle()}
    ><span className="cad-project-name" title={label}>{label}</span><Icon name="caret"/></button>
    {open && <div role="menu" aria-label="CAD-linked projects" className="cad-project-menu">
      {projects === null && <div role="status" className="cad-detail">Loading…</div>}
      {projects?.length === 0 && <div role="status" className="cad-detail">No CAD-linked projects yet. Send a design to CAD to start one.</div>}
      {projects?.map((project) => <button
        key={project.lineageId}
        role="menuitem"
        // A project that exists only in CAD has no snapshot to open; it is
        // reopened from the newest return its document sent.
        disabled={busy}
        aria-current={project.lineageId === current ? 'true' : undefined}
        title={project.designId
          ? `Updated ${fullTime(project.updatedAt)}`
          : 'CAD-only project. Opens the newest geometry its Fusion document sent and prepares it.'}
        onClick={() => void open_(project)}
      >
        <b>{middleEllipsis(`${projectName(project)} · ${cadProjectReference(project)}`, 32)}</b>
        <span>{pluralized(project.exportCount, 'send')} · {relativeTime(project.updatedAt)}</span>
      </button>)}
    </div>}
  </div>;
}

/**
 * The folder plumbing behind a compact ⋯ menu: the project archive and the
 * shared workspace folder are one click away without occupying permanent
 * panel height. The workspace folder is the same store as Settings, so a
 * change made in either place names the same folder everywhere.
 */
function ProjectOverflowMenu({ project, onNotice, onError }: {
  project: { lineageId: string } | null;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const folder = useWorkspaceFolder();

  const reveal = async () => {
    if (!project) return;
    setOpen(false);
    try {
      onNotice(`Opened ${await revealProjectFolder(project.lineageId)}`);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return <div className="cad-project-overflow">
    <button
      className="cad-overflow-trigger"
      aria-expanded={open}
      aria-haspopup="menu"
      aria-label="Project folders"
      title="Project and workspace folders"
      onClick={() => setOpen((value) => !value)}
    >⋯</button>
    {open && <div role="menu" aria-label="Project folders" className="cad-project-menu cad-overflow-menu">
      {project && <button role="menuitem" title="Open this project's archive folder: every run's files, beside the captured Fusion documents." onClick={() => void reveal()}><Icon name="folder"/>Open project folder</button>}
      <button role="menuitem" disabled={!folder.path || folder.busy !== null} title="Open the workspace folder every CAD project is archived under. The same folder as Settings → Workspace folder." onClick={() => { setOpen(false); void folder.open(); }}><Icon name="folder"/>Open workspace folder</button>
      <button role="menuitem" disabled={folder.busy !== null} title="Choose a different workspace folder. Changes the same setting as Settings → Workspace folder." onClick={() => { setOpen(false); void folder.select(); }}>Change workspace folder…</button>
      <div className="cad-overflow-path" title={folder.path ?? undefined}>{folder.path ?? (folder.loaded ? 'No workspace folder selected' : 'Loading…')}</div>
      {folder.error && <div className="cad-projects-folder-error" role="status">{folder.error}</div>}
    </div>}
  </div>;
}

/**
 * Which project the CAD workspace is working on, and how to leave it.
 *
 * One row: the name is the switcher, the ⋯ menu holds the folder plumbing.
 * There is no rename here on purpose. WG has exactly one design name, and in
 * CAD mode the Fusion document owns it -- renaming the document in Fusion is
 * what renames this. The archive folder is frozen separately so that renaming
 * can never strand the CAD document's stored bundle path.
 */
export function CadProjectHeader() {
  const project = useCurrentCadProject();
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const snapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  const runs = runsForProject(snapshot.jobs, project?.lineageId ?? null);

  return <section className="cad-workflow cad-project">
    <header className="cad-project-row">
      <div className="cad-project-title">
        <h3 className="cad-project-heading"><ProjectSwitcher
          current={project?.lineageId ?? null}
          label={project?.name ?? 'No CAD project open'}
          onOpened={(name) => setNotice(`Opened ${name}`)}
          onError={setError}
        /></h3>
        <p className="cad-project-meta">{project
          ? (runs.length ? `${pluralized(runs.length, 'run')} in this project` : 'No runs in this project yet')
          : 'Send a design to CAD, or switch to one you already have.'}</p>
      </div>
      <ProjectOverflowMenu project={project} onNotice={setNotice} onError={setError}/>
    </header>
    {notice && <div className="cad-status-strip" role="status">{notice}</div>}
    {error && <div className="cad-alert cad-alert-error" role="alert">{error}</div>}
  </section>;
}

function RunRow({ job, selected, current, onRemove }: {
  job: JobItem;
  selected: boolean;
  current: boolean;
  onRemove: (job: JobItem) => void;
}) {
  const overlaid = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot)
    .overlays.includes(job.id);
  const name = runDisplayName(job);
  const running = job.status === 'running' || job.status === 'queued';
  return <article role="listitem" className={`cad-run-row${selected ? ' selected' : ''}`}>
    <button
      className="cad-run-open"
      aria-pressed={selected}
      title={selected ? 'Showing this run' : 'Show this run in the viewport and charts'}
      onClick={() => selectJob(job)}
    >
      <b>{middleEllipsis(name, 22)}</b>
      <span>{relativeTime(job.completed_at ?? job.created_at)}{current ? ' · on screen now' : ''}</span>
    </button>
    {(job.rating ?? 0) > 0 && <span className="job-stars" aria-label={`Rated ${job.rating} of 5`}>{'★'.repeat(job.rating ?? 0)}</span>}
    <button
      className={`cad-run-compare${overlaid ? ' on' : ''}`}
      aria-pressed={overlaid}
      disabled={!job.has_results || selected}
      title={selected ? 'This run is the one being shown' : overlaid ? 'Stop comparing this run' : 'Compare this run against the one on screen'}
      onClick={() => compareSelection.toggleOverlay(job.id)}
    >{overlaid ? 'Comparing' : 'Compare'}</button>
    {/* Same removal as the jobs rail, on the same confirmation and the same
        endpoint: this list is a view of the same runs, so it cannot offer a
        second, gentler meaning of "delete". A run still in flight is stopped
        from the jobs rail, never removed from under itself here. */}
    {!running && <button
      className="job-remove cad-run-remove"
      aria-label={`Remove ${name}`}
      title="Remove this run"
      onClick={() => onRemove(job)}
    ><Icon name="close"/></button>}
  </article>;
}

/** How many runs the panel shows before asking. Enough to see the recent
 * shape of the work; few enough that the card below stays one glance away. */
const RUNS_PREVIEW_COUNT = 5;

/**
 * This project's runs, newest first, split where the CAD geometry changed.
 *
 * Deliberately one flat list, capped at {@link RUNS_PREVIEW_COUNT} until the
 * user asks for all of it. The jobs rail answers "everything this machine
 * solved"; this answers "this project, in order". A divider marks each point
 * where the model itself changed and offers the Fusion file for the runs above
 * it, which is the whole of "that older one was better -- give it back to me".
 */
export function CadProjectHistory() {
  // Only the identity is needed here; the name is the header's business.
  const lineageId = useCurrentCadProjectLineage();
  const snapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  const selection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const liveIngestId = useCadReturnStore((state) => state.ingestRecord?.ingest_id ?? null);
  const [documents, setDocuments] = useState<CadProjectDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const generation = useRef(0);

  // The list is a projection of `jobsSocket`'s own snapshot, and deleteJob
  // drops the run from it, so the row disappears here exactly as it does in
  // the jobs rail -- no local list to keep in step.
  const remove = useCallback((job: JobItem) => {
    if (!window.confirm(`Remove “${runDisplayName(job)}” and its saved results?`)) return;
    setRemoveError(null);
    void jobsSocket.deleteJob(job.id).catch((reason) => setRemoveError(String(reason)));
  }, []);

  const refresh = useCallback(async (id: string) => {
    const request = ++generation.current;
    try {
      const listing = await listProjectDocuments(id);
      // The run list is the history; the archived models only decorate it. A
      // response this cannot read must degrade to "no Fusion files", never
      // take the history down with it.
      const items = Array.isArray(listing?.items) ? listing.items : [];
      if (request === generation.current) { setDocuments(items); setError(null); }
    } catch (reason) {
      // Advisory: the run list is still the history. Only the Fusion-file
      // column depends on this, and it says so per row.
      if (request === generation.current) setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => {
    if (!lineageId) {
      setDocuments([]);
      return;
    }
    void refresh(lineageId);
  }, [lineageId, refresh, liveIngestId]);
  useEffect(() => () => { generation.current += 1; }, []);

  if (!lineageId) return null;
  const runs = runsForProject(snapshot.jobs, lineageId);
  const groups = groupRunsByModelState(runs, documents);
  // The cap trims runs, never dividers: a partially shown group still names
  // the model its visible runs were solved from and still offers its file.
  const budget = showAll ? runs.length : RUNS_PREVIEW_COUNT;
  const shownGroups: typeof groups = [];
  let shownRuns = 0;
  for (const group of groups) {
    if (shownRuns >= budget) break;
    const take = Math.min(group.runs.length, budget - shownRuns);
    shownGroups.push(take === group.runs.length ? group : { ...group, runs: group.runs.slice(0, take) });
    shownRuns += take;
  }

  return <section className="cad-workflow cad-project-history">
    <header className="cad-project-runs-head">
      <h3>Runs</h3>
      <span
        className="cad-detail cad-runs-count"
        title="Newest first. A dashed break marks where the CAD model changed and offers the Fusion file those runs were solved from. Older runs are cleaned out of the database after 30 days unless they are rated; the archive folder keeps every run's files."
      >{runs.length ? `${runs.length} · ` : ''}<Icon name="info"/></span>
    </header>
    {error && <div className="cad-alert cad-alert-notice" role="status">Could not read the archived Fusion files: {error}</div>}
    {removeError && <div className="cad-alert cad-alert-error" role="alert">{removeError}</div>}
    {runs.length === 0 && <div className="empty-state">
      <b>No runs yet</b>
      <span>Bring geometry in from CAD and solve it. Every solve of this project is listed here with the model it came from.</span>
    </div>}
    {runs.length > 0 && <div className="cad-run-list" role="list" aria-label="Runs in this project">
      {shownGroups.map((group, index) => <div key={`${group.returnStateHash ?? 'none'}-${index}`} className="cad-run-group">
        {group.runs.map((job) => <RunRow
          key={job.id}
          job={job}
          selected={job.id === selection.primary}
          current={liveIngestId !== null && job.cad_source?.ingest_id === liveIngestId}
          onRemove={remove}
        />)}
        <ModelDivider group={group} lineageId={lineageId}/>
      </div>)}
      {runs.length > RUNS_PREVIEW_COUNT && <button
        className="link-button cad-runs-toggle"
        onClick={() => setShowAll((value) => !value)}
      >{showAll ? 'Show fewer runs' : `Show all ${runs.length} runs`}</button>}
    </div>}
  </section>;
}

function ModelDivider({ group, lineageId }: { group: RunGroup; lineageId: string }) {
  const hash = group.returnStateHash;
  const reason = documentReason(group.document, hash);
  const captured = group.document?.capturedAt;
  return <div className="cad-model-divider">
    <span className="cad-model-label" title={captured ? `Model captured ${fullTime(captured)}` : reason}>{modelStateLabel(group)}</span>
    {reason
      ? <span className="cad-model-missing" title={reason}>no Fusion file</span>
      : <a
        className="cad-model-download"
        href={projectDocumentUrl(lineageId, hash as string)}
        title="Download the Fusion model these runs were solved from. Open it in Fusion and send it back to WG to keep working on it."
      >Fusion file</a>}
  </div>;
}
