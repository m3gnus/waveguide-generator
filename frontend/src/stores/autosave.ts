import { hydrateDesignDocument, importNotice, importStoredDesign, needsDesignImport } from '../api/designIo';
import { isDesignUid } from './designUid';
import { NATIVE_FAMILIES, useDesignStore, type DesignDocument } from './design';
import { DESIGN_CONTENT_KEY_VERSION } from './designContentKey';
import { durableSettings, namespaceStorage, type DurableSettings } from './durableSettings';
import { designNameFromFilename } from './designName';
import {
  useDocumentStore,
  type CadLinkClassification,
  type DesignIdentity,
} from './document';
import { ProgramFault } from '../api/httpError';

export const AUTOSAVE_KEY = 'wg2.autosave.v1';
/** The settings namespace a draft that failed to restore is kept in. Never read back by WG. */
export const REJECTED_DRAFTS_NAMESPACE = 'rejectedDesignDraft';
/** How many set-aside drafts are kept, newest first, and the size they must fit in. */
export const MAX_REJECTED_DRAFTS = 5;
/** Under the server's 256 KiB per-namespace limit, measured the way it measures. */
export const MAX_REJECTED_DRAFT_BYTES = 240 * 1024;

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
  designUid?: string;
  legacyDesignName?: string;
  designRevision: number;
  /** Retired baseline; v1 readers still require this wire field. */
  savedRevision: number | null;
  identity?: DesignIdentity | null;
  classification?: CadLinkClassification | null;
  /**
   * The document's `openedContentKey` when the draft was written: the content
   * WG last opened or wrote somewhere it can be had back from. Stored beside
   * the draft, never computed from it. Absent in drafts written before it was
   * kept, and such a draft counts as kept nowhere.
   */
  openedContentKey?: string | null;
  /** The key format `openedContentKey` was written under (`DESIGN_CONTENT_KEY_VERSION`). */
  contentKeyVersion?: string;
  design: DesignDocument;
}

type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

interface DraftSettingsSubscriber {
  subscribe(namespace: 'designDraft', listener: (raw: string | null) => void): () => void;
}

function defaultStorage(): DraftStorage | null {
  return draftStorage;
}

/** What setting a rejected draft aside needs from the durable settings. */
export type DraftSetAsideSettings = Pick<DurableSettings, 'get' | 'set' | 'retireAfterUpload' | 'afterHydration'>;

interface RejectedDrafts {
  version: 1;
  drafts: { rejectedAt: string; draft: string }[];
}

/** The size the server will count: the namespace value is a string it JSON-encodes, escaping non-ASCII. */
function storedBytes(kept: RejectedDrafts): number {
  const encoded = JSON.stringify(JSON.stringify(kept));
  let bytes = 0;
  for (let index = 0; index < encoded.length; index += 1) bytes += encoded.charCodeAt(index) < 0x80 ? 1 : 6;
  return bytes;
}

function readRejectedDrafts(existing: string | null): RejectedDrafts {
  const kept: RejectedDrafts = { version: 1, drafts: [] };
  if (!existing) return kept;
  try {
    const parsed = JSON.parse(existing) as Partial<RejectedDrafts>;
    if (parsed?.version === 1 && Array.isArray(parsed.drafts)) {
      kept.drafts = parsed.drafts.filter((entry) => typeof entry?.draft === 'string');
      return kept;
    }
  } catch { /* kept below as an entry of its own */ }
  kept.drafts.push({ rejectedAt: '', draft: existing });
  return kept;
}

/**
 * Add `raw` to the set-aside drafts: newest last, at most `MAX_REJECTED_DRAFTS`
 * and within the server's size limit, the oldest dropped first. Returns null
 * when `raw` alone does not fit, and how many older drafts had to go.
 */
function withRejectedDraft(existing: string | null, raw: string): { kept: RejectedDrafts; dropped: number } | null {
  const kept = readRejectedDrafts(existing);
  if (!kept.drafts.some((entry) => entry.draft === raw)) {
    kept.drafts.push({ rejectedAt: new Date().toISOString(), draft: raw });
  }
  let dropped = 0;
  while (kept.drafts.length > MAX_REJECTED_DRAFTS
    || (kept.drafts.length > 1 && storedBytes(kept) > MAX_REJECTED_DRAFT_BYTES)) {
    kept.drafts.shift();
    dropped += 1;
  }
  return storedBytes(kept) > MAX_REJECTED_DRAFT_BYTES ? null : { kept, dropped };
}

