import { useEffect, useRef, useState } from 'react';
import {
  downloadGeometryExport,
  downloadText,
  hydrateDesignDocument,
  inspectDesignText,
  openDesignText,
  saveDesignDocument,
  type ImportReport,
  type StepBody,
} from '../api/designIo';
import { useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { Icon } from '../shell/icons';
import { filenameStem } from '../viewport/presentation';

const ACCEPT = '.cfg,.txt,.mwg,text/plain';

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
  const [open, setOpen] = useState(false);
  const [exportsOpen, setExportsOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const openInput = useRef<HTMLInputElement>(null);
  const reportInput = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  async function act(operation: () => Promise<void>) {
    setBusy(true);
    setMessage(null);
    try { await operation(); } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
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
      replaceDesign(hydrateDesignDocument(opened.design));
      setFilename(`${filenameStem(file.name)}.cfg`);
      markSaved(useDesignStore.getState().designRevision);
      setMessage(reportText(opened));
    });
  }

  async function save() {
    await act(async () => {
      const savingRevision = revision;
      const response = await saveDesignDocument(design, filename);
      downloadText(response.text, filename || response.suggestedFilename);
      setFilename(response.suggestedFilename);
      markSaved(savingRevision);
      setMessage(`Saved ${response.suggestedFilename}`);
    });
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

  return <div ref={root} className="design-file-menu">
    <button className="file-chip" title="Design file menu" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <Icon name="folder"/><span>{filenameStem(filename)}<em>.cfg</em></span>
      {revision !== savedRevision && <i className="unsaved-dot" aria-label="Unsaved changes"/>}
      <span className="chev">⌄</span>
    </button>
    <input ref={openInput} hidden tabIndex={-1} type="file" accept={ACCEPT} onChange={(event) => void readSelected(event.currentTarget, false)}/>
    <input ref={reportInput} hidden tabIndex={-1} type="file" accept={ACCEPT} onChange={(event) => void readSelected(event.currentTarget, true)}/>
    {open && <div role="menu" aria-label="Design file menu" className="design-menu-popover">
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => openInput.current?.click()}><span>Open…</span><kbd>cfg</kbd></button>
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void save()}><span>Save</span><kbd>cfg</kbd></button>
      <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => reportInput.current?.click()}><span>Import report…</span><span>›</span></button>
      <div className="design-menu-divider"/>
      <button role="menuitem" aria-expanded={exportsOpen} className="design-menu-item" disabled={busy} onClick={() => setExportsOpen((value) => !value)}><span>Export</span><span>{exportsOpen ? '⌄' : '›'}</span></button>
      {exportsOpen && <div role="menu" aria-label="Export design" className="design-menu-nested">
        <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void exportOne('step')} title="Closed solid with walls and enclosure — imports straight into Fusion 360 or Onshape"><span>STEP solid</span><span>.step</span></button>
        <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void exportOne('step', 'surface')} title="Inner acoustic surface only, for thickening or lofting yourself"><span>STEP inner surface</span><span>.step</span></button>
        <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void exportOne('stl')}><span>STL</span><span>.stl</span></button>
        <button role="menuitem" className="design-menu-item" disabled={busy} onClick={() => void exportProfiles()}><span>Profiles CSV</span><span>2 files</span></button>
      </div>}
    </div>}
    {message && <div role="status" className="design-menu-status" onClick={() => setMessage(null)}>{message}</div>}
  </div>;
}
