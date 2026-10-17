import { findPreparedModel } from '../api/meshImport';
import { namespaceStorage } from './durableSettings';

/**
 * The model Imported mode reopens at start: one durable pointer.
 *
 * It replaces two older ones -- the saved mesh setup's `{model_id, prepared_id}`
 * (or, before model records, a bare prepared id) and the remembered CAD
 * project's lineage. Both named a model, so the first start after an upgrade
 * migrates whichever exists into this pointer and drops them.
 */
export type LastModelInput = 'file' | 'cad';
export interface LastModel { model_id: string; input: LastModelInput }

const storage = namespaceStorage('lastModel');
const legacyMesh = namespaceStorage('meshImport');
const legacyCad = namespaceStorage('cadProject');
const KEY = 'lastModel';

type Pointers = Partial<Record<LastModelInput, string>>;
type Failures = Partial<Record<LastModelInput, number>>;
interface Stored { pointers: Pointers; failed: Failures }

/** One pointer per input, so reopening never flips the input a person last chose,
 * and beside each the count of consecutive starts that failed to open it.
 * The single `{model_id, input}` form an earlier build wrote is read as one entry. */
function parseStored(raw: string | null): Stored {
  const out: Stored = { pointers: {}, failed: {} };
  if (!raw) return out;
  try {
    const value = JSON.parse(raw) as { model_id?: unknown; input?: unknown; file?: unknown; cad?: unknown; failed?: Record<string, unknown> } | null;
    const take = (input: LastModelInput, id: unknown) => { if (typeof id === 'string' && id.trim()) out.pointers[input] = id.trim(); };
    take('file', value?.file); take('cad', value?.cad);
    if (value?.input === 'file' || value?.input === 'cad') take(value.input, value.model_id);
    for (const input of ['file', 'cad'] as const) {
      const count = value?.failed?.[input];
      if (out.pointers[input] && typeof count === 'number' && count > 0) out.failed[input] = Math.floor(count);
    }
  } catch { /* an unreadable pointer is none */ }
  return out;
}

const parse = (raw: string | null): Pointers => parseStored(raw).pointers;

function writeStored({ pointers, failed }: Stored): void {
  if (!pointers.file && !pointers.cad) { storage.removeItem(KEY); return; }
  const kept = Object.fromEntries((['file', 'cad'] as const).filter((input) => pointers[input] && failed[input]).map((input) => [input, failed[input]]));
  storage.setItem(KEY, JSON.stringify({ ...pointers, ...(Object.keys(kept).length ? { failed: kept } : {}) }));
}

function write(pointers: Pointers): void {
  writeStored({ pointers, failed: parseStored(storage.getItem(KEY)).failed });
}

/** The pointer of one input as stored; the legacy pointers are not consulted (see `migrateLastModel`). */
export function readLastModel(input: LastModelInput): LastModel | null {
  const id = parse(storage.getItem(KEY))[input];
  return id ? { model_id: id, input } : null;
}

function dropLegacy(): void {
  if (legacyMesh.getItem(KEY) !== null) legacyMesh.removeItem(KEY);
  if (legacyCad.getItem(KEY) !== null) legacyCad.removeItem(KEY);
}

/** Point at the model on screen. A successful open also ends its run of failed starts, and settles the legacy pointers. */
export function rememberLastModel(model: LastModel): void {
  const stored = parseStored(storage.getItem(KEY));
  if (stored.pointers[model.input] !== model.model_id || stored.failed[model.input]) {
    stored.pointers[model.input] = model.model_id;
    delete stored.failed[model.input];
    writeStored(stored);
  }
  dropLegacy();
}

/** Starts in a row that failed to open this pointer, after the one just noted. */
export const LAST_MODEL_FAILURE_LIMIT = 2;

/**
 * A start failed to open this pointer for a reason that may pass. Keep it for
 * the next start, and clear it once `LAST_MODEL_FAILURE_LIMIT` starts in a row
 * have failed. Returns true when it was cleared.
 */
export function noteLastModelFailure(pointer: LastModel): boolean {
  const stored = parseStored(storage.getItem(KEY));
  if (stored.pointers[pointer.input] !== pointer.model_id) return false;
  const count = (stored.failed[pointer.input] ?? 0) + 1;
  if (count >= LAST_MODEL_FAILURE_LIMIT) { clearLastModel(pointer); return true; }
  stored.failed[pointer.input] = count;
  writeStored(stored);
  return false;
}

/** Forget one input's pointer, unless a newer one has replaced the one that went stale. */
export function clearLastModel(stale: LastModel): void {
  const stored = parseStored(storage.getItem(KEY));
  if (stored.pointers[stale.input] !== stale.model_id) return;
  delete stored.pointers[stale.input];
  delete stored.failed[stale.input];
  writeStored(stored);
}

/**
 * Move an older pointer in once, then drop the old keys.
 *
 * A pointer already stored for an input wins; each old key fills only the input
 * it named, so both survive. A bare prepared id is resolved to its model; only
 * a server that has none for it drops it, a transport failure keeps it for the
 * next start.
 */
export async function migrateLastModel(fetcher: typeof fetch = fetch): Promise<void> {
  const meshRaw = legacyMesh.getItem(KEY);
  const cadRaw = legacyCad.getItem(KEY);
  if (!meshRaw && !cadRaw) return;
  const found: Pointers = {};
  if (meshRaw) {
    let saved: unknown = meshRaw;
    try { saved = JSON.parse(meshRaw); } catch { /* a bare prepared id */ }
    if (saved && typeof saved === 'object') {
      const id = (saved as { model_id?: unknown }).model_id;
      if (typeof id === 'string' && id.trim()) found.file = id.trim();
    } else if (typeof saved === 'string' && saved) {
      const resolved = await findPreparedModel(saved, fetcher).catch(() => undefined);
      if (resolved === undefined) return;
      if (resolved?.model_id.startsWith('mesh-file:')) found.file = resolved.model_id;
    }
  }
  if (cadRaw?.trim()) found.cad = cadRaw.trim();
  // Another start (or a model opened meanwhile) may have written pointers while this waited.
  const meanwhile = parse(storage.getItem(KEY));
  const merged: Pointers = { ...found, ...meanwhile };
  if (merged.file !== meanwhile.file || merged.cad !== meanwhile.cad) write(merged);
  dropLegacy();
}
