import type { components } from './generated/openapi';
import { failureWords, refusalText } from './httpError';
import { stagedActivity } from '../shell/stagedActivity';

export const RETURNED_STEP_UNAVAILABLE = "This run's returned STEP is no longer available. Send the model again from Fusion and solve it before exporting STEP.";
export type ReturnedStepAvailability = components['schemas']['ReturnedStepAvailability'];

export async function checkReturnedStep(jobId: string, signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<ReturnedStepAvailability> {
  return stagedActivity({ kind: 'export', label: 'Checking returned STEP' }, fetcher, signal, async (fetcher, signal) => {
    try {
      const response = await fetcher(`/api/export/runs/${encodeURIComponent(jobId)}/step/availability`, { method: 'POST', signal });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: unknown } | null;
        throw new Error(refusalText(body?.detail, 'returned STEP', 'WG could not check this run’s STEP. Try the export again.'));
      }
      return await response.json() as ReturnedStepAvailability;
    } catch (error) {
      if (signal.aborted) throw error;
      return { available: false, reason: failureWords(error) };
    }
  });
}
