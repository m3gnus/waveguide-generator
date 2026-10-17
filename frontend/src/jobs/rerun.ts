import { preferencesStore } from '../prefs/preferences';
import { useDocumentStore } from '../stores/document';
import { advanceRunSequence, nextRunLabel } from './runNaming';

/**
 * A change to apply to a finished run's own request before submitting it again.
 * Only the named keys change; nested objects (`polar_config`) merge one level.
 */
export type RerunPatch = { options?: Record<string, unknown> };

export interface RerunDeps {
  fetcher?: typeof fetch;
  now?: () => Date;
  newId?: () => string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function failure(response: Response): Promise<Error> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string' && body.detail) return new Error(body.detail);
  } catch { /* fall through */ }
  return new Error(`${response.status} ${response.statusText}`.trim());
}

/**
 * Submit a finished run again with one change.
 *
 * The request is the run's `effective_request` (what the user asked for, before
 * execution-time adjustments), so engine, accuracy, frequencies and distance
 * stay the run's own. Nothing is read from the editor's solve options and
 * nothing is written to the persisted preferences store.
 */
export async function rerunWithPatch(
  jobId: string,
  patch: RerunPatch,
  { fetcher = fetch, now = () => new Date(), newId = () => crypto.randomUUID() }: RerunDeps = {},
): Promise<string> {
  const stored = await fetcher(`/api/jobs/${encodeURIComponent(jobId)}/requests`, { cache: 'no-store' });
  if (!stored.ok) throw await failure(stored);
  const effective = (await stored.json() as { effective_request?: unknown }).effective_request;
  if (!isRecord(effective) || !isRecord(effective.options)) {
    throw new Error('This result has no readable request, so it cannot be rerun.');
  }
  const options: Record<string, unknown> = { ...effective.options };
  for (const [key, value] of Object.entries(patch.options ?? {})) {
    const current = options[key];
    options[key] = isRecord(current) && isRecord(value) ? { ...current, ...value } : value;
  }
  const designName = useDocumentStore.getState().designName;
  const label = nextRunLabel(designName, preferencesStore.getSnapshot(), now());
  const response = await fetcher('/api/solve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...effective, options, label, client_request_id: newId() }),
  });
  if (!response.ok) throw await failure(response);
  const submitted = ((await response.json()) as { job_id: string }).job_id;
  preferencesStore.update(advanceRunSequence(preferencesStore.getSnapshot(), designName));
  return submitted;
}