function draftSavedAt(raw: string): string | null {
  try {
    const savedAt = (JSON.parse(raw) as { savedAt?: unknown })?.savedAt;
    if (typeof savedAt !== 'string') return null;
    const when = new Date(savedAt);
    return Number.isNaN(when.getTime()) ? null : when.toLocaleString();
  } catch {
    return null;
  }
}

function rejectedDraftNotice(raw: string, setAside: { dropped: number } | null): string {
  const savedAt = draftSavedAt(raw);
  const which = savedAt ? `The unsaved draft from ${savedAt}` : 'The unsaved draft from your last session';
  if (!setAside) {
    return `${which} could not be restored, so WG opened without it. It is too large to set aside, so it `
      + 'stays where it is and WG will try it again at the next start. If this keeps happening, use '
      + 'Report a problem in the top bar.';
  }
  const dropped = setAside.dropped === 0 ? '' : ` To make room, the oldest ${setAside.dropped === 1
    ? 'draft set aside earlier was' : `${setAside.dropped} drafts set aside earlier were`} removed; at most `
    + `${MAX_REJECTED_DRAFTS} are kept.`;
  return `${which} could not be restored, so WG opened without it. It was not deleted: it is kept in `
    + `ui_settings.json in WG's data folder, in the settings entry “${REJECTED_DRAFTS_NAMESPACE}”, so a later `
    + 'version of WG or support can recover it. Until that copy is saved, the draft also stays where it was.'
    + `${dropped} Use Report a problem in the top bar to tell us about it.`;
}

/**
 * Keep a draft that failed to restore, beside the ones kept before it.
 *
 * It is unsaved work, so it leaves the restore slot only once the server has
 * acknowledged the set-aside copy (`retireAfterUpload`); until then, or if the
 * server never takes it, it stays where it was. The set-aside list is read
 * only after hydration has applied the server's copy, so a stale browser
 * cache cannot overwrite drafts another session set aside.
 */
async function setDraftAside(raw: string, settings: DraftSetAsideSettings): Promise<void> {
  await settings.afterHydration();
  const setAside = withRejectedDraft(settings.get(REJECTED_DRAFTS_NAMESPACE), raw);
  if (setAside) settings.set(REJECTED_DRAFTS_NAMESPACE, JSON.stringify(setAside.kept));
  useDesignStore.setState({ loadNotice: rejectedDraftNotice(raw, setAside) });
  if (setAside) await settings.retireAfterUpload('designDraft', [REJECTED_DRAFTS_NAMESPACE], raw);
}

