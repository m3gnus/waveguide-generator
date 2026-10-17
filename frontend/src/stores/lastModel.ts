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

/** One pointer per input, so reopening never flips the input a person last chose.
 * The single `{model_id, input}` form an earlier build wrote is read as one entry. */
function parse(raw: string | null): Pointers {
  if (!raw) return {};
  try {
    const value = JSON.parse(raw) as { model_id?: unknown; input?: unknown; file?: unknown; cad?: unknown } | null;
    const out: Pointers = {};
    const take = (input: LastModelInput, id: unknown) => { if (typeof id === 'string' && id.trim()) out[input] = id.trim(); };
    take('file', value?.file); take('cad', value?.cad);
    if (value?.input === 'file' || value?.input === 'cad') take(value.input, value.model_id);
    return out;
  } catch { return {}; }
}

function write(pointers: Pointers): void {
  if (!pointers.file && !pointers.cad) storage.removeItem(KEY);
  else storage.setItem(KEY, JSON.stringify(pointers));
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

/** Point at the model on screen. Writing it also settles the legacy pointers: this one is newer. */
export function rememberLastModel(model: LastModel): void {
  const pointers = parse(storage.getItem(KEY));
  if (pointers[model.input] !== model.model_id) write({ ...pointers, [model.input]: model.model_id });
  dropLegacy();
}

/** Forget one input's pointer, unless a newer one has replaced the one that went stale. */
export function clearLastModel(stale: LastModel): void {
  const pointers = parse(storage.getItem(KEY));
  if (pointers[stale.input] !== stale.model_id) return;
  delete pointers[stale.input];
  write(pointers);
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
