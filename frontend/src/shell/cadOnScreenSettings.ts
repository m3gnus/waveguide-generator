import { type CadOperationSummary } from '../api/cadOperations';
import { bundleIdentity, useCadReturnStore } from '../stores/cadReturn';

/** The project the settings on screen may be recorded under for this
 * operation, or why they may not.
 *
 * Checked when the settings are used, not when a card rendered: the
 * selection, the ingestion or the listing may all have moved since. The
 * settings are the on-screen model's, so that model has to be this
 * operation's snapshot, prepared from this very listing of its return, and
 * filed under the project the backend names. With no project named, only the
 * ingestion's own counts; the parametric document's lineage never does. */
export function settingsProjectFor(
  operation: CadOperationSummary | undefined,
  state: ReturnType<typeof useCadReturnStore.getState>,
): string {
  const snapshot = operation?.snapshot;
  const record = state.ingestRecord;
  if (!snapshot?.manifestSha256 || !record || record.manifest_sha256 !== snapshot.manifestSha256) {
    throw new Error('This request is for a model that is not the model on screen. Select and prepare its return first.');
  }
  if (state.needsIngest || !state.selectedBundle
    || bundleIdentity(state.selectedBundle) !== state.ingestedBundleIdentity) {
    throw new Error('The model on screen has changed since it was prepared. Prepare it again, then use its settings.');
  }
  const filed = record.project?.lineage_id ?? null;
  if (snapshot.projectLineageId) {
    if (snapshot.projectLineageId !== (filed ?? state.projectLineageId)) {
      throw new Error('The model on screen is filed under another project than this request names. Open that project first.');
    }
    return snapshot.projectLineageId;
  }
  if (!filed) {
    throw new Error('WG does not know which project this model belongs to yet, so its settings cannot be recorded for it.');
  }
  return filed;
}

/** What the Solve card says while the backend prepares a request for the model on screen. */
export function inFlightWords(operation: CadOperationSummary): string {
  return operation.operationId.startsWith('manual-solve:')
    ? 'Preparing your solve…'
    : 'Preparing the request from Fusion…';
}
