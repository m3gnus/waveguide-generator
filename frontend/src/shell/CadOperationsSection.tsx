import { useEffect, useState, useSyncExternalStore } from 'react';
import { getIngest, type CadReturnFinding, type CadReturnIngestRecord } from '../api/cadlink';
import { getCadOperation, type CadOperationSummary } from '../api/cadOperations';
import { listCadProjects } from '../api/cadProjects';
import { pendingCadOperations, useCadOperationsStore } from '../stores/cadOperations';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import { openCadProject } from './CadProjectPanel';
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
  error: string | null;
}

const NO_REVIEW: FindingReview = { preparationId: null, findingIds: [], findings: [], error: null };

/** The blocking findings one preparation reported, as the backend records them. */
function useFindingReview(operation: CadOperationSummary): FindingReview & { retry: () => void } {
  const wanted = operation.reason === 'findings_need_review';
  const [review, setReview] = useState<FindingReview>(NO_REVIEW);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!wanted) return undefined;
    let current = true;
    void (async () => {
      const preparation = (await getCadOperation(operation.operationId)).preparation;
      if (!current) return;
      if (!preparation) throw new Error('the backend has not recorded its preparation yet');
      // The ids are the review; the ingestion record only puts words to them.
      const findings = await getIngest(preparation.ingestId)
        .then((record) => record.findings.filter((finding) => preparation.blockingFindingIds.includes(finding.id)))
        .catch(() => [] as CadReturnFinding[]);
      if (current) {
        setReview({
          preparationId: preparation.preparationId, findingIds: preparation.blockingFindingIds, findings, error: null,
        });
      }
    })().catch((reason: unknown) => {
      if (current) setReview({ ...NO_REVIEW, error: reason instanceof Error ? reason.message : String(reason) });
    });
    return () => { current = false; };
  }, [wanted, operation.operationId, operation.preparationId, operation.attemptGeneration, attempt]);
  return { ...(wanted ? review : NO_REVIEW), retry: () => setAttempt((count) => count + 1) };
}

interface Guidance {
  text: string | null;
  simulation: boolean;
  /** The action the reason calls for, offered while the operation waits. */
  action: 'solve' | 'approve' | 'use-settings' | 'open-project' | null;
}

/** What the user can do about the reason an operation waits. */
function guidance(operation: CadOperationSummary, onScreen: boolean): Guidance {
  const name = operation.snapshot?.documentName ?? 'this model';
  switch (operation.reason) {
    case 'setup_required':
      if (onScreen) {
        return {
          text: 'This model is on screen: check its solve settings in Simulation, then use them to solve it.',
          simulation: true,
          action: 'use-settings',
        };
      }
      if (operation.snapshot?.projectLineageId) {
        return {
          text: `No solve settings are recorded for ${name}’s project and sources yet. Open it to choose them; WG asks before replacing a design that exists nowhere else.`,
          simulation: false,
          action: 'open-project',
        };
      }
      return {
        text: `Select ${name} in the return list first. WG files it under a project when it prepares it; then choose its solve settings and use them to solve it.`,
        simulation: false,
        action: null,
      };
    case 'engine_unavailable':
      return {
        text: 'Pick one of the engines it names in the solver selector, in Simulation, then press Solve now. WG never switches engines for you.',
        simulation: true,
        action: 'solve',
      };
    case 'submission_refused':
      return {
        text: 'If it names engines that can solve this model, pick one in the solver selector, in Simulation; then press Solve now. WG never switches engines for you.',
        simulation: true,
        action: 'solve',
      };
    case 'findings_need_review':
      return {
        text: 'Approving applies to this preparation only; a new preparation needs its own review.',
        simulation: false,
        action: 'approve',
      };
    default:
      return { text: null, simulation: false, action: 'solve' };
  }
}

