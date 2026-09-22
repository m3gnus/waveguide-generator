import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { getIngest, type CadReturnFinding, type CadReturnIngestRecord } from '../api/cadlink';
import { getCadOperation, type CadOperationSummary } from '../api/cadOperations';
import { listCadProjects } from '../api/cadProjects';
import { pendingCadOperations, useCadOperationsStore } from '../stores/cadOperations';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import { openCadProject } from './CadProjectPanel';
import { fullTime, relativeTime } from './cadTime';
import { workspaceNavigation } from './workspaceNavigation';
import { CadSolverFrameConfirm } from './CadSolverFrameConfirm';

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
  update_restart_pending: 'held for the update restart',
  frame_confirmation_required: 'needs its solver frame confirmed',
};

interface FindingReview {
  preparationId: string | null;
  findingIds: string[];
  /** Of `findingIds`, those already approved on this same preparation. */
  approvedIds: string[];
  findings: CadReturnFinding[];
  error: string | null;
}

const NO_REVIEW: FindingReview = { preparationId: null, findingIds: [], approvedIds: [], findings: [], error: null };

/** The gates a preparation stops at, in the backend's order
 * (server/cadlink/preparation.py): frame, then findings, then the submission. */
const LADDER_REASONS: ReadonlySet<string> = new Set(['frame_confirmation_required', 'findings_need_review']);

/** The blocking findings one preparation reported, as the backend records them.
 *
 * Read at the frame gate as well as at the findings gate: `reason` names one
 * gate, the current one, while the preparation behind it already knows its
 * blocking findings (published before the frame gate returns). Reading them
 * only at their own gate is what let a user fix the frame and then hit a second
 * wall with no warning it was coming. */
function useFindingReview(operation: CadOperationSummary): FindingReview & { retry: () => void } {
  const wanted = operation.kind === 'prepare_and_solve'
    && operation.state === 'needs_user_input'
    && LADDER_REASONS.has(operation.reason ?? '');
  const [review, setReview] = useState<FindingReview>(NO_REVIEW);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!wanted) return undefined;
    let current = true;
    void (async () => {
      const detail = await getCadOperation(operation.operationId);
      const preparation = detail.preparation;
      if (!current) return;
      if (!preparation) throw new Error('the backend has not recorded its preparation yet');
      // The ids are the review; the ingestion record only puts words to them.
      const findings = await getIngest(preparation.ingestId)
        .then((record) => record.findings.filter((finding) => preparation.blockingFindingIds.includes(finding.id)))
        .catch(() => [] as CadReturnFinding[]);
      if (current) {
        const approvedIds = approvedFindingIds(detail.approvals, preparation.preparationId)
          .filter((id) => preparation.blockingFindingIds.includes(id));
        setReview({
          preparationId: preparation.preparationId,
          findingIds: preparation.blockingFindingIds,
          approvedIds,
          findings,
          error: null,
        });
      }
    })().catch((reason: unknown) => {
      if (current) setReview({ ...NO_REVIEW, error: reason instanceof Error ? reason.message : String(reason) });
    });
    return () => { current = false; };
  }, [wanted, operation.operationId, operation.preparationId, operation.attemptGeneration, operation.reason, attempt]);
  return { ...(wanted ? review : NO_REVIEW), retry: () => setAttempt((count) => count + 1) };
}

/** The findings approved on one preparation; the stored shape, read defensively. */
function approvedFindingIds(approvals: unknown, preparationId: string): string[] {
  if (!Array.isArray(approvals)) return [];
  return approvals.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const { preparation_id: preparation, finding_id: finding } = item as Record<string, unknown>;
    return preparation === preparationId && typeof finding === 'string' ? [finding] : [];
  });
}

/** One step between a waiting solve and its job. */
export interface SolveGateStep {
  gate: 'frame' | 'findings' | 'solve' | 'other';
  current: boolean;
  text: string;
}

function findingCount(count: number): string {
  return `${count} finding${count === 1 ? '' : 's'}`;
}

/** A finding in words: what kind it is and what it says, never its id. */
function findingWords(finding: CadReturnFinding): string {
  return `${finding.kind.replaceAll('-', ' ')}${finding.detail ? ` — ${finding.detail}` : ''}`;
}

