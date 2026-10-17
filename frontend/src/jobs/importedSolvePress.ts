import type { ImportedSolveSubmission } from './actions';

const keyFor = (input: 'file' | 'cad') => input === 'file' ? 'wg-mesh-solve-press' : 'wg-cad-retained-solve-press';

/** All direct imported submissions keep one press identity until durable acceptance. */
export function importedSolvePress(submission: ImportedSolveSubmission, label: string, input: 'file' | 'cad' = 'file'): { id: string; label: string } {
  const body = JSON.stringify(submission);
  const key = keyFor(input);
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) ?? 'null');
    if (saved?.body === body && typeof saved.id === 'string' && typeof saved.label === 'string') return saved;
  } catch { /* Corrupt cache cannot claim an earlier submission. */ }
  const press = { id: `${input === 'file' ? 'mesh' : 'cad-retained'}-${crypto.randomUUID()}`, label, body };
  sessionStorage.setItem(key, JSON.stringify(press));
  return press;
}

export function finishImportedSolvePress(id: string): void {
  const key = keyFor(id.startsWith('mesh-') ? 'file' : 'cad');
  try {
    if (JSON.parse(sessionStorage.getItem(key) ?? 'null')?.id === id) sessionStorage.removeItem(key);
  } catch { /* Best-effort cache cleanup after durable backend acceptance. */ }
}
