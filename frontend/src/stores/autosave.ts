import { useDesignStore, type DesignDocument } from './design';
import { durableSettings, namespaceStorage } from './durableSettings';
import { designNameFromFilename } from './designName';
import {
  useDocumentStore,
  type CadLinkClassification,
  type DesignIdentity,
} from './document';

export const AUTOSAVE_KEY = 'wg2.autosave.v1';

/** The draft follows the same durable path as settings: a lost browser profile
 * or a shifted port must not cost unsaved work either. */
const draftStorage = namespaceStorage('designDraft');
export const AUTOSAVE_DELAY_MS = 750;

interface AutosaveRecord {
  version: 1;
  savedAt: string;
  /** Derived from `designName`; retained so older drafts still validate. */
  filename: string;
  /** Absent in drafts written before the design carried one name. */
  designName?: string;
  savedDesignName?: string;
  designRevision: number;
  savedRevision: number | null;
  /** Absent in drafts written before directivity edits counted as changes. */
  savedSettings?: string | null;
  identity?: DesignIdentity | null;
  classification?: CadLinkClassification | null;
  design: DesignDocument;
}

type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

interface DraftSettingsSubscriber {
  subscribe(namespace: 'designDraft', listener: (raw: string | null) => void): () => void;
}

function defaultStorage(): DraftStorage | null {
  return draftStorage;
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

function isIdentity(value: unknown): value is DesignIdentity {
  if (!value || typeof value !== 'object') return false;
  const identity = value as Record<string, unknown>;
  return typeof identity.designId === 'string'
    && typeof identity.lineageId === 'string'
    && Number.isInteger(identity.baseEditVersion)
    && Number(identity.baseEditVersion) > 0;
}

function isClassification(value: unknown): value is CadLinkClassification {
  return ['current', 'stale_copy', 'externally_edited', 'foreign', 'missing'].includes(String(value));
}

function hasValidCadLink(record: AutosaveRecord): boolean {
  return (record.identity === undefined || record.identity === null || isIdentity(record.identity))
    && (record.classification === undefined || record.classification === null || isClassification(record.classification));
}

export function writeAutosave(storage: DraftStorage | null = defaultStorage()): boolean {
  if (!storage) return false;
  const { design, designRevision } = useDesignStore.getState();
  const {
    designName, filename, savedRevision, savedDesignName, savedSettings, identity, classification,
  } = useDocumentStore.getState();
  const record: AutosaveRecord = {
    version: 1,
    savedAt: new Date().toISOString(),
    filename,
    designName,
    savedDesignName,
    designRevision,
    savedRevision,
    savedSettings,
    identity: identity ? { ...identity } : null,
    classification,
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
 * recovery, not opening a new file baseline, so the stored savedRevision is
 * retained and the unsaved indicator remains accurate after restart. */
export function restoreAutosave(storage: DraftStorage | null = defaultStorage()): boolean {
  if (!storage) return false;
  let raw: string | null;
  try { raw = storage.getItem(AUTOSAVE_KEY); } catch { return false; }
  if (!raw) return false;
  try {
    const record: unknown = JSON.parse(raw);
    if (!isRecord(record) || !hasValidCadLink(record)) throw new Error('Invalid autosave record');
    useDesignStore.temporal.getState().pause();
    useDesignStore.setState({
      design: structuredClone(record.design),
      designRevision: record.designRevision,
      dragSnapshot: null,
    });
    useDesignStore.temporal.getState().clear();
    useDesignStore.temporal.getState().resume();
    useDocumentStore.getState().restoreDocumentState({
      // A draft written before the rename unification only has the filename;
      // its stem is the same name, one slugging away.
      designName: typeof record.designName === 'string'
        ? record.designName
        : designNameFromFilename(record.filename),
      savedDesignName: typeof record.savedDesignName === 'string' ? record.savedDesignName : undefined,
      savedRevision: record.savedRevision,
      savedSettings: typeof record.savedSettings === 'string' ? record.savedSettings : null,
      identity: record.identity ? { ...record.identity } : null,
      classification: record.classification ?? null,
    });
    return true;
  } catch {
    try { storage.removeItem(AUTOSAVE_KEY); } catch { /* unavailable storage stays non-fatal */ }
    return false;
  }
}

/** Restore synchronously from cache, then retry once the durable draft arrives. */
export function restoreAutosaveWithLateRetry(
  settings: DraftSettingsSubscriber = durableSettings,
  storage: DraftStorage | null = defaultStorage(),
): () => void {
  let restored = restoreAutosave(storage);
  return settings.subscribe('designDraft', () => {
    if (!restored) restored = restoreAutosave(storage);
  });
}

export interface AutosaveController {
  flush: () => boolean;
  dispose: () => void;
}

export function startAutosave(
  storage: DraftStorage | null = defaultStorage(),
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
    if (state.designName !== previous.designName
      || state.savedDesignName !== previous.savedDesignName
      || state.filename !== previous.filename
      || state.savedRevision !== previous.savedRevision
      || state.savedSettings !== previous.savedSettings
      || state.identity !== previous.identity
      || state.classification !== previous.classification) schedule();
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
