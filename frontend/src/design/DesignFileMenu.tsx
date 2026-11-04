import { activityOperation } from '../shell/activityOperation';
import { MoveRunsDialog } from '../shell/MoveRunsDialog';
import { mintDesignUid } from '../stores/designUid';
import { inspectDesignFile, rememberDesignFile, designFileSource } from './designFiles';
import { SharedDesignIdDialog, type SharedDesignQuestion } from './SharedDesignIdDialog';
import { useDesignNameDraft } from './designNameDraft';
import { useCurrentModel } from '../model/currentModel';
import { assertCurrentModelExport, parameterExportReason } from '../results/exportContext';
import { noteExplicitNavigation } from '../shell/workspaceNavigation';
import { nativeCadLimitation } from './nativeFamilies';
import { useEffect, useRef, useState } from 'react';
import {
  buildGeometryExport,
  chooseDesignFile,
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
  EXPORT_OMITS_WOOFERS,
  EXPORT_INNER_SURFACE_ONLY,
} from '../shell/exportDestinationPrompt';
import { designHasEnabledWoofers } from '../stores/assembly';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { wgSolveSettingsFromStore } from '../stores/designWire';
import { resetDocumentStore, useDocumentStore, type CadLinkClassification } from '../stores/document';
import {
  discardConfirmation,
  keptContentKeyOf,
  rememberWrittenCopy,
  replacingWouldLose,
} from './replacementCheck';
import { designFilename, designNameSlug, designNameFromFilename, UNTITLED_DESIGN } from '../stores/designName';
import { usePreferences } from '../prefs/preferences';
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
import { showCadSendOutcome, useCadSendOutcome } from './useSendToCad';
import { failureWords, isAbortError } from '../api/httpError';

// Extensions only. The macOS desktop window (pywebview on WKWebView) filters its
// open panel by the MIME types in `accept` and ignores the extensions, so a MIME
// type here greys out every file macOS does not map to it (.msh, .mwg, .cfg,
// .wgreplay). With no MIME type the panel shows every file and the handlers
// check the name.
const ACCEPT = '.cfg,.txt,.mwg,.json';
const MESH_ACCEPT = '.msh';
const REPLAY_ACCEPT = '.wgreplay';

function requireExtension(name: string, extension: string, kind: string) {
  if (!name.toLowerCase().endsWith(extension)) throw new Error(`${name} is not ${kind}. Choose a ${extension} file.`);
}

