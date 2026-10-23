import { mintDesignUid } from '../stores/designUid';
import { inspectDesignFile, rememberDesignFile, designFileSource } from './designFiles';
import { SharedDesignIdDialog, type SharedDesignQuestion } from './SharedDesignIdDialog';
import { nativeCadLimitation } from './nativeFamilies';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  buildGeometryExport,
  chooseDesignFile,
  saveSelectedDesignIdentity,
  exportGeometryToOutputFolder,
  inspectDesignText,
  openDesignText,
  serializeDesignDocument,
  type GeometryExportFile,
  type ImportReport,
  type CadLinkOpenState,
  type StepBody,
} from '../api/designIo';
import { writeToOutputFolder } from '../api/workspace';
import {
  askForExportDestination,
  askToReplaceExports,
  replacedNotice,
  EXPORT_CANCELLED_MESSAGE,
} from '../shell/exportDestinationPrompt';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { wgSolveSettingsFromStore } from '../stores/designWire';
import { resetDocumentStore, useDocumentStore, type CadLinkClassification } from '../stores/document';
import {
  discardConfirmation,
  keptContentKeyOf,
  rememberWrittenCopy,
  replacingWouldLose,
} from './replacementCheck';
import { designNameSlug, designNameFromFilename } from '../stores/designName';
import { usePreferences } from '../prefs/preferences';
import { Icon } from '../shell/icons';
import { polarConfigFromUi, useSolveOptionsStore } from '../stores/solveOptions';
import { MAX_MESH_REPLAY_BYTES } from '../api/meshReplay';
import { beginMeshFileImport, loadMeshFile, loadMeshReplay, useMeshImportStore } from '../stores/meshImport';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { workspaceModeStore } from '../stores/workspaceMode';
import { isOutboundSupersededError } from '../shell/cadlink/outbound';
import { cadWorkspace } from '../stores/cadWorkspace';
import { listCadLinkedDesigns, type CadLinkedDesignSummary } from '../api/cadlink';
import { cadProjectReference } from '../api/cadProjects';
import {
  applyOpenedDesign as applyOpenedDocument,
  assertDesignOpenCurrent,
  editableIdentity,
  openCadLinkedProject,
  takeDesignOpenTicket,
} from './openCadProject';
import { sentToCadMessage } from './useSendToCad';

const ACCEPT = '.cfg,.txt,.mwg,.json,text/plain,application/json';

const CLASSIFICATION_DISPLAY: Record<CadLinkClassification, { label: string; detail: string }> = {
  current: { label: 'current', detail: 'Current: this file matches the latest saved version in this machine’s CAD-link registry.' },
  stale_copy: { label: 'stale copy', detail: 'Stale copy: this is an older saved version. Open the current head under CAD-linked designs before sending it to CAD.' },
  externally_edited: { label: 'edited elsewhere', detail: 'Edited elsewhere: the design text changed outside this Waveguide Generator session.' },
  foreign: { label: 'foreign', detail: 'Foreign: this identity is not yet known to this machine’s CAD-link registry.' },
  missing: { label: 'unlinked', detail: 'Unlinked: this file has no CAD-link identity yet. Its first CAD-linked export will create one.' },
};

const NAMES_SHOWN = 8;

/**
 * Name the preserved keys or blocks, so a user can see that a key they meant
 * to matter was kept but not read. A count alone hid exactly that.
 */
function nameList(names: readonly string[] | undefined): string {
  if (!names?.length) return '';
  const shown = names.slice(0, NAMES_SHOWN).join(', ');
  const more = names.length > NAMES_SHOWN ? `, +${names.length - NAMES_SHOWN} more` : '';
  return ` (${shown}${more})`;
}

/**
 * What an opened file's parse report says, in the status line.
 *
 * Show each note, not its identifier: the server sends a sentence with every
 * migration, and `006_machine_solver_mode_not_portable` is not something a user
 * can look up. `ignoredSettings` is optional, so a server without it changes
 * nothing here.
 */
export function reportText(report: ImportReport): string {
  const { blockCount, keyCount, blocksPreserved, keysPreserved } = report.passthrough;
  const summary = `${report.dialect.toUpperCase()} · migrations: ${report.migrationsApplied.length || 'none'} · passthrough: ${blockCount} blocks${nameList(blocksPreserved)}, ${keyCount} keys preserved${nameList(keysPreserved)}`;
  const notes = [...report.migrationsApplied, ...report.ignoredSettings ?? []]
    .map((item) => item.note.trim())
    .filter(Boolean);
  return notes.length ? `${summary} · ${notes.join(' ')}` : summary;
}