/**
 * Every step still between a waiting solve and its job, the current one first.
 *
 * `reason` is by design a single current gate, so a card that rendered only it
 * always under-reported: the remaining ladder comes from the preparation's
 * blocking findings and the submission step behind them. Only what remains is
 * listed -- a gate already passed, or one that never applied to this model,
 * is not.
 */
export function solveGateLadder(
  operation: Pick<CadOperationSummary, 'kind' | 'state' | 'reason'>,
  review: Pick<FindingReview, 'findingIds' | 'approvedIds'> | null,
): SolveGateStep[] {
  if (operation.kind !== 'prepare_and_solve' || operation.state !== 'needs_user_input') return [];
  const blocking = review?.findingIds ?? [];
  const approved = new Set(review?.approvedIds ?? []);
  const unapproved = blocking.filter((id) => !approved.has(id));
  switch (operation.reason) {
    case 'frame_confirmation_required':
      return [
        {
          gate: 'frame', current: true,
          text: 'Now: confirm the solver frame — the axis this model radiates along — below.',
        },
        // Confirming the frame approves nothing: a finding still unapproved
        // is the next stop, and only then is it named.
        ...(unapproved.length ? [{
          gate: 'findings' as const, current: false,
          text: `Then: approve ${findingCount(unapproved.length)}.`,
        }] : []),
      ];
    case 'findings_need_review':
      return [{
        gate: 'findings', current: true,
        text: `Now: review ${findingCount(unapproved.length || blocking.length)}, then Approve and solve.`,
      }];
    case 'ready_to_solve':
      return [{ gate: 'solve', current: true, text: 'Now: press Solve now to start it.' }];
    default:
      return [
        { gate: 'other', current: true, text: `Now: ${REASON_COPY[operation.reason ?? ''] ?? 'it needs your attention'} — see below.` },
      ];
  }
}

type OperationAction = 'solve' | 'approve' | 'use-settings' | 'dismiss';

/** Reasons whose backend message the card on screen already says in its own
 * guidance and controls. */
const SAID_BY_THE_CARD: ReadonlySet<string> = new Set(['setup_required', 'frame_confirmation_required']);

interface Guidance {
  text: string | null;
  simulation: boolean;
  /** The action the reason calls for, offered while the operation waits. */
  action: Exclude<OperationAction, 'dismiss'> | 'confirm-frame' | null;
}

/** What the user can do about the reason an operation waits. A card is only
 * ever shown for the model on screen (`CadOperationsSection`). */
