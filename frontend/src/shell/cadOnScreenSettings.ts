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

/** Why a request can wait that a WG Solve does not continue: the backend
 * queues it again by itself once the update restart is over. */
const NOT_CONTINUED: ReadonlySet<string> = new Set(['update_restart_pending']);

/** The request for the model on screen that WG's Solve continues: one Fusion
 * sent ("Solve in WG") for this very snapshot, waiting for the user at any of
 * its gates -- its first settings, its solver frame, an engine that cannot
 * solve it, a failed or interrupted preparation.
 *
 * Solve continues that operation, with the settings and frame on screen,
 * instead of creating a second one for the same snapshot: the same operation
 * id is the explicit continuation (PLAN.md M1b, "one card per intent"). A
 * request for another snapshot is not this, and neither is one the backend is
 * still preparing or will queue again by itself. */
export function onScreenRequestToContinue(
  operations: Record<string, CadOperationSummary>,
  record: CadReturnIngestRecord | null,
): CadOperationSummary | null {
  if (!record) return null;
  return pendingCadOperations(operations).find((operation) => operation.kind === 'prepare_and_solve'
    && operation.state === 'needs_user_input'
    && !NOT_CONTINUED.has(operation.reason ?? '')
    && operation.snapshot?.manifestSha256 === record.manifest_sha256) ?? null;
}