/**
 * Export the profile pair: build both files, then write them once.
 *
 * Building stays parallel -- it only reads -- but the two files are **one**
 * write. They used to be two, and with a destination folder that meant two
 * replacement questions racing for a dialog that answers one at a time: the
 * second was auto-declined, so half the export landed and the message said the
 * whole of it had. One write is one question covering both names, and one
 * atomic publish.
 *
 * A half that fails to build is still reported by name, and the half that built
 * is still written -- that part is unchanged.
 */
export async function exportProfileArtifacts(
  build: (kind: 'profiles' | 'slices') => Promise<GeometryExportFile>,
  write: (files: GeometryExportFile[]) => Promise<{ directory: string }>,
  revision: number,
): Promise<string> {
  const kinds = ['profiles', 'slices'] as const;
  const results = await Promise.allSettled(kinds.map((kind) => build(kind)));
  const built = results.flatMap((result) => (
    result.status === 'fulfilled' ? [result.value] : []
  ));
  const completed = kinds.filter((_kind, index) => results[index].status === 'fulfilled');
  const failed = kinds.flatMap((kind, index) => {
    const result = results[index];
    return result.status === 'rejected'
      ? [`${kind}: ${result.reason instanceof Error ? result.reason.message : String(result.reason)}`]
      : [];
  });
  // Whatever built is written first, so a reported failure never also loses the
  // half that worked.
  const written = built.length ? await write(built) : null;
  if (failed.length) {
    const partial = completed.length
      ? `Exported ${completed.join(' and ')} CSV${written ? ` to ${written.directory}` : ''}; `
      : '';
    throw new Error(`${partial}failed ${failed.join('; ')}`);
  }
  return `Exported profiles and slices CSV from revision ${revision}${written ? ` to ${written.directory}` : ''}`;
}

