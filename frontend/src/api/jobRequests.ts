import { formatApiDetail, type ImportedSolveSubmission } from '../jobs/actions';

/** Historical file and CAD runs restore the admitted request, before execution transforms. */
export async function readImportedJobRequest(jobId: string, fetcher: typeof fetch = fetch): Promise<ImportedSolveSubmission> {
  const response = await fetcher(`/api/jobs/${encodeURIComponent(jobId)}/requests`, undefined);
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(formatApiDetail(error?.detail) ?? `Run inputs unavailable (${response.status}).`);
  }
  const { effective_request } = await response.json();
  return effective_request;
}
