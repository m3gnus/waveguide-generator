/** Shared with server/settings/cad_drafts.py: bounded per-entry last-write wins. */
export const CAD_DRAFTS_NAMESPACE = 'cadSetupDrafts_v2';
const MAX_ENTRIES = 20;
const MAX_MARKERS = 64;
const MAX_NAMESPACE_BYTES = 512 * 1024;
export interface CadDraftEntry {
  draft: Record<string, unknown> | null;
  baseRevisionId: string | null;
  updatedAt: number;
}
export type CadDraftJournal = Record<string, CadDraftEntry>;
export function cadDraftHash(value: string): string {
  let hash = 0xcbf29ce484222325n;
  for (const byte of new TextEncoder().encode(value)) hash = BigInt.asUintN(64, (hash ^ BigInt(byte)) * 0x100000001b3n);
  return hash.toString(16).padStart(16, '0');
}
export function journalKey(model: string, inventory: string): string {
  return `${cadDraftHash(model)}_${cadDraftHash(inventory)}`;
}
export function parseCadDrafts(raw: unknown): CadDraftJournal {
  try {
    const parsed: unknown = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter(([key, entry]) =>
      /^[a-f0-9]{16}_[a-f0-9]{16}$/.test(key) && entry && typeof entry === 'object'
      && (entry.draft === null || (typeof entry.draft === 'object' && !Array.isArray(entry.draft)))
      && (entry.baseRevisionId === null || typeof entry.baseRevisionId === 'string')
      && Number.isFinite(entry.updatedAt) && entry.updatedAt >= 0
      && new TextEncoder().encode(JSON.stringify(entry)).length <= 64 * 1024,
    ));
  } catch { return {}; }
}
export function mergeCadDrafts(...journals: unknown[]): CadDraftJournal {
  const merged: CadDraftJournal = {};
  for (const raw of journals) for (const [key, entry] of Object.entries(parseCadDrafts(raw))) {
    if (!merged[key] || entry.updatedAt >= merged[key].updatedAt) merged[key] = entry;
  }
  const newest = Object.entries(merged)
    .sort(([ka, a], [kb, b]) => b.updatedAt - a.updatedAt || (ka < kb ? -1 : ka > kb ? 1 : 0));
  const live = newest.filter(([, entry]) => entry.draft !== null).slice(0, MAX_ENTRIES);
  const markers = newest.filter(([, entry]) => entry.draft === null).slice(0, MAX_MARKERS);
  const retained = new Set([...live, ...markers].map(([key]) => key));
  for (;;) {
    const result = Object.fromEntries(newest.filter(([key]) => retained.has(key)));
    // Match Python's ASCII JSON encoding of the namespace's JSON string.
    const stored = JSON.stringify(JSON.stringify(result)).replace(/[\u007f-\uffff]/g,
      char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
    if (new TextEncoder().encode(stored).length <= MAX_NAMESPACE_BYTES) return result;
    retained.delete((live.length ? live : markers).pop()![0]);
  }
}
/** Only migration understands the original unbounded keys and flat draft values. */
export function migrateCadDrafts(raw: string): CadDraftJournal {
  try {
    const migrated: CadDraftJournal = {};
    for (const [key, value] of Object.entries(JSON.parse(raw))) {
      try {
        const [model, inventory] = JSON.parse(key);
        if (typeof model !== 'string' || typeof inventory !== 'string' || !value || typeof value !== 'object') continue;
        const { baseRevisionId = null, updatedAt = 0, ...draft } = value as Record<string, unknown>;
        migrated[journalKey(model, inventory)] = {
          draft: { ...draft, ...(Object.hasOwn(value, 'baseRevisionId') ? {} : { legacy: true }) },
          baseRevisionId: baseRevisionId as string | null, updatedAt: updatedAt as number,
        };
      } catch { /* one malformed entry must not hide the remaining drafts */ }
    }
    return mergeCadDrafts(migrated);
  } catch { return {}; }
}