function guidance(operation: CadOperationSummary): Guidance {
  switch (operation.reason) {
    case 'setup_required':
      return {
        text: 'This model is on screen: check its solve settings in Simulation, then use them to solve it.',
        simulation: true,
        action: 'use-settings',
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
    case 'frame_confirmation_required':
      // An unlinked model: the backend solves nothing until its project's
      // solver frame is confirmed, whichever way the solve was asked for.
      // The frame chooser says why; nothing here repeats it.
      return { text: null, simulation: false, action: 'confirm-frame' };
    case 'update_restart_pending':
      // The backend queues it again by itself; Solve now would only be refused until then.
      return {
        text: 'WG is about to restart to install an update, so it starts nothing now. It prepares this again by itself once it has restarted, or once the restart is called off.',
        simulation: false,
        action: null,
      };
    default:
      return { text: null, simulation: false, action: 'solve' };
  }
}

function CadOperationCard({ operation }: { operation: CadOperationSummary }) {
  const coordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe, cadLinkCoordinatorBridge.getSnapshot, cadLinkCoordinatorBridge.getSnapshot,
  );
  // The action asked for, and the operation as it stood then. The answer is
  // the row as it was before the backend claimed it, so that action stays held
  // until the operation moves on -- a newer attempt, or another state -- or the
  // request fails. Re-enabling on the answer let a second press start a second
  // attempt. The other actions stay available: the request can still be dismissed.
  const [asked, setAsked] = useState<{ action: OperationAction; attemptGeneration: number; state: string } | null>(null);
  const heldAction = asked !== null
    && asked.attemptGeneration === operation.attemptGeneration && asked.state === operation.state
    ? asked.action
    : null;
  const review = useFindingReview(operation);
  const documentName = operation.snapshot?.documentName ?? null;
  const help = guidance(operation);
  const solve = operation.kind === 'prepare_and_solve';
  // A received operation is the backend's own loop to prepare.
  const waiting = solve && operation.state === 'needs_user_input';
  const label = documentName ?? `operation ${operation.operationId}`;
  // Only the preparation the operation names: an approval never carries to another.
  const reviewedPreparation = review.findingIds.length > 0
    && review.preparationId === operation.preparationId ? review.preparationId : null;
  // A solve started from Simulation → Solve bound its settings on its first
  // prepare only, and no project setup was recorded for it. Every follow-up
  // therefore sends the settings on screen, as "Use these settings and solve"
  // does; one without them falls back to the project setup and stops again.
  const manual = operation.operationId.startsWith('manual-solve:');
  const solveRequest = () => (manual
    ? coordinator.solveOperationWithSettings(operation.operationId)
    : coordinator.solveOperation(operation.operationId));
  const approveRequest = (approvals: { preparationId: string; findingIds: string[] }) => (manual
    ? coordinator.solveOperationWithSettings(operation.operationId, approvals)
    : coordinator.approveOperation(operation.operationId, approvals));
  const ask = (action: OperationAction, request: () => Promise<void>) => {
    setAsked({ action, attemptGeneration: operation.attemptGeneration, state: operation.state });
    void request().catch(() => setAsked(null));
  };
  const solveNow = <button
    className="primary"
    disabled={heldAction === 'solve'}
    aria-label={`Solve now: ${label}`}
    title="Prepare this model from its project’s own solve settings and start the solve."
    onClick={() => ask('solve', solveRequest)}
  >Solve now</button>;
  // The stage is the backend's bookkeeping ("validating"); the state and the
  // reason are what the user acts on.
  const status = [
    STATE_COPY[operation.state] ?? operation.state,
    operation.reason ? REASON_COPY[operation.reason] ?? operation.reason : null,
  ].filter(Boolean).join(' · ');
  // For the model on screen the card's own guidance and controls say what the
  // backend's message says, which named menus for a model not on screen.
  const message = SAID_BY_THE_CARD.has(operation.reason ?? '') ? null : operation.message;
  const ladder = solveGateLadder(operation, review.error ? null : review);
  return <div className="cad-direction-alert cad-operation" data-operation-id={operation.operationId}>
    <div>
      <b>{solve ? (manual ? 'Your solve is waiting' : 'Fusion asked for a solve') : `CAD operation · ${operation.kind}`}{documentName ? ` · ${documentName}` : ''}</b>
      <span role="status">{status}</span>
      {message && <span>{message}</span>}
      {ladder.length > 0 && <ol className="cad-operation-ladder" aria-label="What this solve still needs">
        {ladder.map((step) => <li
          key={step.gate}
          data-gate={step.gate}
          aria-current={step.current ? 'step' : undefined}
          className={step.current ? 'current' : undefined}
        >{step.text}</li>)}
      </ol>}
      {/* Listed at the gate that approves them, where Approve and solve is. */}
      {reviewedPreparation && operation.reason === 'findings_need_review' && <ul className="cad-operation-findings">
        {review.findingIds.map((id) => {
          const finding = review.findings.find((item) => item.id === id);
          return <li key={id}>{finding ? findingWords(finding) : 'A finding whose details could not be read'}</li>;
        })}
      </ul>}
      {/* At the frame gate the read only forecasts the next gate; failing it
          must not read as a problem with this one. */}
      {review.error && operation.reason === 'findings_need_review' && <span>Could not read the findings to review: {review.error}</span>}
      {help.text && <span>{help.text}</span>}
      {waiting && help.action === 'confirm-frame' && heldAction !== 'solve' && <CadSolverFrameConfirm
        key={`${operation.operationId}:${operation.attemptGeneration}:${operation.preparationId ?? ''}`}
        snapshot={{ operationId: operation.operationId }}
        label={label}
        onConfirmed={() => ask('solve', solveRequest)}
      />}
    </div>
    <div className="cad-confirm-actions">
      {operation.state !== 'cancel_requested' && <button
        disabled={heldAction === 'dismiss'}
        aria-label={`Dismiss: ${label}`}
        title="Dismiss this request. Fusion will not offer it again."
        onClick={() => ask('dismiss', () => coordinator.dismissOperation(operation.operationId))}
      >Dismiss</button>}
      {review.error && operation.reason === 'findings_need_review' && <button aria-label={`Retry reading the findings for ${label}`} onClick={review.retry}>Retry</button>}
      {help.simulation && <button
        aria-label={`Open Simulation for ${label}`}
        onClick={() => workspaceNavigation.navigate('simulation')}
      >Open Simulation</button>}
      {waiting && help.action === 'approve' && reviewedPreparation && <button
        className="primary"
        disabled={heldAction === 'approve'}
        aria-label={`Approve and solve: ${label}`}
        onClick={() => ask('approve', () => approveRequest({
          preparationId: reviewedPreparation, findingIds: review.findingIds,
        }))}
      >Approve and solve</button>}
      {waiting && help.action === 'use-settings' && <button
        className="primary"
        disabled={heldAction === 'use-settings'}
        aria-label={`Use these settings and solve: ${label}`}
        title="Record the settings on screen as this model’s project setup, then prepare and solve it."
        onClick={() => ask('use-settings', () => coordinator.solveOperationWithSettings(operation.operationId))}
      >Use these settings and solve</button>}
      {waiting && help.action === 'solve' && solveNow}
    </div>
  </div>;
}

