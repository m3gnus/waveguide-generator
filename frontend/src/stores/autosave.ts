import { useDesignStore, type DesignDocument } from './design';
import { useDocumentStore } from './document';

export const AUTOSAVE_KEY = 'wg2.autosave.v1';
export const AUTOSAVE_DELAY_MS = 750;

interface AutosaveRecord {
  version: 1;
  savedAt: string;
  filename: string;
  designRevision: number;
  savedRevision: number | null;
  design: DesignDocument;
}

function defaultStorage(): Storage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

function isRecord(value: unknown): value is AutosaveRecord {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  const design = record.design as Record<string, unknown> | undefined;
  return record.version === 1
    && typeof record.filename === 'string'
    && Number.isInteger(record.designRevision) && Number(record.designRevision) > 0
    && (record.savedRevision === null || (Number.isInteger(record.savedRevision) && Number(record.savedRevision) > 0))
    && Boolean(design)
    && ['OSSE', 'R-OSSE', 'ICW', 'FREEFORM'].includes(String(design?.formula))
    && typeof design?.mesh === 'object'
    && typeof design?.simulation === 'object'
    && typeof design?.source === 'object';
}

export function writeAutosave(storage: Storage | null = defaultStorage()): boolean {
  if (!storage) return false;
  const { design, designRevision } = useDesignStore.getState();
  const { filename, savedRevision } = useDocumentStore.getState();
  const record: AutosaveRecord = {
    version: 1,
    savedAt: new Date().toISOString(),
    filename,
    designRevision,
    savedRevision,
    design: structuredClone(design),
  };
  try {
    storage.setItem(AUTOSAVE_KEY, JSON.stringify(record));
    return true;
  } catch {
    return false;
  }
}

/** Restore the most recent local draft before React mounts. Autosave is crash
 * recovery, not an explicit file save, so the stored savedRevision is retained
 * and the unsaved indicator remains accurate after restart. */
export function restoreAutosave(storage: Storage | null = defaultStorage()): boolean {
  if (!storage) return false;
  let raw: string | null;
  try { raw = storage.getItem(AUTOSAVE_KEY); } catch { return false; }
  if (!raw) return false;
  try {
    const record: unknown = JSON.parse(raw);
    if (!isRecord(record)) throw new Error('Invalid autosave record');
    useDesignStore.temporal.getState().pause();
    useDesignStore.setState({
      design: structuredClone(record.design),
      designRevision: record.designRevision,
      dragSnapshot: null,
    });
    useDesignStore.temporal.getState().clear();
    useDesignStore.temporal.getState().resume();
    useDocumentStore.getState().restoreDocumentState({
      filename: record.filename,
      savedRevision: record.savedRevision,
    });
    return true;
  } catch {
    try { storage.removeItem(AUTOSAVE_KEY); } catch { /* unavailable storage stays non-fatal */ }
    return false;
  }
}

export interface AutosaveController {
  flush: () => boolean;
  dispose: () => void;
}

export function startAutosave(
  storage: Storage | null = defaultStorage(),
  delayMs = AUTOSAVE_DELAY_MS,
  windowTarget: Window | null = typeof window === 'undefined' ? null : window,
  documentTarget: Document | null = typeof document === 'undefined' ? null : document,
): AutosaveController {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const flush = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    return writeAutosave(storage);
  };
  const schedule = () => {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(flush, Math.max(0, delayMs));
  };
  const unsubscribeDesign = useDesignStore.subscribe((state, previous) => {
    if (state.designRevision !== previous.designRevision) schedule();
  });
  const unsubscribeDocument = useDocumentStore.subscribe((state, previous) => {
    if (state.filename !== previous.filename || state.savedRevision !== previous.savedRevision) schedule();
  });
  const beforeUnload = () => { flush(); };
  const visibilityChange = () => { if (documentTarget?.visibilityState === 'hidden') flush(); };
  windowTarget?.addEventListener('beforeunload', beforeUnload);
  documentTarget?.addEventListener('visibilitychange', visibilityChange);
  return {
    flush,
    dispose: () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      unsubscribeDesign();
      unsubscribeDocument();
      windowTarget?.removeEventListener('beforeunload', beforeUnload);
      documentTarget?.removeEventListener('visibilitychange', visibilityChange);
    },
  };
}
