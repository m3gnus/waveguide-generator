import { formatApiDetail } from '../jobs/actions';
import type { ImportedSetup } from '../jobs/importedSetup';

/** A model's setup, for the sources of one of its prepared meshes (the server reads them). */
async function request(modelId: string, preparedId: string, setup?: ImportedSetup, fetcher: typeof fetch = fetch) {
  const path = `/api/models/${encodeURIComponent(modelId)}/setup`;
  const response = await fetcher(setup ? path : `${path}?prepared_id=${encodeURIComponent(preparedId)}`, setup ? {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prepared_id: preparedId, setup: { schema_version: 1, ...setup } }),
  } : undefined);
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(formatApiDetail(error?.detail) ?? `Setup unavailable (${response.status}).`);
  }
  return response.json();
}

export const saveImportedSetup = (modelId: string, preparedId: string, setup: ImportedSetup, fetcher?: typeof fetch):
  Promise<{ revision_id: string; content_sha256: string }> => request(modelId, preparedId, setup, fetcher);
export const readImportedSetup = (modelId: string, preparedId: string, fetcher?: typeof fetch):
  Promise<{ setup: ImportedSetup | null; revision_id?: string | null }> => request(modelId, preparedId, undefined, fetcher);