const CLASSIFICATION_DISPLAY: Record<CadLinkClassification, { label: string; detail: string }> = {
  current: { label: 'current', detail: 'Current: this file matches the latest saved version in this machine’s CAD-link registry.' },
  stale_copy: { label: 'stale copy', detail: 'Stale copy: this is an older saved version. Open the current head under CAD-linked designs before sending it to CAD.' },
  externally_edited: { label: 'edited elsewhere', detail: 'Edited elsewhere: the design text changed outside this Waveguide Generator session.' },
  foreign: { label: 'foreign', detail: 'From another computer: this computer has no CAD link for this design yet. Send it to CAD to create one.' },
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

function noteText(items: readonly { note: string }[]): string {
  return items.map(item => item.note.trim()).filter(Boolean).join(' ');
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
  const notes = noteText([...report.migrationsApplied, ...report.ignoredSettings ?? []]);
  return notes ? `${summary} · ${notes}` : summary;
}

/**
 * Ignored settings carry the server's correction for the file's author.
 * Keep the technical parse inventory behind Developer details.
 */
export function openedMessage(filename: string, report: ImportReport, developer: boolean): string {
  const details = developer ? reportText(report) : noteText(report.ignoredSettings ?? []);
  return `Opened ${filename}${details ? ` · ${details}` : ''}`;
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
  build: (kind: 'profiles' | 'slices', fetcher: typeof fetch) => Promise<GeometryExportFile>,
  write: (files: GeometryExportFile[], fetcher: typeof fetch) => Promise<{ directory: string }>,
  revision: number,
  fetcher: typeof fetch = fetch,
): Promise<string> {
  return activityOperation({ kind: 'export', label: 'Exporting profile CSV' }, fetcher, async (fetcher, activity) => {
    let done = 0;
    activity.progress(0, 3);
    const kinds = ['profiles', 'slices'] as const;
    const results = await Promise.allSettled(kinds.map(async (kind) => {
      try { return await build(kind, fetcher); }
      finally { activity.progress(++done, 3); }
    }));
    const built = results.flatMap((result) => (
      result.status === 'fulfilled' ? [result.value] : []
    ));
    const completed = kinds.filter((_kind, index) => results[index].status === 'fulfilled');
    const failed = kinds.flatMap((kind, index) => {
      const result = results[index];
      return result.status === 'rejected'
        ? [`${kind}: ${failureWords(result.reason)}`]
        : [];
    });
    // Whatever built is written first, so a reported failure never also loses the
    // half that worked.
    const written = built.length ? await write(built, fetcher) : null;
    activity.progress(3, 3);
    if (failed.length) {
      const partial = completed.length
        ? `Exported ${completed.join(' and ')} CSV${written ? ` to ${written.directory}` : ''}; `
        : '';
      throw new Error(`${partial}failed ${failed.join('; ')}`);
    }
    return `Exported profiles and slices CSV from revision ${revision}${written ? ` to ${written.directory}` : ''}`;
  });
}

/** Stable, so the rename field is focused and selected once, as it opens. */
function focusAndSelect(input: HTMLInputElement | null) {
  input?.focus();
  input?.select();
}

export function DesignFileMenu() {
  const model = useCurrentModel();
  const copyReason = parameterExportReason(model, 'mwg_config');
  const geometryReason = parameterExportReason(model, 'step');
  const design = useDesignStore((state) => state.design);
  const omitsWoofers = designHasEnabledWoofers(design) ? ` ${EXPORT_OMITS_WOOFERS}` : '';
  const innerSurfaceOnly = omitsWoofers ? ` ${EXPORT_INNER_SURFACE_ONLY}` : '';
  const revision = useDesignStore((state) => state.designRevision);
  const designName = useDocumentStore((state) => state.designName);
  const moveIdentity = useDocumentStore((state) => state.identity);
  const meshFilename = useMeshImportStore((state) => state.filename);
  const designUid = useDocumentStore((state) => state.designUid);
  const classification = useDocumentStore((state) => state.classification);
  const setCadLink = useDocumentStore((state) => state.setCadLink);
  const { cadApplication, showDeveloperDetails } = usePreferences();
  const [open, setOpen] = useState(false);
  const [exportsOpen, setExportsOpen] = useState(false);
  const [projectsOpen, setProjectsOpen] = useState(false);
  const [projects, setProjects] = useState<CadLinkedDesignSummary[]>([]);
  const [moveRunsOpen, setMoveRunsOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const nameDraft = useDesignNameDraft(designName, () => setRenaming(false));
  const [message, setMessage] = useState<string | null>(null);
  const { outcome: sendOutcome, requestId: sentRequest } = useCadSendOutcome();
  /** What a send from this menu left out, said only with that send's outcome. */
  const [sendNote, setSendNote] = useState<{ requestId: string | null; text: string } | null>(null);
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
    setSendNote(null);
    setAdoptionCandidate(null);
    try { await operation(); } catch (error) {
      if (isOutboundSupersededError(error) || isAbortError(error)) return;
      setMessage(failureWords(error));
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
        name: chosen.filename, path: chosen.source, fileKey: chosen.fileKey,
        text: async () => chosen.text!,
      }, false, ticket);
    });
  }

  async function readDesign(
    file: { name: string; path?: string; webkitRelativePath?: string; fileKey?: string; text: () => Promise<string> },
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
      if (stamp) {
        const known = inspectDesignFile(stamp, source, file.fileKey);
        let uid = known.uid;
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
          if (choice === 'new') uid = mintDesignUid();
        }
        assertDesignOpenCurrent(ticket, file.name);
        // Remember only the first observed file for a stamp. Copy choices live
        // in the open document until an explicit save/export writes its uid.
        if (uid === stamp) rememberDesignFile(stamp, source, designNameFromFilename(file.name), file.fileKey);
        opened.designUid = uid;
        if (uid !== stamp) opened.cadlink = { identity: null, classification: 'missing', adoptionCandidate: null };
      }
      applyOpenedDesign(opened, file.name);
    };
    if (nativeTicket) await operation();
    else await act(operation);
  }

  function applyOpenedDesign(opened: Awaited<ReturnType<typeof openDesignText>>, filename: string) {
    const applied = applyOpenedDocument(opened, filename);
    setMessage(openedMessage(filename, opened, showDeveloperDetails));
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
      setMessage(failureWords(error));
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
      const opened = await openCadLinkedProject(designId, ticket);
      if (opened.adoptionCandidate) setAdoptionCandidate(opened.adoptionCandidate);
      setMessage(`Opened CAD-linked design ${opened.filename}`);
    });
  }

  async function readMesh(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = '';
    if (!file || busyRef.current) return;
    await act(async () => {
      requireExtension(file.name, MESH_ACCEPT, 'a Gmsh mesh');
      if (file.size > 16 * 1024 * 1024) throw new Error('This mesh file is larger than the 16 MiB WG can import. Export a coarser mesh, then import it.');
      const generation = importedMeshStore.beginIntent();
      const readRequest = beginMeshFileImport(file.name);
      try { await loadMeshFile(file.name, await file.text(), fetch, generation); }
      catch (error) {
        if (importedMeshStore.isCurrentGeneration(generation)) useMeshImportStore.setState({ busy: false, error: failureWords(error) });
        throw error;
      } finally {
        if (useMeshImportStore.getState().request === readRequest) useMeshImportStore.setState({ busy: false });
      }
      if (!importedMeshStore.isCurrentGeneration(generation)) return;
    });
  }

  async function readReplay(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = '';
    if (!file || busyRef.current) return;
    await act(async () => {
      requireExtension(file.name, REPLAY_ACCEPT, 'a solve setup');
      if (file.size > MAX_MESH_REPLAY_BYTES) throw new Error('This file is larger than the 64 MiB WG can open. Choose a smaller file.');
      const generation = importedMeshStore.beginIntent();
      const readRequest = beginMeshFileImport(file.name);
      let restored = false;
      try { restored = await loadMeshReplay(await file.text(), fetch, generation); }
      catch (error) {
        if (importedMeshStore.isCurrentGeneration(generation)) useMeshImportStore.setState({ busy: false, error: failureWords(error) });
        throw error;
      } finally {
        if (useMeshImportStore.getState().request === readRequest) useMeshImportStore.setState({ busy: false });
      }
      if (restored) setMessage(`Opened ${file.name}. Solve uses its saved settings; editing starts a new setup.`);
    });
  }

  async function exportCopy() {
    await act(async () => {
      assertCurrentModelExport(model, 'mwg_config');
      // Asked before the file is built: cancelling must cost nothing, and the
      // question is where this export goes, not whether it can be serialized.
      const destination = await askForExportDestination({
        title: 'Export design file',
        detail: 'A text copy of this design.',
      });
      if (!destination) { setMessage(EXPORT_CANCELLED_MESSAGE); return; }
      assertCurrentModelExport(model, 'mwg_config');
      const solveState = useSolveOptionsStore.getState();
      const polarConfig = polarConfigFromUi(solveState.polar);
      // Taken before the awaits, so it describes exactly what is written.
      const writtenKey = keptContentKeyOf(design);
      const writtenUid = designUid;
      const { written, response } = await activityOperation({ kind: 'export', label: 'Exporting design file' }, fetch, async (fetcher) => {
        const response = await serializeDesignDocument(
          design, designName, fetcher, polarConfig, wgSolveSettingsFromStore(solveState), writtenUid,
        );
        const written = await writeToOutputFolder(designNameSlug(designName), [{
          filename: response.suggestedFilename,
          blob: new Blob([response.text], { type: 'text/plain;charset=utf-8' }),
        }], fetcher, 'confirm', destination.token, askToReplaceExports);
        return { written, response };
      });
      // A file now holds this design, so replacing it later loses nothing.
      rememberWrittenCopy(writtenKey);
      if (writtenUid) {
        const source = written.files[0] ?? `${written.directory}/${response.suggestedFilename}`;
        rememberDesignFile(writtenUid, source, designName || response.suggestedFilename);
      }
      setMessage(
        `Exported a copy as ${response.suggestedFilename} to ${written.directory}`
        + replacedNotice(written.replaced),
      );
    });
  }

  async function newDesign() {
    if (!await mayReplace('start a new design')) return;
    noteExplicitNavigation();
    resetDesignStore();
    // Directivity and solver settings deliberately survive New: they describe
    // how this user measures, not which horn is on screen, and resetting them
    // meant retyping the same measurement rig for every new design.
    resetDocumentStore();
    workspaceModeStore.setMode('parametric');
    setMessage('New design');
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
      assertCurrentModelExport(model, kind);
      const exportNotice = kind === 'step' && stepBody === 'solid' ? omitsWoofers : innerSurfaceOnly;
      const destination = await askForExportDestination({
        title: `Export ${kind.toUpperCase()}`,
        detail: `${designNameSlug(designName)}.${kind} from revision ${revision}.${exportNotice}`,
      });
      if (!destination) { setMessage(EXPORT_CANCELLED_MESSAGE); return; }
      assertCurrentModelExport(model, kind);
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
        + `${replacedNotice(written.replaced)}${warning}${exportNotice}`,
      );
    });
  }

  async function exportProfiles() {
    await act(async () => {
      assertCurrentModelExport(model, 'fusion_csv');
      // One question, then one more only if the folder already holds them:
      // profiles and slices are halves of one export, and both are written by
      // a single request so neither can be answered without the other.
      const destination = await askForExportDestination({
        title: 'Export profiles',
        detail: 'Profile and slice CSV files.',
      });
      if (!destination) { setMessage(EXPORT_CANCELLED_MESSAGE); return; }
      assertCurrentModelExport(model, 'fusion_csv');
      const result = await exportProfileArtifacts(
        (kind, fetcher) => buildGeometryExport(
          'profiles', design, revision, designNameSlug(designName), kind, 'solid', fetcher,
        ),
        (files, fetcher) => writeToOutputFolder(
          designNameSlug(designName), files, fetcher, 'confirm',
          destination.token, askToReplaceExports,
        ),
        revision,
      );
      setMessage(result);
    });
  }

  /** The file picker takes over from here, so a cancelled pick leaves no menu behind. */
  function pickFile(input: HTMLInputElement | null) {
    setOpen(false);
    input?.click();
  }

  async function sendToCad() {
    await act(async () => {
      assertCurrentModelExport(model, 'step');
      // The coordinator owns the send: it derives open-vs-update, attaches the
      // expected-document guard, and parks on the two-way conflict dialog.
      const result = await cadWorkspace.getSnapshot().sendWgToFusion();
      if (result) setSendNote({ requestId: result.cadHandoffRequestId ?? null, text: omitsWoofers });
      else setMessage('Both WG and Fusion changed — choose a direction in the dialog.');
    });
  }

  const documentName = model.kind === 'file' && meshFilename ? meshFilename : model.label;
  const cadLink = model.kind === 'parametric' && classification ? CLASSIFICATION_DISPLAY[classification] : null;
  const fileExtension = design.formula === 'LOOKUP' ? '.mwg' : '.cfg';
  const cadSendLimit = nativeCadLimitation(design.formula);

  return <div ref={root} className="design-file-menu">
    <button className="file-chip" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
      <span>File</span><span className="chev">⌄</span>
    </button>
    {/* A parametric design is renamed here as well as in Geometry, through the
        same draft; an imported model's name is its file's, and only shown. */}
    <span className="document-name" title={model.kind !== 'parametric' ? documentName : undefined}>
      {model.kind !== 'parametric'
        ? <span>{documentName}</span>
        : renaming
          ? <input ref={focusAndSelect} className="document-name-input" aria-label="Rename design" placeholder={UNTITLED_DESIGN}
            style={{ width: `${Math.max(nameDraft.value.length, UNTITLED_DESIGN.length) + 2}ch` }} {...nameDraft}/>
          : <button type="button" className="document-name-edit" onClick={() => setRenaming(true)}
            title={`Design file ${designFilename(designName, design.formula)}. Click to rename the design, its file and its runs.`}>
            {designName ? <>{designName}<em>{fileExtension}</em></> : <em>{UNTITLED_DESIGN}</em>}
          </button>}
      {cadLink && <small className="cadlink-badge" aria-label={`CAD link: ${cadLink.label}`} title={cadLink.detail}>{cadLink.label}</small>}
    </span>
    <input ref={openInput} hidden tabIndex={-1} type="file" accept={ACCEPT} onChange={(event) => void readSelected(event.currentTarget, false)}/>
    <input ref={reportInput} hidden tabIndex={-1} type="file" accept={ACCEPT} aria-label="Check design file" onChange={(event) => void readSelected(event.currentTarget, true)}/>
    <input ref={meshInput} hidden tabIndex={-1} type="file" accept={MESH_ACCEPT} aria-label="Import Gmsh mesh file" onChange={(event) => void readMesh(event.currentTarget)}/>
    <input ref={replayInput} hidden tabIndex={-1} type="file" accept={REPLAY_ACCEPT} aria-label="Open solve setup file" onChange={(event) => void readReplay(event.currentTarget)}/>
    <SharedDesignIdDialog question={sharedQuestion}/>
    {/* Beside the menu, not in it: the menu closes on any click outside itself,
        and this dialog is outside it. */}
    {moveRunsOpen && <MoveRunsDialog
      target={{ key: moveIdentity?.lineageId ? `lineage:${moveIdentity.lineageId}` : `design:${designUid}`, name: designName || UNTITLED_DESIGN }}
      onClose={() => setMoveRunsOpen(false)}
    />}
    {open && <div role="menu" aria-label="File" className="design-menu-popover">
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void newDesign()}><span>New design</span></button>
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void chooseLocalDesign()}><span>Open…</span><kbd>cfg</kbd></button>
      <button role="menuitem" aria-expanded={projectsOpen} className="design-menu-item" disabled={busy} onClick={() => void toggleProjects()}><span>CAD-linked designs</span><span>{projectsOpen ? '⌄' : '›'}</span></button>
      {projectsOpen && <div role="menu" aria-label="CAD-linked designs" className="design-menu-nested">
        {projects.length === 0
          ? <div className="design-menu-item" role="status"><span>No linked designs yet</span></div>
          : projects.map((project) => <button key={project.designId} role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void openProject(project)} title={`Updated ${new Date(project.updatedAt).toLocaleString()}`}>
            <span>{project.filename}</span><span>v{project.editVersion} · {project.exportCount} export{project.exportCount === 1 ? '' : 's'} · {cadProjectReference(project)}</span>
          </button>)}
      </div>}
      <div className="design-menu-divider"/>
      <button
        role="menuitem"
        className="design-menu-item"
        disabled={busy}
        title="Import an ASCII Gmsh 2.2 triangular surface mesh into Imported mesh"
        onClick={() => pickFile(meshInput.current)}
      ><span>Import mesh…</span><kbd>msh</kbd></button>
      <button role="menuitem" className="design-menu-item" disabled={busy}
        title="Open the mesh and solve settings saved from a finished mesh run, to solve them again here or on another computer"
        onClick={() => pickFile(replayInput.current)}><span>Open solve setup…</span><kbd>wgreplay</kbd></button>
      {showDeveloperDetails && <button role="menuitem" className="design-menu-item" disabled={busy}
        title="Read a design file without opening it, and show how WG parsed it"
        onClick={() => pickFile(reportInput.current)}><span>Check a design file…</span><kbd>cfg</kbd></button>}
      <div className="design-menu-divider"/>
      <button role="menuitem" aria-expanded={exportsOpen} className="design-menu-item" disabled={busy} onClick={() => setExportsOpen((value) => !value)}><span>Export</span><span>{exportsOpen ? '⌄' : '›'}</span></button>
      {exportsOpen && <div role="menu" aria-label="Export design" className="design-menu-nested">
        {geometryReason && <div className="design-menu-item" role="note">Imported models cannot export geometry or profiles. Switch to Parametric to export them.</div>}
        {copyReason && !geometryReason && <div className="design-menu-item" role="note">{copyReason}</div>}
        <button role="menuitem" className="design-menu-item" disabled={busy || Boolean(copyReason)} title={copyReason} onClick={() => void exportCopy()}><span>Design file</span><kbd>{design.formula === 'LOOKUP' ? 'mwg' : 'cfg'}</kbd></button>
        {/* The .wglink bundle and its handoff are Fusion's transport; Onshape
            sends live in the CAD Link panel and go through its own adapter. */}
        {cadApplication === 'fusion360' && <button role="menuitem" className="design-menu-item" disabled={busy || Boolean(geometryReason) || Boolean(cadSendLimit)} onClick={() => void sendToCad()} title={geometryReason ?? cadSendLimit ?? `Save this CAD model (.wglink) in the selected workspace.${omitsWoofers}`}><span>Send to CAD</span><span>.wglink</span></button>}
        {cadApplication === 'fusion360' && !geometryReason && cadSendLimit && <div className="design-menu-item" role="note">{cadSendLimit}</div>}
        <button role="menuitem" className="design-menu-item" disabled={busy || Boolean(geometryReason)} onClick={() => void exportOne('step')} title={geometryReason ?? `Closed solid with walls and enclosure — imports straight into Fusion 360 or Onshape.${omitsWoofers}`}><span>STEP solid</span><span>.step</span></button>
        <button role="menuitem" className="design-menu-item" disabled={busy || Boolean(geometryReason)} onClick={() => void exportOne('step', 'surface')} title={geometryReason ?? `Inner acoustic surface only, for thickening or lofting yourself.${innerSurfaceOnly}`}><span>STEP inner surface</span><span>.step</span></button>
        <button role="menuitem" className="design-menu-item" disabled={busy || Boolean(geometryReason)} title={parameterExportReason(model, 'stl') ?? (innerSurfaceOnly.trim() || undefined)} onClick={() => void exportOne('stl')}><span>STL</span><span>.stl</span></button>
        <button role="menuitem" className="design-menu-item" disabled={busy || Boolean(geometryReason)} title={parameterExportReason(model, 'fusion_csv')} onClick={() => void exportProfiles()}><span>Profiles CSV</span><span>2 files</span></button>
      </div>}
      {!copyReason && <>
        <div className="design-menu-divider"/>
        <button role="menuitem" className="design-menu-item" disabled={busy}
          title="Bring runs of this design from older folders into its run folder"
          onClick={() => { setOpen(false); setMoveRunsOpen(true); }}><span>Move earlier runs…</span></button>
      </>}
    </div>}
    {(message || adoptionCandidate || sendOutcome) && <div
      role={sendOutcome?.tone === 'error' ? 'alert' : 'status'}
      className={`design-menu-status${sendOutcome?.tone === 'error' ? ' error' : ''}`}
      onClick={() => { setMessage(null); setAdoptionCandidate(null); showCadSendOutcome(null); }}
    >
      {/* Both, when both exist: a menu message never hides a send's outcome. */}
      {message && message !== sendOutcome?.text && <div>{message}</div>}
      {sendOutcome && <div data-outcome="cad-send">{sendOutcome.text}{sendNote?.requestId === sentRequest ? sendNote?.text : ''}</div>}
      {adoptionCandidate && <button className="btn"
        type="button"
        onClick={(event) => { event.stopPropagation(); adoptCandidate(); }}
        style={{ marginLeft: 10, padding: '3px 6px', border: '1px solid var(--hair)', borderRadius: 4, background: 'var(--ctl-grad)' }}
      >Re-adopt CAD link</button>}
    </div>}
  </div>;
}