function CadOperationCard({ operation, record }: {
  operation: CadOperationSummary;
  record: CadReturnIngestRecord | null;
}) {
  const coordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe, cadLinkCoordinatorBridge.getSnapshot, cadLinkCoordinatorBridge.getSnapshot,
  );
  // The operation as it stood when an action was asked for. The answer is the
  // row as it was before the backend claimed it, so the actions stay held until
  // the operation moves on -- a newer attempt, or another state -- or the
  // request fails. Re-enabling on the answer let a second press start a second
  // attempt.
  const [asked, setAsked] = useState<{ attemptGeneration: number; state: string } | null>(null);
  const held = asked !== null
    && asked.attemptGeneration === operation.attemptGeneration && asked.state === operation.state;
  const [opening, setOpening] = useState(false);
  const review = useFindingReview(operation);
  const manifest = operation.snapshot?.manifestSha256 ?? null;
  const documentName = operation.snapshot?.documentName ?? null;
  const onScreen = Boolean(record && manifest && record.manifest_sha256 === manifest);
  const help = guidance(operation, onScreen);
  const solve = operation.kind === 'prepare_and_solve';
  // A received operation is the backend's own loop to prepare.
  const waiting = solve && operation.state === 'needs_user_input';
  const label = documentName ?? `operation ${operation.operationId}`;
  // Only the preparation the operation names: an approval never carries to another.
  const reviewedPreparation = review.findingIds.length > 0
    && review.preparationId === operation.preparationId ? review.preparationId : null;
  const ask = (action: () => Promise<void>) => {
    setAsked({ attemptGeneration: operation.attemptGeneration, state: operation.state });
    void action().catch(() => setAsked(null));
  };
  // The project switcher's own open: it asks before discarding a design that
  // exists nowhere else, and never opens over anything opened since.
  const openProject = async () => {
    const lineageId = operation.snapshot?.projectLineageId;
    if (!lineageId) return;
    setOpening(true);
    try {
      const project = (await listCadProjects()).find((item) => item.lineageId === lineageId);
      if (!project) throw new Error(`This copy of WG does not hold the project ${label} belongs to.`);
      const opened = await openCadProject(project);
      if (opened !== null) coordinator.reportStatus(`Opened ${opened}. Choose its solve settings, then use them to solve ${label}.`);
    } catch (reason) {
      coordinator.reportError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setOpening(false);
    }
  };
  const status = [
    STATE_COPY[operation.state] ?? operation.state,
    operation.stage,
    operation.reason ? REASON_COPY[operation.reason] ?? operation.reason : null,
  ].filter(Boolean).join(' · ');
  return <div className="cad-direction-alert cad-operation" data-operation-id={operation.operationId}>
    <div>
      <b>{solve ? 'Fusion asked for a solve' : `CAD operation · ${operation.kind}`}{documentName ? ` · ${documentName}` : ''}</b>
      <span role="status">{status}</span>
      {operation.message && <span>{operation.message}</span>}
      {reviewedPreparation && <ul className="cad-operation-findings">
        {review.findingIds.map((id) => {
          const finding = review.findings.find((item) => item.id === id);
          return <li key={id}>
            <code>{id}</code>{finding ? ` · ${finding.kind}${finding.detail ? ` — ${finding.detail}` : ''}` : ''}
          </li>;
        })}
      </ul>}
      {review.error && <span>Could not read the findings to review: {review.error}</span>}
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
        disabled={held}
        aria-label={`Dismiss: ${label}`}
        title="Dismiss this request. Fusion will not offer it again."
        onClick={() => ask(() => coordinator.dismissOperation(operation.operationId))}
      >Dismiss</button>}
      {review.error && <button aria-label={`Retry reading the findings for ${label}`} onClick={review.retry}>Retry</button>}
      {help.simulation && <button
        aria-label={`Open Simulation for ${label}`}
        onClick={() => workspaceNavigation.activate('simulation')}
      >Open Simulation</button>}
      {waiting && help.action === 'approve' && reviewedPreparation && <button
        className="primary"
        disabled={held}
        aria-label={`Approve and solve: ${label}`}
        onClick={() => ask(() => coordinator.approveOperation(operation.operationId, {
          preparationId: reviewedPreparation, findingIds: review.findingIds,
        }))}
      >Approve and solve</button>}
      {waiting && help.action === 'use-settings' && <button
        className="primary"
        disabled={held}
        aria-label={`Use these settings and solve: ${label}`}
        title="Record the settings on screen as this model’s project setup, then prepare and solve it."
        onClick={() => ask(() => coordinator.solveOperationWithSettings(operation.operationId))}
      >Use these settings and solve</button>}
      {waiting && help.action === 'open-project' && <button
        className="primary"
        disabled={opening}
        aria-label={`Open ${documentName ?? 'the project'} to choose its solve settings`}
        onClick={() => void openProject()}
      >Open {documentName ?? 'its project'}</button>}
      {waiting && help.action === 'solve' && <button
        className="primary"
        disabled={held}
        aria-label={`Solve now: ${label}`}
        title="Prepare this model from its project’s own solve settings and start the solve."
        onClick={() => ask(() => coordinator.solveOperation(operation.operationId))}
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
