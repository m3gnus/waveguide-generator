import { formatApiDetail } from '../jobs/actions';
import type { ImportedSetup } from '../jobs/importedSetup';

async function request(id: string, setup?: ImportedSetup, fetcher: typeof fetch = fetch) {
  const response = await fetcher(`/api/imported-models/${encodeURIComponent(id)}/setup`, setup ? {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ schema_version: 1, ...setup }),
  } : undefined);
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(formatApiDetail(error?.detail) ?? `Setup unavailable (${response.status}).`);
  }
  return response.json();
}

export const saveImportedSetup = (id: string, setup: ImportedSetup, fetcher?: typeof fetch) => request(id, setup, fetcher);
export const readImportedSetup = (id: string, fetcher?: typeof fetch): Promise<{ setup: ImportedSetup | null }> => request(id, undefined, fetcher);
