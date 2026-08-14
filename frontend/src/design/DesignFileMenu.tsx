import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  downloadGeometryExport,
  downloadText,
  hydrateDesignDocument,
  inspectDesignText,
  openDesignText,
  saveDesignDocument,
  type ImportReport,
  type CadLinkOpenState,
  type StepBody,
} from '../api/designIo';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore, type CadLinkClassification, type DesignIdentity } from '../stores/document';
import { projectSubmittedDesign } from '../jobs/submittedProjection';
import { runNameFromFilename } from '../jobs/runNaming';
import { preferencesStore, usePreferences } from '../prefs/preferences';
import { Icon } from '../shell/icons';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { documentDisplayName, filenameStem } from '../viewport/presentation';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { workspaceModeStore } from '../stores/workspaceMode';
import { cadLinkCoordinatorBridge } from '../shell/CadLinkCoordinator';
import { sentToCadMessage } from './useSendToCad';

const ACCEPT = '.cfg,.txt,.mwg,text/plain';

const CLASSIFICATION_DISPLAY: Record<CadLinkClassification, { label: string; detail: string }> = {
  current: { label: 'current', detail: 'Current: this file matches the latest saved version in this machine’s CAD-link registry.' },
  stale_copy: { label: 'stale copy', detail: 'Stale copy: this is an older saved version. Saving will preserve both versions by creating a fork.' },
  externally_edited: { label: 'edited elsewhere', detail: 'Edited elsewhere: the design text changed outside this Waveguide Generator session.' },
  foreign: { label: 'foreign', detail: 'Foreign: this identity is not yet known to this machine’s CAD-link registry.' },
  missing: { label: 'unlinked', detail: 'Unlinked: this file has no CAD-link identity yet. Its first save will create one.' },
};

function editableIdentity(identity: DesignIdentity | null | undefined): DesignIdentity | null {
  return identity ? {
    designId: identity.designId,
    lineageId: identity.lineageId,
    baseEditVersion: identity.baseEditVersion,
  } : null;
}

function reportText(report: ImportReport): string {
  const migrations = report.migrationsApplied.length
    ? report.migrationsApplied.map((item) => item.name).join(', ')
    : 'none';
  return `${report.dialect.toUpperCase()} · migrations: ${migrations} · passthrough: ${report.passthrough.blockCount} blocks, ${report.passthrough.keyCount} keys preserved`;
}

export async function exportProfileArtifacts(
  exporter: (kind: 'profiles' | 'slices') => Promise<void>,
  revision: number,
): Promise<string> {
  const kinds = ['profiles', 'slices'] as const;
  const results = await Promise.allSettled(kinds.map((kind) => exporter(kind)));
  const completed = kinds.filter((_kind, index) => results[index].status === 'fulfilled');
  const failed = kinds.flatMap((kind, index) => {
    const result = results[index];
    return result.status === 'rejected'
      ? [`${kind}: ${result.reason instanceof Error ? result.reason.message : String(result.reason)}`]
      : [];
  });
  if (failed.length) {
    const partial = completed.length ? `Exported ${completed.join(' and ')} CSV; ` : '';
    throw new Error(`${partial}failed ${failed.join('; ')}`);
  }
  return `Exported profiles and slices CSV from revision ${revision}`;
}

