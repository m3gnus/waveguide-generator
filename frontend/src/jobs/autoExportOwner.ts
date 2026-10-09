import type { JobItem } from '../api/jobsSocket';
import type { ExportFormat } from '../prefs/preferences';
import type { ExportBundleResult } from '../results/exporters';

async function post(path: string, body?: unknown): Promise<Record<string, unknown>> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal });
    if (!response.ok) {
      const detail = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(detail?.detail ?? `Automatic export request failed (${response.status})`);
    }
    return await response.json() as Record<string, unknown>;
  } finally { clearTimeout(timeout); }
}

export async function retryBlockedExport(jobId: string): Promise<void> {
  window.dispatchEvent(new CustomEvent('wg-retry-auto-export', { detail: jobId }));
  await post(`/api/jobs/${encodeURIComponent(jobId)}/auto-export/retry`);
}

/** The server owns both publication and completion; stale window rows never
 * overwrite its fresh metadata. No generation happens before the claim. */
export async function ownedAutoExport(
  job: JobItem,
  formats: ExportFormat[],
  generate: (job: JobItem, formats: ExportFormat[], fetcher: typeof fetch) => Promise<ExportBundleResult>,
  selectedFormats: readonly string[] = formats,
): Promise<null> {
  let claim: Record<string, unknown>;
  do {
    claim = await post(`/api/jobs/${encodeURIComponent(job.id)}/auto-export/claim`, { formats: selectedFormats });
    if (claim.busy) await new Promise((resolve) => setTimeout(resolve, 2000));
  } while (claim.busy);
  if (!claim.claimed) return null;
  const token = claim.token as string;
  const pending = claim.formats as ExportFormat[];
  const freshJob = claim.job as JobItem;
  const base = `/api/jobs/auto-export/${encodeURIComponent(token)}`;
  const abort = new AbortController();
  let lost: Error | null = null;
  let heartbeatRunning = false;
  const heartbeat = setInterval(() => {
    if (heartbeatRunning) return;
    heartbeatRunning = true;
    void post(`${base}/heartbeat`).catch((error: unknown) => {
      lost = error instanceof Error ? error : new Error(String(error));
      abort.abort();
    }).finally(() => { heartbeatRunning = false; });
  }, 10_000);
  const fencedFetch: typeof fetch = (input, init) => {
    if (lost) return Promise.reject(lost);
    const headers = new Headers(init?.headers);
    headers.set('X-WG-Auto-Export', token);
    return fetch(input, { ...init, headers, signal: abort.signal });
  };
  try {
    let result: ExportBundleResult;
    try {
      result = await generate(freshJob, pending, fencedFetch);
    } catch (error) {
      if (lost) throw lost;
      const conflict = (error as { destinationConflict?: boolean }).destinationConflict === true
        || (error as { publicationUncertain?: boolean }).publicationUncertain === true;
      result = { files: [], failures: pending.map((format) => ({ format, reason: error instanceof Error ? error.message : String(error), blocked: conflict })) };
    }
    if (lost) throw lost;
    const attemptedAt = new Date().toISOString();
    const statuses: JobItem['auto_export_formats'] = {};
    for (const format of pending) {
      const failure = result.failures.find((item) => item.format === format);
      statuses[format] = failure
        ? { status: failure.blocked ? 'blocked' : 'failed', attempted_at: attemptedAt, reason: failure.reason }
        : { status: 'complete', attempted_at: attemptedAt };
    }
    const all = { ...freshJob.auto_export_formats, ...statuses };
    await post(`${base}/finish`, { files: result.files, formats: statuses,
      completed_at: selectedFormats.every((format) => all[format]?.status === 'complete') ? attemptedAt : null });
    if (result.failures.length) throw new Error(`Automatic export ${job.id.slice(0, 6)}: ${result.failures.map(({ format, reason }) => `${format} (${reason})`).join(', ')}`);
    return null;
  } finally {
    clearInterval(heartbeat);
    abort.abort();
    // Already-finished and expired tokens are harmless refusals. An interrupted
    // reservation stays blocked durably, and in-flight server work retains its fence.
    void post(`${base}/release`).catch(() => undefined);
  }
}
