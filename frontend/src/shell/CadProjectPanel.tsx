import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  groupRunsByModelState,
  listCadProjects,
  listProjectDocuments,
  projectDocumentUrl,
  revealProjectFolder,
  runsForProject,
  type CadProject,
  type CadProjectDocument,
  type RunGroup,
} from '../api/cadProjects';
import { compareSelection } from '../api/results';
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

export { groupRunsByModelState } from '../api/cadProjects';

/** What a project is called: the folder it owns, else the design file. */
export function projectName(project: Pick<CadProject, 'archiveStem' | 'filename'>): string {
  return project.archiveStem?.trim() || project.filename.replace(/\.[^.]+$/, '');
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

function ProjectSwitcher({ current, onOpened, onError }: {
  current: string | null;
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
      const opened = await openCadLinkedProject(project.designId);
      setOpen(false);
      onOpened(opened.filename);
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return <div className="cad-project-switcher">
    <button
      className="link-button"
      aria-expanded={open}
      aria-haspopup="menu"
      title="Open a different CAD-linked project"
      onClick={() => void toggle()}
    >Switch{open ? ' ⌄' : ' ›'}</button>
    {open && <div role="menu" aria-label="CAD-linked projects" className="cad-project-menu">
      {projects === null && <div role="status" className="cad-detail">Loading…</div>}
      {projects?.length === 0 && <div role="status" className="cad-detail">No CAD-linked projects yet. Send a design to CAD to start one.</div>}
      {projects?.map((project) => <button
        key={project.designId}
        role="menuitem"
        disabled={busy}
        aria-current={project.designId === current ? 'true' : undefined}
        title={`Updated ${fullTime(project.updatedAt)}`}
        onClick={() => void open_(project)}
      >
        <b>{middleEllipsis(projectName(project), 24)}</b>
        <span>{pluralized(project.exportCount, 'send')} · {relativeTime(project.updatedAt)}</span>
      </button>)}
    </div>}
  </div>;
}

/**
 * Which project the CAD workspace is working on, and how to leave it.
 *
 * There is no rename here on purpose. WG has exactly one design name, and in
 * CAD mode the Fusion document owns it -- renaming the document in Fusion is
 * what renames this. The archive folder is frozen separately so that renaming
 * can never strand the CAD document's stored bundle path.
 */
export function CadProjectHeader({ documentName }: { documentName: string | null }) {
  const identity = useDocumentStore((state) => state.identity);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const snapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  const runs = runsForProject(snapshot.jobs, identity?.lineageId ?? null);

  const reveal = async () => {
    if (!identity?.lineageId) return;
    setError(null);
    try {
      setNotice(`Opened ${await revealProjectFolder(identity.lineageId)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  if (!identity?.lineageId) {
    return <section className="cad-workflow cad-project">
      <header className="cad-workflow-header no-step">
        <div><h3>No CAD project open</h3><p>Send a design to CAD, or switch to one you already have.</p></div>
        <ProjectSwitcher current={null} onOpened={(name) => setNotice(`Opened ${name}`)} onError={setError}/>
      </header>
      {notice && <div className="cad-status-strip" role="status">{notice}</div>}
      {error && <div className="cad-alert cad-alert-error" role="alert">{error}</div>}
    </section>;
  }

  return <section className="cad-workflow cad-project">
    <header className="cad-workflow-header no-step">
      <div>
        <h3 className="cad-project-name" title={documentName ?? undefined}>{documentName ?? 'Untitled project'}</h3>
        <p>{runs.length ? `${pluralized(runs.length, 'run')} in this project` : 'No runs in this project yet'}</p>
      </div>
      <ProjectSwitcher current={identity.designId ?? null} onOpened={(name) => setNotice(`Opened ${name}`)} onError={setError}/>
    </header>
    <button className="cad-secondary-action" onClick={() => void reveal()}><Icon name="folder"/>Open project folder</button>
    {notice && <div className="cad-status-strip" role="status">{notice}</div>}
    {error && <div className="cad-alert cad-alert-error" role="alert">{error}</div>}
  </section>;
}

function RunRow({ job, selected, current }: { job: JobItem; selected: boolean; current: boolean }) {
  const overlaid = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot)
    .overlays.includes(job.id);
  const name = runDisplayName(job);
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
  </article>;
}

/**
 * This project's runs, newest first, split where the CAD geometry changed.
 *
 * Deliberately one flat list. The jobs rail answers "everything this machine
 * solved"; this answers "this project, in order". A divider marks each point
 * where the model itself changed and offers the Fusion file for the runs above
 * it, which is the whole of "that older one was better -- give it back to me".
 */
export function CadProjectHistory() {
  const identity = useDocumentStore((state) => state.identity);
  const lineageId = identity?.lineageId ?? null;
  const snapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  const selection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const liveIngestId = useCadReturnStore((state) => state.ingestRecord?.ingest_id ?? null);
  const [documents, setDocuments] = useState<CadProjectDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

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

  return <section className="cad-workflow cad-project-history">
    <header className="cad-workflow-header no-step">
      <div><h3>Runs in this project</h3><p>Newest first. A break marks where the CAD model changed.</p></div>
    </header>
    {error && <div className="cad-alert cad-alert-notice" role="status">Could not read the archived Fusion files: {error}</div>}
    {runs.length === 0 && <div className="empty-state">
      <b>No runs yet</b>
      <span>Bring geometry in from CAD and solve it. Every solve of this project is listed here with the model it came from.</span>
    </div>}
    {runs.length > 0 && <div className="cad-run-list" role="list" aria-label="Runs in this project">
      {groups.map((group, index) => <div key={`${group.returnStateHash ?? 'none'}-${index}`} className="cad-run-group">
        {group.runs.map((job) => <RunRow
          key={job.id}
          job={job}
          selected={job.id === selection.primary}
          current={liveIngestId !== null && job.cad_source?.ingest_id === liveIngestId}
        />)}
        <ModelDivider group={group} lineageId={lineageId}/>
      </div>)}
      <p className="cad-detail cad-history-foot">Older runs are cleaned out of the database after 30 days unless they are rated. The archive folder keeps every run's files.</p>
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