function isRecord(value: unknown): value is AutosaveRecord {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  const design = record.design as Record<string, unknown> | undefined;
  return record.version === 1
    && (record.designUid === undefined || isDesignUid(record.designUid))
    && (record.legacyDesignName === undefined || typeof record.legacyDesignName === 'string')
    && typeof record.filename === 'string'
    && Number.isInteger(record.designRevision) && Number(record.designRevision) > 0
    && (record.savedRevision === null || (Number.isInteger(record.savedRevision) && Number(record.savedRevision) > 0))
    && Boolean(design)
    && (['OSSE', 'R-OSSE', 'ICW', 'FREEFORM', ...NATIVE_FAMILIES].includes(String(design?.formula)) || needsDesignImport(design ?? {}))
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

/**
 * The remembered-copy key the record stored, or null. A key written under
 * another format, or none at all, cannot be compared with one computed now,
 * so it keeps nothing -- while the draft beside it still restores.
 */
function storedOpenedContentKey(record: AutosaveRecord): string | null {
  const key = record.openedContentKey;
  return record.contentKeyVersion === DESIGN_CONTENT_KEY_VERSION
    && typeof key === 'string'
    && key.startsWith(`${DESIGN_CONTENT_KEY_VERSION}:`)
    ? key
    : null;
}

// A pending or failed import must survive tab hiding and shutdown. A real
// editor/document change supersedes it; a notice-only state change does not.
let importedDraft: { design: DesignDocument; revision: number; document: ReturnType<typeof useDocumentStore.getState> } | null = null;

export function writeAutosave(storage: DraftStorage | null = defaultStorage()): boolean {
  if (!storage) return false;
  const { design, designRevision } = useDesignStore.getState();
  if (importedDraft) {
    if (design === importedDraft.design && designRevision === importedDraft.revision
      && useDocumentStore.getState() === importedDraft.document) return false;
    importedDraft = null;
  }
  const {
    designName, filename, openedContentKey, identity, classification, designUid, legacyDesignName,
  } = useDocumentStore.getState();
  const record: AutosaveRecord = {
    version: 1,
    savedAt: new Date().toISOString(),
    filename,
    designName,
    designUid,
    legacyDesignName,
    designRevision,
    savedRevision: null,
    identity: identity ? { ...identity } : null,
    classification,
    openedContentKey,
    contentKeyVersion: DESIGN_CONTENT_KEY_VERSION,
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
 * recovery. The replacement check's remembered-copy key is the one the record
 * stored beside the draft, so a draft still equal to what was last
 * opened or written stays kept after a restart, and an edited one does not.
 * No key is ever derived from the draft itself. */
let restoreGeneration = 0;

export function restoreAutosave(
  storage: DraftStorage | null = defaultStorage(),
  settings: DraftSetAsideSettings = durableSettings,
): boolean {
  if (!storage) return false;
  let raw: string | null;
  try { raw = storage.getItem(AUTOSAVE_KEY); } catch { return false; }
  if (!raw) return false;
  try {
    const record: unknown = JSON.parse(raw);
    if (!isRecord(record) || !hasValidCadLink(record)) throw new ProgramFault('Invalid autosave record');
    const generation = ++restoreGeneration;
    const initialDesign = useDesignStore.getState();
    const initialDocument = useDocumentStore.getState();
    const apply = (design: DesignDocument, notice: string | null = null) => {
      useDocumentStore.getState().restoreDocumentState({
        // A draft written before the rename unification only has the filename;
        // its stem is the same name, one slugging away.
        designName: typeof record.designName === 'string'
          ? record.designName
          : designNameFromFilename(record.filename),
        openedContentKey: storedOpenedContentKey(record),
        designUid: record.designUid,
        legacyDesignName: record.legacyDesignName,
        identity: record.identity ? { ...record.identity } : null,
        classification: record.classification ?? null,
      });
      // Announced last, once the document state it pairs with is in place, so a
      // restore after mount reloads the preview and resets its revision floor.
      useDesignStore.getState().replaceDocument(design, {
        revision: record.designRevision,
        loadSource: 'autosave-restore',
        notice,
      });
    };
    if (needsDesignImport(record.design)) {
      importedDraft = { design: initialDesign.design, revision: initialDesign.designRevision, document: initialDocument };
      // Never install a retired family in live editor state. Keep the saved
      // draft until the same server importer used by file Open has succeeded.
      void importStoredDesign(record.design).then((opened) => {
        if (generation !== restoreGeneration || initialDesign !== useDesignStore.getState()
          || initialDocument !== useDocumentStore.getState()) return;
        const design = hydrateDesignDocument(opened.design);
        const notice = importNotice(opened);
        importedDraft = null;
        apply(design, notice);
      }).catch(() => {
        if (generation === restoreGeneration && initialDesign === useDesignStore.getState()) {
          useDesignStore.setState({ loadNotice: 'The saved draft could not be imported. It is retained; restart WG to try restoring it again.' });
        }
      });
    } else apply(record.design);
    return true;
  } catch {
    // Never delete unsaved work: the draft is set aside, and leaves the
    // restore slot only once that copy is safely stored.
    void setDraftAside(raw, settings);
    return false;
  }
}

/** Restore synchronously from cache, then retry once the durable draft arrives. */
export function restoreAutosaveWithLateRetry(
  settings: DraftSettingsSubscriber = durableSettings,
  storage: DraftStorage | null = defaultStorage(),
): () => void {
  restoreAutosave(storage);
  let restoredDesignState = useDesignStore.getState();
  let restoredDocumentState = useDocumentStore.getState();
  return settings.subscribe('designDraft', () => {
    // Hydration may finish after startup already restored the browser cache.
    // Adopt that durable draft only while the restored state is untouched;
    // otherwise the user's intervening work is newer and autosave must publish
    // it back instead of replacing it with the late server response.
    if (useDesignStore.getState() !== restoredDesignState
      || useDocumentStore.getState() !== restoredDocumentState) return;
    if (restoreAutosave(storage)) {
      restoredDesignState = useDesignStore.getState();
      restoredDocumentState = useDocumentStore.getState();
    }
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
    if (state.legacyDesignName !== previous.legacyDesignName
      || state.designUid !== previous.designUid
      || state.designName !== previous.designName
      || state.filename !== previous.filename
      // A design file export and Send to CAD change only this: the draft must record
      // the copy they wrote, or a restart forgets it.
      || state.openedContentKey !== previous.openedContentKey
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
