import { useEffect, useState, useSyncExternalStore } from 'react';
import { getIngest, type CadReturnFinding, type CadReturnIngestRecord } from '../api/cadlink';
import { getCadOperation, type CadOperationSummary } from '../api/cadOperations';
import { pendingCadOperations, useCadOperationsStore } from '../stores/cadOperations';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

/** The hash of a `sha256:` digest, cut to what a person can read and compare. */
export function shortSha256(digest: string | null | undefined): string {
  return (digest ?? '').replace(/^sha256:/, '').slice(0, 12);
}

const STATE_COPY: Record<string, string> = {
  received: 'Received',
  processing: 'Preparing',
  needs_user_input: 'Waiting for you',
  recovery_required: 'Needs recovery',
  cancel_requested: 'Dismissing',
};

const REASON_COPY: Record<string, string> = {
  setup_required: 'needs its solve settings',
  findings_need_review: 'blocking findings to review',
  preparation_failed: 'preparation failed',
  engine_unavailable: 'the selected engine cannot solve it',
  submission_refused: 'the jobs system refused it',
  interrupted: 'interrupted',
  ready_to_solve: 'ready to solve',
};

interface FindingReview {
  preparationId: string | null;
  findingIds: string[];
  findings: CadReturnFinding[];
}

const NO_REVIEW: FindingReview = { preparationId: null, findingIds: [], findings: [] };

/** The blocking findings one preparation reported, as the backend records them. */
function useFindingReview(operation: CadOperationSummary): FindingReview {
  const wanted = operation.reason === 'findings_need_review';
  const [review, setReview] = useState<FindingReview>(NO_REVIEW);
  useEffect(() => {
    if (!wanted) return undefined;
    let current = true;
    void (async () => {
      const preparation = (await getCadOperation(operation.operationId)).preparation;
      if (!preparation || !current) return;
      // The ids are the review; the ingestion record only puts words to them.
      const findings = await getIngest(preparation.ingestId)
        .then((record) => record.findings.filter((finding) => preparation.blockingFindingIds.includes(finding.id)))
        .catch(() => [] as CadReturnFinding[]);
      if (current) {
        setReview({ preparationId: preparation.preparationId, findingIds: preparation.blockingFindingIds, findings });
      }
    })().catch(() => undefined);
    return () => { current = false; };
  }, [wanted, operation.operationId, operation.preparationId, operation.attemptGeneration]);
  return wanted ? review : NO_REVIEW;
}

/** What the user can do about the reason an operation waits. */
function guidance(operation: CadOperationSummary, onScreen: boolean): { text: string | null; simulation: boolean } {
  switch (operation.reason) {
    case 'setup_required':
      return onScreen
        ? { text: 'Choose this model’s solve settings in Simulation, then press Solve now.', simulation: true }
        : {
          text: 'No solve settings are recorded for this model’s project and sources yet. Open its project from File → CAD-linked designs, choose its settings, then press Solve now.',
          simulation: false,
        };
    case 'engine_unavailable':
      return {
        text: 'Pick one of the engines it names in the solver selector, in Simulation, then press Solve now. WG never switches engines for you.',
        simulation: true,
      };
    case 'submission_refused':
      return {
        text: 'If it names engines that can solve this model, pick one in the solver selector, in Simulation; then press Solve now. WG never switches engines for you.',
        simulation: true,
      };
    case 'findings_need_review':
      return { text: 'Approving applies to this preparation only; a new preparation needs its own review.', simulation: false };
    default:
      return { text: null, simulation: false };
  }
}

function CadOperationCard({ operation, record }: {
  operation: CadOperationSummary;
  record: CadReturnIngestRecord | null;
}) {
  const coordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe, cadLinkCoordinatorBridge.getSnapshot, cadLinkCoordinatorBridge.getSnapshot,
  );
  const [busy, setBusy] = useState(false);
  const review = useFindingReview(operation);
  const manifest = operation.snapshot?.manifestSha256 ?? null;
  const onScreen = Boolean(record && manifest && record.manifest_sha256 === manifest);
  const help = guidance(operation, onScreen);
  const solve = operation.kind === 'prepare_and_solve';
  const waiting = operation.state === 'needs_user_input' || operation.state === 'received';
  const reviewing = operation.reason === 'findings_need_review';
  // Only the preparation the operation names: an approval never carries to another.
  const reviewedPreparation = reviewing && review.findingIds.length > 0
    && review.preparationId === operation.preparationId ? review.preparationId : null;
  const run = (action: () => Promise<void>) => {
    setBusy(true);
    void action().catch(() => undefined).finally(() => setBusy(false));
  };
  const status = [
    STATE_COPY[operation.state] ?? operation.state,
    operation.stage,
    operation.reason ? REASON_COPY[operation.reason] ?? operation.reason : null,
  ].filter(Boolean).join(' · ');
  return <div className="cad-direction-alert cad-operation" role="status" data-operation-id={operation.operationId}>
    <div>
      <b>{solve ? 'Fusion asked for a solve' : `CAD operation · ${operation.kind}`}</b>
      <span>{status}</span>
      {operation.message && <span>{operation.message}</span>}
      {reviewedPreparation && <ul className="cad-operation-findings">
        {review.findingIds.map((id) => {
          const finding = review.findings.find((item) => item.id === id);
          return <li key={id}>
            <code>{id}</code>{finding ? ` · ${finding.kind}${finding.detail ? ` — ${finding.detail}` : ''}` : ''}
          </li>;
        })}
      </ul>}
      {help.text && <span>{help.text}</span>}
      <span className="cad-operation-ids">
        Operation <code>{operation.operationId}</code>
        {' · '}Setup revision <code>{operation.setupRevisionId ?? 'none yet'}</code>
        {' · '}Preparation <code>{operation.preparationId ?? 'none yet'}</code>
        {manifest && <>{' · '}Snapshot <code>{shortSha256(manifest)}</code></>}
      </span>
    </div>
    <div className="cad-confirm-actions">
      {operation.state !== 'cancel_requested' && <button
        disabled={busy}
        title="Dismiss this request. Fusion will not offer it again."
        onClick={() => run(() => coordinator.dismissOperation(operation.operationId))}
      >Dismiss</button>}
      {help.simulation && <button onClick={() => workspaceNavigation.activate('simulation')}>Open Simulation</button>}
      {solve && waiting && reviewedPreparation && <button
        className="primary"
        disabled={busy}
        onClick={() => run(() => coordinator.approveOperation(operation.operationId, {
          preparationId: reviewedPreparation, findingIds: review.findingIds,
        }))}
      >Approve and solve</button>}
      {solve && waiting && !reviewing && <button
        className="primary"
        disabled={busy}
        title="Prepare this model from its project’s own solve settings and start the solve."
        onClick={() => run(() => coordinator.solveOperation(operation.operationId))}
      >Solve now</button>}
    </div>
  </div>;
}

/** The CAD operations still waiting or running: the solves Fusion sent, which
 * the backend prepares from each project's own setup. */
export function CadOperationsSection({ record }: { record: CadReturnIngestRecord | null }) {
  const operations = useCadOperationsStore((state) => state.operations);
  const pending = pendingCadOperations(operations);
  if (!pending.length) return null;
  return <div className="cad-operations">
    {pending.map((operation) => <CadOperationCard key={operation.operationId} operation={operation} record={record}/>)}
  </div>;
}