export function DesignFileMenu() {
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const designName = useDocumentStore((state) => state.designName);
  const designUid = useDocumentStore((state) => state.designUid);
  const classification = useDocumentStore((state) => state.classification);
  const setCadLink = useDocumentStore((state) => state.setCadLink);
  const workspaceMode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const cadApplication = usePreferences().cadApplication;
  const [open, setOpen] = useState(false);
  const [exportsOpen, setExportsOpen] = useState(false);
  const [projectsOpen, setProjectsOpen] = useState(false);
  const [projects, setProjects] = useState<CadLinkedDesignSummary[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [adoptionCandidate, setAdoptionCandidate] = useState<CadLinkOpenState['adoptionCandidate']>(null);
  const [sharedQuestion, setSharedQuestion] = useState<SharedDesignQuestion | null>(null);
  const pendingSharedQuestion = useRef<SharedDesignQuestion | null>(null);
  useEffect(() => () => pendingSharedQuestion.current?.settle(null), []);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const openInput = useRef<HTMLInputElement>(null);
  const reportInput = useRef<HTMLInputElement>(null);
  const meshInput = useRef<HTMLInputElement>(null);
  const replayInput = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  async function act(operation: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setMessage(null);
    setAdoptionCandidate(null);
    try { await operation(); } catch (error) {
      if (isOutboundSupersededError(error)) return;
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      busyRef.current = false;
      setBusy(false);
      setOpen(false);
    }
  }

  async function readSelected(input: HTMLInputElement, reportOnly: boolean) {
    const file = input.files?.[0];
    input.value = '';
    if (file) await readDesign(file, reportOnly);
  }

  async function chooseLocalDesign() {
    if (busyRef.current) return;
    // Reserve the open before showing the native dialog, just like a browser
    // selection reserves it before reading the file.
    const ticket = takeDesignOpenTicket();
    await act(async () => {
      const chosen = await chooseDesignFile();
      if (chosen === null) { openInput.current?.click(); return; }
      if (!chosen.selected || !chosen.filename || chosen.text === undefined) return;
      await readDesign({
        name: chosen.filename, path: chosen.source, fileKey: chosen.fileKey, selectedFile: chosen.selectedFile,
        text: async () => chosen.text!,
      }, false, ticket);
    });
  }

  async function readDesign(
    file: { name: string; path?: string; webkitRelativePath?: string; fileKey?: string; selectedFile?: string; text: () => Promise<string> },
    reportOnly: boolean,
    nativeTicket?: ReturnType<typeof takeDesignOpenTicket>,
  ) {
    // `busyRef` guards re-entrancy the same way `mayReplace` does, and before
    // the ticket: a call that is about to be a no-op (`act` below refuses it
    // too) must not still bump the open-request counter and make a real
    // in-flight open look superseded by nothing.
    if (!file || (busyRef.current && !nativeTicket)) return;
    // Taken now, before the first await, so an open asked for earlier --
    // including one already in flight for this same input -- becomes stale.
    // The other opens (the CAD-linked project switcher, `openProject` above)
    // take theirs the same way and refuse just as unconditionally below;
    // this one used to ask "the design changed while loading, replace it?"
    // instead, which let whichever answer arrived last win the race no
    // matter which was actually newer.
    const ticket = nativeTicket ?? takeDesignOpenTicket();
    const operation = async () => {
      const text = await file.text();
      if (reportOnly) {
        setMessage(reportText(await inspectDesignText(text)));
        return;
      }
      // Parsing is deliberately side-effect free. The discard decision belongs
      // immediately beside the one operation that replaces the document, so a
      // malformed file cannot prompt and an edit made while either await was
      // pending cannot be overwritten by a stale response.
      const opened = await openDesignText(text);
      // Taken after every await, including the run-list read inside it, so
      // what is decided is the design on screen at the moment it is replaced.
      const wouldLose = await replacingWouldLose();
      if (wouldLose && !window.confirm(discardConfirmation(`open ${file.name}`))) return;
      // Refuses, with the same structured message every other open uses,
      // when a newer open was asked for, another load applied first, or the
      // design, its name or a file-owned solve setting changed while this
      // one was in flight. Immediately before the write, so nothing between
      // here and `applyOpenedDesign` can race it.
      assertDesignOpenCurrent(ticket, file.name);
      const source = designFileSource(file);
      const stamp = opened.designUid;
      let identityWriteNotice: string | undefined;
      if (stamp) {
        const known = inspectDesignFile(stamp, source, file.fileKey);
        let uid = known.uid;
        let fileKey = file.fileKey;
        let persisted = true;
        if (known.originalName) {
          const choice = await new Promise<'keep' | 'new' | null>((resolve) => {
            const question: SharedDesignQuestion = {
              name: known.originalName!,
              settle: (answer) => {
                pendingSharedQuestion.current = null;
                setSharedQuestion(null);
                resolve(answer);
              },
            };
            pendingSharedQuestion.current = question;
            setSharedQuestion(question);
          });
          if (choice === null) return;
          assertDesignOpenCurrent(ticket, file.name);
          if (choice === 'new') {
            uid = mintDesignUid();
            persisted = false;
            if (file.selectedFile) {
              try {
                const saved = await saveSelectedDesignIdentity(file.selectedFile, uid);
                fileKey = saved.fileKey;
                persisted = true;
              } catch (error) {
                identityWriteNotice = `Opened as a new design, but could not save its id: ${error instanceof Error ? error.message : String(error)}. You will be asked again next time.`;
              }
            } else {
              identityWriteNotice = 'Opened as a new design. Export a copy to keep its new id; this browser cannot update the selected file. You will be asked again next time.';
            }
          }
        }
        assertDesignOpenCurrent(ticket, file.name);
        if (persisted) rememberDesignFile(uid, source, designNameFromFilename(file.name), fileKey);
        opened.designUid = uid;
        if (uid !== stamp) opened.cadlink = { identity: null, classification: 'missing', adoptionCandidate: null };
      }
      applyOpenedDesign(opened, file.name);
      if (identityWriteNotice) setMessage(identityWriteNotice);
    };
    if (nativeTicket) await operation();
    else await act(operation);
  }

  function applyOpenedDesign(opened: Awaited<ReturnType<typeof openDesignText>>, filename: string) {
    const applied = applyOpenedDocument(opened, filename);
    setMessage(reportText(opened));
    if (applied.adoptionCandidate) setAdoptionCandidate(applied.adoptionCandidate);
  }

  async function toggleProjects() {
    if (projectsOpen) {
      setProjectsOpen(false);
      return;
    }
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setMessage(null);
    try {
      // This menu opens designs; a project that exists only in CAD has no
      // snapshot to open, so it belongs to the CAD Link tab and not here.
      setProjects((await listCadLinkedDesigns()).items.filter((item) => item.designId));
      setProjectsOpen(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  /**
   * Whether the design on screen may be replaced: nothing would be lost, or
   * the user said so. The menu is held busy while it asks, so a second click
   * during the run-list read cannot put a second question on screen.
   */
  async function mayReplace(replacement: string): Promise<boolean> {
    if (busyRef.current) return false;
    busyRef.current = true;
    try {
      return !(await replacingWouldLose()) || window.confirm(discardConfirmation(replacement));
    } finally {
      busyRef.current = false;
    }
  }

  async function openProject(project: CadLinkedDesignSummary) {
    if (!project.designId) return;
    const designId = project.designId;
    if (!await mayReplace('open this CAD-linked design')) return;
    // Taken as soon as the replacement is decided, before the open's first
    // await: the open applies only if nothing newer happened in between, and
    // says what did if something had.
    const ticket = takeDesignOpenTicket();
    await act(async () => {
      const opened = await openCadLinkedProject(designId, ticket, { loadSource: 'cad-project-switch' });
      if (opened.adoptionCandidate) setAdoptionCandidate(opened.adoptionCandidate);
      setMessage(`Opened CAD-linked design ${opened.filename}`);
    });
  }

  async function readMesh(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = '';
    if (!file || busyRef.current) return;
    await act(async () => {
      if (file.size > 16 * 1024 * 1024) throw new Error('Mesh exceeds the 16 MiB import limit.');
      const generation = importedMeshStore.beginIntent();
      const readRequest = beginMeshFileImport(file.name);
      try { await loadMeshFile(file.name, await file.text(), fetch, generation); }
      catch (error) {
        if (importedMeshStore.isCurrentGeneration(generation)) useMeshImportStore.setState({ busy: false, error: String(error) });
        throw error;
      } finally {
        if (useMeshImportStore.getState().request === readRequest) useMeshImportStore.setState({ busy: false });
      }
      if (!importedMeshStore.isCurrentGeneration(generation)) return;
      setMessage(`Imported ${file.name}. Choose units and Radiating surfaces in Geometry, then prepare the mesh.`);
    });
  }

  async function readReplay(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = '';
    if (!file || busyRef.current) return;
    await act(async () => {
      if (file.size > MAX_MESH_REPLAY_BYTES) throw new Error('Mesh replay exceeds the 64 MiB import limit.');
      const generation = importedMeshStore.beginIntent();
      const readRequest = beginMeshFileImport(file.name);
      let restored = false;
      try { restored = await loadMeshReplay(await file.text(), fetch, generation); }
      catch (error) {
        if (importedMeshStore.isCurrentGeneration(generation)) useMeshImportStore.setState({ busy: false, error: String(error) });
        throw error;
      } finally {
        if (useMeshImportStore.getState().request === readRequest) useMeshImportStore.setState({ busy: false });
      }
      if (restored) setMessage(`Opened ${file.name}. Solve preserves the replay settings; editing starts a new setup.`);
    });
  }

  async function exportCopy() {
    await act(async () => {
      // Asked before the file is built: cancelling must cost nothing, and the
      // question is where this export goes, not whether it can be serialized.
      const destination = await askForExportDestination({
        title: 'Export a copy',
        detail: 'A text copy of this design.',
      });
      if (!destination) { setMessage(EXPORT_CANCELLED_MESSAGE); return; }
      const solveState = useSolveOptionsStore.getState();
      const polarConfig = polarConfigFromUi(solveState.polar);
      // Taken before the awaits, so it describes exactly what is written.
      const writtenKey = keptContentKeyOf(design);
      const writtenUid = designUid;
      const response = await serializeDesignDocument(
        design, designName, fetch, polarConfig, wgSolveSettingsFromStore(solveState), writtenUid,
      );
      const written = await writeToOutputFolder(designNameSlug(designName), [{
        filename: response.suggestedFilename,
        blob: new Blob([response.text], { type: 'text/plain;charset=utf-8' }),
      }], fetch, 'confirm', destination.token, askToReplaceExports);
      // A file now holds this design, so replacing it later loses nothing.
      rememberWrittenCopy(writtenKey);
      if (writtenUid) {
        const source = written.files[0] ?? `${written.directory}/${response.suggestedFilename}`;
        rememberDesignFile(writtenUid, source, designName || response.suggestedFilename);
        // The browser fallback cannot expose a path; remember its visible name too.
        rememberDesignFile(writtenUid, response.suggestedFilename, designName || response.suggestedFilename);
      }
      setMessage(
        `Exported a copy as ${response.suggestedFilename} to ${written.directory}`
        + replacedNotice(written.replaced),
      );
    });
  }

  async function newDesign() {
    if (!await mayReplace('start a new design')) return;
    resetDesignStore();
    // Directivity and solver settings deliberately survive New: they describe
    // how this user measures, not which horn is on screen, and resetting them
    // meant retyping the same measurement rig for every new design.
    resetDocumentStore();
    setMessage('New design');
    setAdoptionCandidate(null);
    setOpen(false);
  }

  function duplicateDesign() {
    useDesignStore.getState().replaceDocument(design);
    useDocumentStore.getState().duplicateAsNewDesign();
    setMessage('Duplicated as new design');
    setAdoptionCandidate(null);
    setOpen(false);
  }

  function adoptCandidate() {
    if (!adoptionCandidate) return;
    setCadLink(editableIdentity(adoptionCandidate), 'current');
    setMessage(`Re-adopted CAD link for ${adoptionCandidate.filename}`);
    setAdoptionCandidate(null);
  }

  async function exportOne(kind: 'step' | 'stl', stepBody: StepBody = 'solid') {
    await act(async () => {
      const destination = await askForExportDestination({
        title: `Export ${kind.toUpperCase()}`,
        detail: `${designNameSlug(designName)}.${kind} from revision ${revision}.`,
      });
      if (!destination) { setMessage(EXPORT_CANCELLED_MESSAGE); return; }
      const written = await exportGeometryToOutputFolder(
        kind, design, revision, designNameSlug(designName), undefined, stepBody, fetch,
        destination.token, askToReplaceExports,
      );
      // Naming the folder is the point: the desktop window has no download
      // shelf, so an export that does not say where it went looks like one
      // that did not happen.
      const warning = written.warning ? ` Warning: ${written.warning}` : '';
      setMessage(
        `Exported ${kind.toUpperCase()} from revision ${revision} to ${written.directory}.`
        + `${replacedNotice(written.replaced)}${warning}`,
      );
    });
  }

  async function exportProfiles() {
    await act(async () => {
      // One question, then one more only if the folder already holds them:
      // profiles and slices are halves of one export, and both are written by
      // a single request so neither can be answered without the other.
      const destination = await askForExportDestination({
        title: 'Export profiles',
        detail: 'Profile and slice CSV files.',
      });
      if (!destination) { setMessage(EXPORT_CANCELLED_MESSAGE); return; }
      const result = await exportProfileArtifacts(
        (kind) => buildGeometryExport(
          'profiles', design, revision, designNameSlug(designName), kind, 'solid', fetch,
        ),
        (files) => writeToOutputFolder(
          designNameSlug(designName), files, fetch, 'confirm',
          destination.token, askToReplaceExports,
        ),
        revision,
      );
      setMessage(result);
    });
  }

  async function sendToCad() {
    await act(async () => {
      // The coordinator owns the send: it derives open-vs-update, attaches the
      // expected-document guard, and parks on the two-way conflict dialog.
      const result = await cadWorkspace.getSnapshot().sendWgToFusion();
      setMessage(result ? sentToCadMessage(result) : 'Both WG and Fusion changed — choose a direction in the dialog.');
    });
  }

  return <div ref={root} className="design-file-menu">
    <button className="file-chip" title={classification ? CLASSIFICATION_DISPLAY[classification].detail : 'Design file menu'} onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <Icon name="folder"/><span>{designName ? <>{designName}<em>{design.formula === 'LOOKUP' ? '.mwg' : '.cfg'}</em></> : <em>Untitled</em>}</span>
      {classification && <small
        className="cadlink-badge"
        aria-label={`CAD link: ${CLASSIFICATION_DISPLAY[classification].label}`}
        title={CLASSIFICATION_DISPLAY[classification].detail}
        style={{ color: 'var(--fg3)', fontSize: 'var(--text-micro)', fontWeight: 500, letterSpacing: '.02em' }}
      >{CLASSIFICATION_DISPLAY[classification].label}</small>}
      <span className="chev">⌄</span>
    </button>
    {workspaceMode === 'cad' && <button type="button" className="file-chip import-mesh-action" disabled={busy}
      title="Import an ASCII Gmsh 2.2 surface mesh" onClick={() => meshInput.current?.click()}>
      <Icon name="folder"/>Import mesh…
    </button>}
    <input ref={openInput} hidden tabIndex={-1} type="file" accept={ACCEPT} onChange={(event) => void readSelected(event.currentTarget, false)}/>
    <input ref={reportInput} hidden tabIndex={-1} type="file" accept={ACCEPT} onChange={(event) => void readSelected(event.currentTarget, true)}/>
    <input ref={meshInput} hidden tabIndex={-1} type="file" accept=".msh,text/plain" aria-label="Import Gmsh mesh file" onChange={(event) => void readMesh(event.currentTarget)}/>
    <input ref={replayInput} hidden tabIndex={-1} type="file" accept=".wgreplay,application/json" aria-label="Open mesh replay file" onChange={(event) => void readReplay(event.currentTarget)}/>
    <SharedDesignIdDialog question={sharedQuestion}/>
    {open && <div role="menu" aria-label="Design file menu" className="design-menu-popover">
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void newDesign()}><span>New</span><kbd>cfg</kbd></button>
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void chooseLocalDesign()}><span>Open…</span><kbd>cfg</kbd></button>
      <button role="menuitem" aria-expanded={projectsOpen} className="design-menu-item" disabled={busy} onClick={() => void toggleProjects()}><span>CAD-linked designs</span><span>{projectsOpen ? '⌄' : '›'}</span></button>
      {projectsOpen && <div role="menu" aria-label="CAD-linked designs" className="design-menu-nested">
        {projects.length === 0
          ? <div className="design-menu-item" role="status"><span>No linked designs yet</span></div>
          : projects.map((project) => <button key={project.designId} role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void openProject(project)} title={`Updated ${new Date(project.updatedAt).toLocaleString()}`}>
            <span>{project.filename}</span><span>v{project.editVersion} · {project.exportCount} export{project.exportCount === 1 ? '' : 's'} · {cadProjectReference(project)}</span>
          </button>)}
      </div>}
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={duplicateDesign}><span>Duplicate as new design</span></button>
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void exportCopy()}><span>Export a copy</span><kbd>{design.formula === 'LOOKUP' ? 'mwg' : 'cfg'}</kbd></button>
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => reportInput.current?.click()}><span>Import report…</span><span>›</span></button>
      <div className="design-menu-divider"/>
      <button
        role="menuitem"
        className="design-menu-item"
        disabled={busy}
        title="Import an ASCII Gmsh 2.2 triangular surface mesh into Imported mesh"
        onClick={() => meshInput.current?.click()}
      ><span>Import mesh…</span><kbd>msh</kbd></button>
      <button role="menuitem" className="design-menu-item" disabled={busy}
        title="Open the retained mesh and exact solve settings in Imported mesh"
        onClick={() => replayInput.current?.click()}><span>Open mesh replay…</span><kbd>wgreplay</kbd></button>
      <button role="menuitem" aria-expanded={exportsOpen} className="design-menu-item" disabled={busy} onClick={() => setExportsOpen((value) => !value)}><span>Export</span><span>{exportsOpen ? '⌄' : '›'}</span></button>
      {exportsOpen && <div role="menu" aria-label="Export design" className="design-menu-nested">
        {/* The .wglink bundle and its handoff are Fusion's transport; Onshape
            sends live in the CAD Link panel and go through its own adapter. */}
        {cadApplication === 'fusion360' && <button role="menuitem" className="design-menu-item" disabled={busy || Boolean(nativeCadLimitation(design.formula))} onClick={() => void sendToCad()} title={nativeCadLimitation(design.formula) ?? 'Write an identity-bearing .wglink bundle to the selected workspace'}><span>Send to CAD</span><span>.wglink</span></button>}
        <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void exportOne('step')} title="Closed solid with walls and enclosure — imports straight into Fusion 360 or Onshape"><span>STEP solid</span><span>.step</span></button>
        <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void exportOne('step', 'surface')} title="Inner acoustic surface only, for thickening or lofting yourself"><span>STEP inner surface</span><span>.step</span></button>
        <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void exportOne('stl')}><span>STL</span><span>.stl</span></button>
        <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void exportProfiles()}><span>Profiles CSV</span><span>2 files</span></button>
      </div>}
    </div>}
    {(message || adoptionCandidate) && <div role="status" className="design-menu-status" onClick={() => { setMessage(null); setAdoptionCandidate(null); }}>
      {message}
      {adoptionCandidate && <button
        type="button"
        onClick={(event) => { event.stopPropagation(); adoptCandidate(); }}
        style={{ marginLeft: 10, padding: '3px 6px', border: '1px solid var(--hair)', borderRadius: 4, background: 'var(--ctl-grad)' }}
      >Re-adopt CAD link</button>}
    </div>}
  </div>;
}
