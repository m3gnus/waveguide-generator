import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  cadProjectName,
  cadProjectReference,
  listCadProjects,
  revealProjectFolder,
  runsForProject,
  type CadProject,
  type RunGroup,
  newestReturnForProject,
} from '../api/cadProjects';
import { listReturns } from '../api/cadlink';
import { cadWorkspace } from '../stores/cadWorkspace';
import { jobsSocket } from '../api/jobsSocket';
import {
  assertDesignOpenCurrent,
  openCadLinkedProject,
  takeDesignOpenTicket,
  type DesignOpenTicket,
} from '../design/openCadProject';
import { showProjectRuns } from '../jobs/runFilters';
import { workspaceNavigation } from './workspaceNavigation';
import { useCadReturnStore } from '../stores/cadReturn';
import { useDocumentStore } from '../stores/document';
import { discardConfirmation, replacingWouldLose } from '../design/replacementCheck';
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

async function openCadOnlyProject(project: CadProject, ticket: DesignOpenTicket): Promise<string> {
  // Asked of the server, not the coordinator's snapshot: that snapshot is
  // React state and may still be the pre-reload empty list while a poll is
  // in flight.
  const response = await listReturns();
  const bundle = newestReturnForProject(response.items, project);
  if (!bundle) {
    throw new Error(`No return from ${projectName(project)} is available to open. Send the document from Fusion again.`);
  }
  // After the last await, immediately before the switch: a design opened, or
  // a newer open asked for, while the list was read is the one that stays.
  assertDesignOpenCurrent(ticket, projectName(project));
  // Naming the project is what files this session's settings -- the drivers
  // above all -- under the project being opened rather than under whichever
  // one the workspace was on.
  cadWorkspace.getSnapshot().selectBundle(bundle, project.lineageId);
  return projectName(project);
}

/** Open a CAD-linked project as the switcher does: asking before a design
 * that exists nowhere else is discarded, and never over anything opened
 * since. Resolves with what was opened, or null when the user kept the design. */
export async function openCadProject(project: CadProject): Promise<string | null> {
  if (await replacingWouldLose()
    && !window.confirm(discardConfirmation('open this CAD-linked project'))) return null;
  // Taken as soon as the switch is decided, before its first await: the
  // switch applies only if nothing newer happened in between. A CAD-only
  // project replaces no design, so an edit to the design cannot overtake
  // it; a newer open or another design put on screen still can.
  const ticket = takeDesignOpenTicket({ checkEdits: Boolean(project.designId) });
  if (project.designId) {
    const opened = await openCadLinkedProject(project.designId, ticket, { loadSource: 'cad-project-switch' });
    return opened.filename;
  }
  return openCadOnlyProject(project, ticket);
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

  // Held from the first click, before the question: a second click while the
  // run list is read must not put a second question on screen.
  const busyRef = useRef(false);
  const open_ = async (project: CadProject) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      const name = await openCadProject(project);
      if (name === null) return;
      setOpen(false);
      onOpened(name);
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      busyRef.current = false;
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
      title="Project and Runs folders"
      onClick={() => setOpen((value) => !value)}
    >⋯</button>
    {open && <div role="menu" aria-label="Project folders" className="cad-project-menu cad-overflow-menu">
      {project && <button role="menuitem" title="Open this project's archive folder: every run's files, beside the captured Fusion documents." onClick={() => void reveal()}><Icon name="folder"/>Open project folder</button>}
      <button role="menuitem" disabled={!folder.path || folder.busy !== null} title="Open the Runs folder every CAD project is archived under. Its effective path is shown in Settings → WG root folder." onClick={() => { setOpen(false); void folder.open(); }}><Icon name="folder"/>Open Runs folder</button>
      <button role="menuitem" disabled={folder.busy !== null} title="Choose the WG root folder. Existing overrides and files are kept." onClick={() => { setOpen(false); void folder.select(); }}>Change WG root folder…</button>
      <div className="cad-overflow-path" title={folder.path ?? undefined}>{folder.path ?? (folder.loaded ? 'Runs folder unavailable' : 'Loading…')}</div>
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

/** The CAD adapter navigates to the one shared Runs view. */
export function CadProjectHistory() {
  const lineageId = useCurrentCadProjectLineage();
  const snapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  if (!lineageId) return null;
  const count = runsForProject(snapshot.jobs, lineageId).length;
  return <section className="cad-workflow cad-project-history">
    <button className="secondary" onClick={() => {
      showProjectRuns(lineageId);
      workspaceNavigation.navigate('jobs');
    }}>View project runs{count ? ` (${count})` : ''}</button>
  </section>;
}
