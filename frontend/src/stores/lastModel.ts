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

function parse(raw: string | null): LastModel | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<LastModel> | null;
    const id = typeof value?.model_id === 'string' ? value.model_id.trim() : '';
    return id && (value?.input === 'file' || value?.input === 'cad') ? { model_id: id, input: value.input } : null;
  } catch { return null; }
}

/** The pointer as stored; the legacy pointers are not consulted (see `migrateLastModel`). */
export function readLastModel(): LastModel | null {
  return parse(storage.getItem(KEY));
}

function dropLegacy(): void {
  if (legacyMesh.getItem(KEY) !== null) legacyMesh.removeItem(KEY);
  if (legacyCad.getItem(KEY) !== null) legacyCad.removeItem(KEY);
}

/** Point at the model on screen. Writing it also settles the legacy pointers: this one is newer. */
export function rememberLastModel(model: LastModel): void {
  const current = readLastModel();
  if (!current || current.model_id !== model.model_id || current.input !== model.input) {
    storage.setItem(KEY, JSON.stringify({ model_id: model.model_id, input: model.input }));
  }
  dropLegacy();
}

/** Forget the pointer, unless a newer one has replaced the one that went stale. */
export function clearLastModel(stale?: LastModel): void {
  const current = readLastModel();
  if (stale && current && (current.model_id !== stale.model_id || current.input !== stale.input)) return;
  storage.removeItem(KEY);
}

/**
 * The pointer, after moving an older one into it once.
 *
 * A pointer already here wins and the old keys are dropped. Of two old ones the
 * input already on screen wins. A bare prepared id is resolved to its model;
 * only a server that has none for it drops it, a transport failure keeps it for
 * the next start.
 */
export async function migrateLastModel(preferred: LastModelInput, fetcher: typeof fetch = fetch): Promise<LastModel | null> {
  const current = readLastModel();
  const meshRaw = legacyMesh.getItem(KEY);
  const cadRaw = legacyCad.getItem(KEY);
  if (!meshRaw && !cadRaw) return current;
  if (current) { dropLegacy(); return current; }
  const candidates: LastModel[] = [];
  if (meshRaw) {
    let saved: unknown = meshRaw;
    try { saved = JSON.parse(meshRaw); } catch { /* a bare prepared id */ }
    if (saved && typeof saved === 'object') {
      const id = (saved as { model_id?: unknown }).model_id;
      if (typeof id === 'string' && id.trim()) candidates.push({ model_id: id.trim(), input: 'file' });
    } else if (typeof saved === 'string' && saved) {
      const found = await findPreparedModel(saved, fetcher).catch(() => undefined);
      if (found === undefined) return null;
      if (found?.model_id.startsWith('mesh-file:')) candidates.push({ model_id: found.model_id, input: 'file' });
    }
  }
  if (cadRaw?.trim()) candidates.push({ model_id: cadRaw.trim(), input: 'cad' });
  // Another start (or a model opened meanwhile) may have written the pointer while this waited.
  const meanwhile = readLastModel();
  if (meanwhile) { dropLegacy(); return meanwhile; }
  const chosen = candidates.find((item) => item.input === preferred) ?? candidates[0] ?? null;
  if (chosen) storage.setItem(KEY, JSON.stringify(chosen));
  dropLegacy();
  return chosen;
}
