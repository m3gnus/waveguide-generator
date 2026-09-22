import type { CadReturnIngestRecord } from '../api/cadlink';
import { putProjectSetup, type CadOperationSummary } from '../api/cadOperations';
import { importedSubmissionBlocker } from '../jobs/importedSubmission';
import { bundleIdentity, useCadReturnStore } from '../stores/cadReturn';
import { pendingCadOperations, useCadOperationsStore } from '../stores/cadOperations';
import { buildCadProjectSetup } from './cadSetupPublisher';

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

/** Record the settings on screen as the setup of this operation's project and
 * return that setup revision: what an action that chooses settings sends. */
export async function recordOnScreenSettings(operationId: string): Promise<string> {
  const state = useCadReturnStore.getState();
  const lineageId = settingsProjectFor(useCadOperationsStore.getState().operations[operationId], state);
  const built = buildCadProjectSetup(state, undefined, undefined, lineageId);
  if (!built) throw new Error(importedSubmissionBlocker() ?? 'The solve settings on screen are not complete yet.');
  return (await putProjectSetup(built)).revisionId;
}

/** The request for the model on screen that still waits for its first
 * settings (`setup_required`), when the settings on screen may be used for it.
 *
 * WG's own Solve continues that operation instead of creating a second one
 * for the same snapshot: the same operation id is the explicit continuation.
 * A request for another snapshot, or one waiting at any other gate, is not
 * this; neither is one whose settings could not be filed under its project. */
export function waitingForFirstSettings(
  operations: Record<string, CadOperationSummary>,
  record: CadReturnIngestRecord | null,
  state: ReturnType<typeof useCadReturnStore.getState> = useCadReturnStore.getState(),
): CadOperationSummary | null {
  if (!record) return null;
  const waiting = pendingCadOperations(operations).find((operation) => operation.kind === 'prepare_and_solve'
    && operation.state === 'needs_user_input'
    && operation.reason === 'setup_required'
    && operation.snapshot?.manifestSha256 === record.manifest_sha256);
  if (!waiting) return null;
  try {
    settingsProjectFor(waiting, state);
  } catch {
    return null;
  }
  return waiting;
}