/** An interrupted Fusion update the active document still reports: the one
 * recovery that is about the model in front of the user. */
function RecoveryOperationCard({ operation }: { operation: CadOperationSummary }) {
  const coordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe, cadLinkCoordinatorBridge.getSnapshot, cadLinkCoordinatorBridge.getSnapshot,
  );
  const [asked, setAsked] = useState<'dismiss' | 'reconcile' | null>(null);
  const documentName = operation.snapshot?.documentName ?? coordinator.fusionStatus?.documentName ?? null;
  const label = documentName ?? 'the Fusion document';
  const ask = (action: 'dismiss' | 'reconcile', request: () => Promise<void>) => {
    setAsked(action);
    void request().then(() => setAsked(null), () => setAsked(null));
  };
  return <div className="cad-direction-alert cad-operation cad-operation-recovery" data-operation-id={operation.operationId}>
    <div>
      <b>Update interrupted — recovery required{documentName ? ` · ${documentName}` : ''}</b>
      <span>Fusion has no transaction covering these edits. Use Undo in Fusion to recover the document, or repair the link; do not continue modelling on a partially failed rebuild.</span>
      <span>Dismissing this card does not repair Fusion, and WGLink will not repeat the update.</span>
    </div>
    <div className="cad-confirm-actions">
      <button
        disabled={asked !== null}
        aria-label={`Dismiss recovery notice: ${label}`}
        onClick={() => ask('dismiss', () => coordinator.dismissOperation(operation.operationId))}
      >Dismiss</button>
      <button
        className="primary"
        disabled={asked !== null}
        aria-label={`Check Fusion again: ${label}`}
        onClick={() => ask('reconcile', () => coordinator.reconcileOperation(operation.operationId))}
      >Check Fusion again</button>
    </div>
  </div>;
}

/** What an earlier request was, in a few quiet words. */
function earlierRequestLabel(operation: CadOperationSummary): string {
  const name = operation.snapshot?.documentName ?? null;
  if (operation.kind === 'prepare_and_solve') return `Solve request · ${name ?? 'a model not on screen'}`;
  return `Interrupted Fusion update · ${name ?? 'another document'}`;
}

