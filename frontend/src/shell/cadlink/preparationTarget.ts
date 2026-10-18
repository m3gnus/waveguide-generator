import type { CadReturnBundle } from '../../api/cadlink';
import { useCadConnectionStore } from '../../stores/cadConnection';

/** The one choice of which instance a CAD return is prepared as, for manual
 * arrivals and receipts alike. Ingest refuses any instance but the return's
 * solver anchor when the return declares one. An older return without an
 * anchor accepts the linked live instance when the return contains it, which
 * ingest needs when that design appears more than once; otherwise instance
 * inference is left to ingest, which also validates the target. */
export function resolvePreparationTarget(
  bundle: CadReturnBundle,
  liveInstanceId: string | null = useCadConnectionStore.getState().fusionStatus?.link?.instanceId ?? null,
): string | null {
  if (bundle.solverAnchorInstanceId) return bundle.solverAnchorInstanceId;
  if (liveInstanceId && (bundle.instances ?? []).some((item) => item.instanceId === liveInstanceId)) {
    return liveInstanceId;
  }
  return null;
}