export function DesignFileMenu() {
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const replaceDesign = useDesignStore((state) => state.replaceDesign);
  const filename = useDocumentStore((state) => state.filename);
  const savedRevision = useDocumentStore((state) => state.savedRevision);
  const setFilename = useDocumentStore((state) => state.setFilename);
  const markSaved = useDocumentStore((state) => state.markSaved);
  const identity = useDocumentStore((state) => state.identity);
  const classification = useDocumentStore((state) => state.classification);
  const setCadLink = useDocumentStore((state) => state.setCadLink);
  const adoptSavedIdentity = useDocumentStore((state) => state.adoptSavedIdentity);
  const workspaceMode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const cadApplication = usePreferences().cadApplication;
  const [open, setOpen] = useState(false);
  const [exportsOpen, setExportsOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [adoptionCandidate, setAdoptionCandidate] = useState<CadLinkOpenState['adoptionCandidate']>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const openInput = useRef<HTMLInputElement>(null);
  const reportInput = useRef<HTMLInputElement>(null);
  const meshInput = useRef<HTMLInputElement>(null);
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
    if (!file) return;
    await act(async () => {
      const text = await file.text();
      if (reportOnly) {
        setMessage(reportText(await inspectDesignText(text)));
        return;
      }
      const opened = await openDesignText(text);
      const openedDesign = hydrateDesignDocument(opened.design);
      replaceDesign(openedDesign);
      setFilename(`${filenameStem(file.name)}.cfg`);
      setCadLink(editableIdentity(opened.cadlink?.identity), opened.cadlink?.classification ?? 'missing');
      let nameSourceProjection = null;
      try {
        nameSourceProjection = projectSubmittedDesign(openedDesign, useSolveOptionsStore.getState().options());
      } catch { /* invalid solve-option drafts cannot make opening a design fail */ }
      preferencesStore.update({ outputName: runNameFromFilename(file.name), nameSourceProjection });
      markSaved(useDesignStore.getState().designRevision);
      setMessage(reportText(opened));
      if (opened.cadlink?.classification === 'missing') setAdoptionCandidate(opened.cadlink.adoptionCandidate);
    });
  }

  async function readMesh(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = '';
    if (!file || workspaceMode === 'cad') return;
    await act(async () => {
      const generation = importedMeshStore.beginIntent();
      const imported = createImportedMeshScene(file.name, parseMSH(await file.text()));
      importedMeshStore.setFile(imported, generation);
      setMessage(`Showing ${file.name} in the viewport · ${imported.triangleCount.toLocaleString()} triangles`);
    });
  }

  async function save() {
    await act(async () => {
      const savingRevision = revision;
      const savedName = filenameStem(filename);
      const response = await saveDesignDocument(design, filename, identity);
      downloadText(response.text, filename || response.suggestedFilename);
      setFilename(response.suggestedFilename);
      adoptSavedIdentity(response.identity);
      markSaved(savingRevision);
      setMessage(response.forked
        ? `Saved as a new fork of ${savedName}`
        : `Saved ${response.suggestedFilename}`);
    });
  }

  function newDesign() {
    if (revision !== savedRevision && !window.confirm('Discard unsaved changes and create a new design?')) return;
    resetDesignStore();
    resetDocumentStore();
    preferencesStore.update({ outputName: 'horn', nameSourceProjection: null });
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
      await downloadGeometryExport(kind, design, revision, filenameStem(filename), undefined, stepBody);
      setMessage(`Exported ${kind.toUpperCase()} from revision ${revision}`);
    });
  }

  async function exportProfiles() {
    await act(async () => {
      const result = await exportProfileArtifacts(
        (kind) => downloadGeometryExport('profiles', design, revision, filenameStem(filename), kind),
        revision,
      );
      setMessage(result);
    });
  }

  async function sendToCad() {
    await act(async () => {
      // The coordinator owns the send: it derives open-vs-update, attaches the
      // expected-document guard, and parks on the two-way conflict dialog.
      const result = await cadLinkCoordinatorBridge.getSnapshot().sendWgToFusion();
      setMessage(result ? sentToCadMessage(result) : 'Both WG and Fusion changed — choose a direction in the dialog.');
    });
  }

  return <div ref={root} className="design-file-menu">
    <button className="file-chip" title={classification ? CLASSIFICATION_DISPLAY[classification].detail : 'Design file menu'} onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <Icon name="folder"/><span>{documentDisplayName(filename) ? <>{documentDisplayName(filename)}<em>.cfg</em></> : <em>Untitled</em>}</span>
      {classification && <small
        className="cadlink-badge"
        aria-label={`CAD link: ${CLASSIFICATION_DISPLAY[classification].label}`}
        title={CLASSIFICATION_DISPLAY[classification].detail}
        style={{ color: 'var(--fg3)', fontSize: 'var(--text-micro)', fontWeight: 500, letterSpacing: '.02em' }}
      >{CLASSIFICATION_DISPLAY[classification].label}</small>}
      {revision !== savedRevision && <i className="unsaved-dot" aria-label="Unsaved changes"/>}
      <span className="chev">⌄</span>
    </button>
    <input ref={openInput} hidden tabIndex={-1} type="file" accept={ACCEPT} onChange={(event) => void readSelected(event.currentTarget, false)}/>
    <input ref={reportInput} hidden tabIndex={-1} type="file" accept={ACCEPT} onChange={(event) => void readSelected(event.currentTarget, true)}/>
    <input ref={meshInput} hidden tabIndex={-1} type="file" accept=".msh,text/plain" aria-label="Import Gmsh mesh file" onChange={(event) => void readMesh(event.currentTarget)}/>
    {open && <div role="menu" aria-label="Design file menu" className="design-menu-popover">
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={newDesign}><span>New</span><kbd>cfg</kbd></button>
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => openInput.current?.click()}><span>Open…</span><kbd>cfg</kbd></button>
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void save()}><span>Save</span><kbd>cfg</kbd></button>
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => reportInput.current?.click()}><span>Import report…</span><span>›</span></button>
      <div className="design-menu-divider"/>
      <button
        role="menuitem"
        className="design-menu-item"
        disabled={busy || workspaceMode === 'cad'}
        title={workspaceMode === 'cad' ? 'Standalone mesh import is available in Parametric mode only.' : 'Show an ASCII Gmsh 2.2 mesh in the viewport'}
        onClick={() => meshInput.current?.click()}
      ><span>Import mesh…</span><kbd>msh</kbd></button>
      <button role="menuitem" aria-expanded={exportsOpen} className="design-menu-item" disabled={busy} onClick={() => setExportsOpen((value) => !value)}><span>Export</span><span>{exportsOpen ? '⌄' : '›'}</span></button>
      {exportsOpen && <div role="menu" aria-label="Export design" className="design-menu-nested">
        {/* The .wglink bundle and its handoff are Fusion's transport; Onshape
            sends live in the CAD Link panel and go through its own adapter. */}
        {cadApplication === 'fusion360' && <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void sendToCad()} title="Write an identity-bearing .wglink bundle to the selected workspace"><span>Send to CAD</span><span>.wglink</span></button>}
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