/** Requests about a model that is not on screen: a line each, never a card. */
function EarlierRequests({ operations }: { operations: CadOperationSummary[] }) {
  const coordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe, cadLinkCoordinatorBridge.getSnapshot, cadLinkCoordinatorBridge.getSnapshot,
  );
  const [dismissing, setDismissing] = useState<ReadonlySet<string>>(new Set());
  const [opening, setOpening] = useState(false);
  const openingRef = useRef(false);
  const dismiss = (ids: string[]) => {
    setDismissing((held) => new Set([...held, ...ids]));
    void (async () => {
      for (const id of ids) {
        await coordinator.dismissOperation(id).catch(() => undefined);
      }
    })().finally(() => setDismissing((held) => new Set([...held].filter((id) => !ids.includes(id)))));
  };
  // A solve waiting on its project's settings can still be taken up: opening
  // its project puts the model on screen, where its card offers the rest. The
  // project switcher's own open: it asks before discarding a design that
  // exists nowhere else, and never opens over anything opened since. What the
  // open found is the coordinator's to say.
  const openProject = async (operation: CadOperationSummary) => {
    const lineageId = operation.snapshot?.projectLineageId;
    // Held from the first click, before the question, as the switcher is.
    if (!lineageId || openingRef.current) return;
    openingRef.current = true;
    setOpening(true);
    try {
      const project = (await listCadProjects()).find((item) => item.lineageId === lineageId);
      if (!project) throw new Error(`This copy of WG does not hold the project ${operation.snapshot?.documentName ?? 'this request'} belongs to.`);
      await openCadProject(project);
    } catch (reason) {
      coordinator.reportError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      openingRef.current = false;
      setOpening(false);
    }
  };
  return <details className="cad-earlier-requests">
    <summary>Earlier requests ({operations.length})</summary>
    <ul>
      {operations.map((operation) => {
        const label = earlierRequestLabel(operation);
        const name = operation.snapshot?.documentName ?? null;
        const openable = operation.kind === 'prepare_and_solve' && operation.state === 'needs_user_input'
          && operation.reason === 'setup_required' && Boolean(operation.snapshot?.projectLineageId);
        return <li key={operation.operationId} data-operation-id={operation.operationId}>
          <span>{label}</span>
          {operation.createdAt && <time dateTime={operation.createdAt} title={fullTime(operation.createdAt)}>{relativeTime(operation.createdAt)}</time>}
          {openable && <button
            className="link-button"
            disabled={opening}
            aria-label={`Open ${name ?? 'the project'} to choose its solve settings`}
            onClick={() => void openProject(operation)}
          >Open {name ?? 'its project'}</button>}
          {operation.state !== 'cancel_requested' && <button
            className="link-button"
            disabled={dismissing.has(operation.operationId)}
            aria-label={`Dismiss: ${label}`}
            onClick={() => dismiss([operation.operationId])}
          >Dismiss</button>}
        </li>;
      })}
    </ul>
    <button
      className="link-button"
      disabled={operations.every((operation) => dismissing.has(operation.operationId))}
      onClick={() => dismiss(operations.filter((operation) => operation.state !== 'cancel_requested').map((operation) => operation.operationId))}
    >Clear all</button>
  </details>;
}

/** Whether an operation is about what is in front of the user: a solve of the
 * model on screen (the same snapshot), or a recovery the active Fusion
 * document still reports. */
function aboutWhatIsOnScreen(
  operation: CadOperationSummary,
  record: CadReturnIngestRecord | null,
  reportedRecovery: string | null,
): boolean {
  if (operation.kind === 'prepare_and_solve') {
    const manifest = operation.snapshot?.manifestSha256 ?? null;
    return Boolean(record && manifest && record.manifest_sha256 === manifest);
  }
  return reportedRecovery !== null && operation.operationId === reportedRecovery;
}

/** The waiting or running solves of the model on screen. */
export function onScreenSolves(
  operations: Record<string, CadOperationSummary>,
  record: CadReturnIngestRecord | null,
): CadOperationSummary[] {
  return pendingCadOperations(operations)
    .filter((operation) => operation.kind === 'prepare_and_solve' && aboutWhatIsOnScreen(operation, record, null));
}

/** The CAD operations still waiting or running: the solves Fusion sent, which
 * the backend prepares from each project's own setup. Only those about the
 * model on screen get a card; the rest are one quiet line each. */
export function CadOperationsSection({ record }: { record: CadReturnIngestRecord | null }) {
  const operations = useCadOperationsStore((state) => state.operations);
  const coordinator = useSyncExternalStore(
    cadLinkCoordinatorBridge.subscribe, cadLinkCoordinatorBridge.getSnapshot, cadLinkCoordinatorBridge.getSnapshot,
  );
  const pending = pendingCadOperations(operations)
    .filter((operation) => operation.kind === 'prepare_and_solve'
      || (operation.state === 'recovery_required'
        && (operation.kind === 'insert_link' || operation.kind === 'update_link')));
  if (!pending.length) return null;
  const reportedRecovery = coordinator.fusionStatus?.recoveryRequired?.operationId ?? null;
  const current = pending.filter((operation) => aboutWhatIsOnScreen(operation, record, reportedRecovery));
  const earlier = pending.filter((operation) => !aboutWhatIsOnScreen(operation, record, reportedRecovery));
  return <div className="cad-operations">
    {current.map((operation) => operation.kind === 'prepare_and_solve'
      ? <CadOperationCard key={operation.operationId} operation={operation}/>
      : <RecoveryOperationCard key={operation.operationId} operation={operation}/>)}
    {earlier.length > 0 && <EarlierRequests operations={earlier}/>}
  </div>;
}
