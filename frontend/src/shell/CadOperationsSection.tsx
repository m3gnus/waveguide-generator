import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { isActiveJobStatus, jobsSocket } from '../api/jobsSocket';
import { getIngest, type CadReturnFinding, type CadReturnIngestRecord } from '../api/cadlink';
import type { CadOperationSummary } from '../api/cadOperations';
import { listCadProjects } from '../api/cadProjects';
import { isPendingCadOperation, reconcileCadOperation } from '../api/cadOperations';
import { createCadRequestActions, selectCadRequests, useCadRecoveryNotices, type CadRequest } from '../stores/cadRecoveryNotices';
import { cadWorkspace } from '../stores/cadWorkspace';
import { openCadProject } from './CadProjectPanel';
import { cadJobSummaries, dismissCadJob, useCadJobs } from '../jobs/cadSolve';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { inFlightWords } from './cadOnScreenSettings';
import { fullTime, relativeTime } from './cadTime';
import { usePanelVisible } from './panelVisibility';
import { workspaceNavigation } from './workspaceNavigation';

const RECOVERY_DISMISSED = 'Dismissed this recovery notice. Fusion still needs Undo or repair before you continue modelling.';

const makeRequestActions = () => createCadRequestActions({
  openProject: async (lineage) => {
    const project = (await listCadProjects()).find((item) => item.lineageId === lineage);
    if (!project) throw new Error('This copy of WG does not hold that project.');
    await openCadProject(project);
  },
});

/** The hash of a `sha256:` digest, cut to what a person can read and compare. */
export function shortSha256(digest: string | null | undefined): string {
  return (digest ?? '').replace(/^sha256:/, '').slice(0, 12);
}

const STATE_COPY: Record<string, string> = {
  received: 'Received',
  processing: 'Preparing',
  needs_user_input: 'Waiting for you',
  recovery_required: 'Needs recovery',
  cancel_requested: 'Stopping',
};

const REASON_COPY: Record<string, string> = {
  setup_required: 'needs its solve settings',
  findings_need_review: 'blocking findings to review',
  preparation_failed: 'preparation failed',
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
 * (server/cadlink/joblane/preparation.py): frame, then findings, then the submission. */
const LADDER_REASONS: ReadonlySet<string> = new Set(['frame_confirmation_required', 'findings_need_review']);

/** The blocking findings one preparation reported, as the backend records them.
 *
 * Read at the frame gate as well as at the findings gate: `reason` names one
 * gate, the current one, while the preparation behind it already knows its
 * blocking findings (published before the frame gate returns). Reading them
 * only at their own gate is what let a user fix the frame and then hit a second
 * wall with no warning it was coming. */
function useFindingReview(operation: CadOperationSummary): FindingReview & { retry: () => void } {
  const jobs = useCadJobs();
  const job = jobs.find((item) => item.id === operation.jobId);
  const wanted = operation.kind === 'prepare_and_solve'
    && operation.state === 'needs_user_input'
    && LADDER_REASONS.has(operation.reason ?? '');
  const [review, setReview] = useState<FindingReview>(NO_REVIEW);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!wanted) return undefined;
    let current = true;
    void (async () => {
      const detail = job?.cad_state;
      const preparation = detail?.preparation;
      if (!current) return;
      if (!preparation) throw new Error('the backend has not recorded its preparation yet');
      // The ids are the review; the ingestion record only puts words to them.
      const findings = await getIngest(String(preparation.ingest_id ?? ''))
        .then((record) => record.findings.filter((finding) => preparation.blocking_finding_ids.includes(finding.id)))
        .catch(() => [] as CadReturnFinding[]);
      if (current) {
        const approvedIds = approvedFindingIds(detail!.approvals, preparation.preparation_id)
          .filter((id) => preparation.blocking_finding_ids.includes(id));
        setReview({
          preparationId: preparation.preparation_id,
          findingIds: preparation.blocking_finding_ids,
          approvedIds,
          findings,
          error: null,
        });
      }
    })().catch((reason: unknown) => {
      if (current) setReview({ ...NO_REVIEW, error: reason instanceof Error ? reason.message : String(reason) });
    });
    return () => { current = false; };
  }, [wanted, operation.operationId, operation.preparationId, operation.attemptGeneration, operation.reason, job, attempt]);
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
          text: 'Now: check which way it radiates, above, and press Solve to confirm it.',
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
      return [{ gate: 'solve', current: true, text: 'Now: press Solve to start it.' }];
    default:
      return [
        { gate: 'other', current: true, text: `Now: ${REASON_COPY[operation.reason ?? ''] ?? 'it needs your attention'} — see below.` },
      ];
  }
}

/** What a waiting request's own card still offers. Solving it is the Solve
 * card's one Solve (PLAN.md M1b): it continues this very request with the
 * settings and frame on screen. Approving a finding stays explicit. */
type OperationAction = 'approve' | 'dismiss' | 'stop';

/** Reasons whose backend message the card on screen already says in its own
 * guidance and controls. */
const SAID_BY_THE_CARD: ReadonlySet<string> = new Set([
  'setup_required', 'frame_confirmation_required',
  // Its message lists the findings by id; the card lists them in words.
  'findings_need_review',
]);

interface Guidance {
  text: string | null;
  simulation: boolean;
  /** The action the reason calls for, offered on this card while it waits;
   * null when Solve is the action. */
  action: 'approve' | null;
}

/** What the user can do about the reason an operation waits. A card is only
 * ever shown for the model on screen (`CadOperationsSection`). */
function guidance(operation: CadOperationSummary): Guidance {
  if (operation.reason?.startsWith('engine_') || operation.reason === 'submission_refused') {
    return { text: null, simulation: false, action: null };
  }
  switch (operation.reason) {
    case 'setup_required':
      return {
        text: 'This model is on screen: check the settings above, then press Solve. WG remembers them for this project.',
        simulation: false,
        action: null,
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
      // The frame line above says which way; Solve confirms it.
      return { text: null, simulation: false, action: null };
    case 'update_restart_pending':
      // The backend queues it again by itself; Solve now would only be refused until then.
      return {
        text: 'WG is about to restart to install an update, so it starts nothing now. It prepares this again by itself once it has restarted, or once the restart is called off.',
        simulation: false,
        action: null,
      };
    case 'ready_to_solve':
      return { text: null, simulation: false, action: null };
    default:
      // A failed or interrupted preparation: Solve tries it again, with the
      // settings shown, as the same request.
      return { text: 'Press Solve to try it again with the settings shown.', simulation: false, action: null };
  }
}

function CadOperationCard({ operation, showStatus = true }: { operation: CadOperationSummary; showStatus?: boolean }) {
  const jobs = useCadJobs();
  const owner = useSyncExternalStore(jobsCoordinatorBridge.subscribe, jobsCoordinatorBridge.getSnapshot, jobsCoordinatorBridge.getSnapshot);
  const approvalUnavailable = owner.available ? null : 'Solve coordinator is unavailable. Reopen this workspace to approve and solve.';
  const job = jobs.find((item) => item.id === operation.jobId)!;
  // The action asked for, and the operation as it stood then. The answer is
  // the row as it was before the backend claimed it, so that action stays held
  // until the operation moves on -- a newer attempt, or another state -- or the
  // request fails. Re-enabling on the answer let a second press start a second
  // attempt. The other actions stay available: the request can still be dismissed.
  const [asked, setAsked] = useState<{ action: OperationAction; jobId: string; preparationId: string | null; state: string } | null>(null);
  const askedRef = useRef<typeof asked>(null);
  const heldAction = asked !== null
    && asked.jobId === job.id && asked.preparationId === operation.preparationId && asked.state === operation.state
    ? asked.action
    : null;
  const review = useFindingReview(operation);
  const documentName = operation.snapshot?.documentName ?? null;
  const help = guidance(operation);
  const solve = operation.kind === 'prepare_and_solve';
  // A received operation is the backend's own loop to prepare.
  const waiting = solve && operation.state === 'needs_user_input';
  const active = isActiveJobStatus(job.status);
  const label = documentName ?? `operation ${operation.operationId}`;
  // Only the preparation the operation names: an approval never carries to another.
  const reviewedPreparation = review.findingIds.length > 0
    && review.preparationId === operation.preparationId ? review.preparationId : null;
  const manual = operation.operationId.startsWith('manual-solve:');
  // An approval is a continuation: it sends no settings, so the backend
  // reuses the setup revision the operation holds and the approvals given on
  // its preparation still apply. Everything else this request waits for is
  // the Solve card's one Solve, which continues this very request.
  const ask = (action: OperationAction, request: () => Promise<unknown>) => {
    const held = askedRef.current;
    if (held?.action === action && held.jobId === job.id && held.preparationId === operation.preparationId && held.state === operation.state) return;
    const press = { action, jobId: job.id, preparationId: operation.preparationId, state: operation.state };
    askedRef.current = press;
    setAsked(press);
    const release = () => {
      if (askedRef.current !== press) return;
      askedRef.current = null;
      setAsked(null);
    };
    void request().then((result) => {
      // A busy owner accepted nothing; the user can press again.
      if (result === null) release();
    }).catch((reason: unknown) => {
      release();
      jobsCoordinatorBridge.getSnapshot().reportError(reason instanceof Error ? reason.message : String(reason));
    });
  };
  // The stage is the backend's bookkeeping ("validating"); the state and the
  // reason are what the user acts on.
  const engineReason = operation.reason?.startsWith('engine_') || operation.reason === 'submission_refused';
  const status = [
    STATE_COPY[operation.state] ?? operation.state,
    operation.reason && !engineReason ? REASON_COPY[operation.reason] ?? operation.reason : null,
  ].filter(Boolean).join(' · ');
  // For the model on screen the card's own guidance and controls say what the
  // backend's message says, which named menus for a model not on screen.
  const message = engineReason || SAID_BY_THE_CARD.has(operation.reason ?? '') || (!showStatus && !waiting) ? null : operation.message;
  const ladder = engineReason ? [] : solveGateLadder(operation, review.error ? null : review);
  const header = solve
    ? manual ? waiting ? 'Your solve is waiting' : 'Your solve is in progress' : 'Fusion asked for a solve'
    : `CAD operation · ${operation.kind}`;
  return <div className="cad-direction-alert cad-operation" data-operation-id={operation.operationId} data-request-id={job.id} tabIndex={-1}>
    <div>
      <b>{header}{documentName ? ` · ${documentName}` : ''}</b>
      {showStatus && <span role="status">{status}</span>}
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
      {waiting && help.text && <span>{help.text}</span>}
      {showStatus && solve && (operation.state === 'received' || operation.state === 'processing')
        && <span className="cad-operation-in-flight">{inFlightWords(operation)}</span>}
    </div>
    <div className="cad-confirm-actions">
      {waiting && !active && <button
        disabled={heldAction === 'dismiss'}
        aria-label={`Dismiss: ${label}`}
        title="Dismiss this request. Fusion will not offer it again."
        onClick={() => ask('dismiss', () => dismissCadJob(job))}
      >Dismiss</button>}
      {active && operation.state !== 'cancel_requested' && <button
        disabled={heldAction === 'stop'}
        aria-label={`Stop: ${label}`}
        onClick={() => ask('stop', () => jobsSocket.stopJob(job.id))}
      >Stop</button>}
      {review.error && operation.reason === 'findings_need_review' && <button aria-label={`Retry reading the findings for ${label}`} onClick={review.retry}>Retry</button>}
      {help.simulation && <button
        aria-label={`Open Simulation for ${label}`}
        onClick={() => workspaceNavigation.navigate('simulation')}
      >Open Simulation</button>}
      {waiting && help.action === 'approve' && reviewedPreparation && <>
        {approvalUnavailable && <span role="status">{approvalUnavailable}</span>}
        <button
        className="primary"
        disabled={heldAction === 'approve' || approvalUnavailable !== null}
        aria-label={`Approve and solve: ${label}`}
        onClick={() => ask('approve', () => jobsCoordinatorBridge.getSnapshot().continueRun(job.id, {
          approvals: { preparation_id: reviewedPreparation, finding_ids: review.findingIds },
        }))}
      >Approve and solve</button></>}
    </div>
  </div>;
}

/** Request feedback stays with the rows, including outside CAD input. */
function useRequestAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busyRef = useRef(false);
  const ask = (action: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    void action().catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => { busyRef.current = false; setBusy(false); });
  };
  return { busy, error, ask };
}

/** An interrupted Fusion update the active document still reports: the one
 * recovery that is about the model in front of the user. */
function RecoveryOperationCard({ request, requestActions }: { request: CadRequest; requestActions: ReturnType<typeof makeRequestActions> }) {
  const operation = request.operation;
  const coordinator = useSyncExternalStore(
    cadWorkspace.subscribe, cadWorkspace.getSnapshot, cadWorkspace.getSnapshot,
  );
  const { busy, error, ask } = useRequestAction();
  const documentName = operation.snapshot?.documentName ?? coordinator.fusionStatus?.documentName ?? null;
  const label = documentName ?? 'the Fusion document';
  return <div className="cad-direction-alert cad-operation cad-operation-recovery" data-operation-id={operation.operationId} data-request-id={request.id} tabIndex={-1}>
    <div>
      <b>Update interrupted — recovery required{documentName ? ` · ${documentName}` : ''}</b>
      <span>Fusion has no transaction covering these edits. Use Undo in Fusion to recover the document, or repair the link; do not continue modelling on a partially failed rebuild.</span>
      <span>Dismissing this card does not repair Fusion, and the Waveguide Generator add-in in Fusion will not repeat the update.</span>
      {error && <span role="alert">{error}</span>}
    </div>
    <div className="cad-confirm-actions">
      <button
        disabled={busy}
        aria-label={`Dismiss recovery notice: ${label}`}
        onClick={() => ask(async () => {
          await requestActions.dismiss(request);
          coordinator.reportStatus(RECOVERY_DISMISSED);
        })}
      >Dismiss</button>
      <button
        className="primary"
        disabled={busy}
        aria-label={`Check Fusion again: ${label}`}
        onClick={() => ask(async () => {
          await reconcileCadOperation(request.id);
          await coordinator.refresh();
          coordinator.reportStatus('Checked Fusion recovery notices. Use Undo in Fusion or repair the link if its warning remains.');
        })}
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
function EarlierRequests({ requests, requestActions }: { requests: CadRequest[]; requestActions: ReturnType<typeof makeRequestActions> }) {
  const coordinator = useSyncExternalStore(cadWorkspace.subscribe, cadWorkspace.getSnapshot, cadWorkspace.getSnapshot);
  const { busy, error, ask } = useRequestAction();
  return <details className="cad-earlier-requests">
    <summary>Earlier requests ({requests.length})</summary>
    {error && <p role="alert">{error}</p>}
    <ul>
      {requests.map((request) => {
        const operation = request.operation;
        const label = earlierRequestLabel(operation);
        const name = operation.snapshot?.documentName ?? null;
        return <li key={`${request.source}:${request.id}`} data-operation-id={operation.operationId} data-request-id={request.id} tabIndex={-1}>
          <span>{label}</span>
          {operation.createdAt && <time dateTime={operation.createdAt} title={fullTime(operation.createdAt)}>{relativeTime(operation.createdAt)}</time>}
          {operation.reason && <span>{REASON_COPY[operation.reason] ?? operation.reason}</span>}
          {operation.message && <span>{operation.message}</span>}
          {request.openable && <button
            className="link-button"
            disabled={busy}
            aria-label={`Open ${name ?? 'the project'} to review ${operation.reason === 'frame_confirmation_required' ? 'its solver frame' : 'its solve settings'}`}
            onClick={() => ask(() => requestActions.open(request))}
          >Open {name ?? 'its project'}</button>}
          <button
            className="link-button"
            disabled={busy}
            aria-label={`Dismiss: ${label}`}
            onClick={() => ask(async () => {
              await requestActions.dismiss(request);
              if (request.source === 'recovery') coordinator.reportStatus(RECOVERY_DISMISSED);
            })}
          >Dismiss</button>
        </li>;
      })}
    </ul>
    <button
      className="link-button"
      disabled={busy}
      onClick={() => ask(() => requestActions.clearAll(requests))}
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
  return Object.values(operations).filter(isPendingCadOperation)
    .filter((operation) => operation.kind === 'prepare_and_solve' && aboutWhatIsOnScreen(operation, record, null));
}

/** The waiting or running solves of the model on screen, as the Solve card
 * shows them: what each still needs, in words, with only the actions Solve
 * does not take -- approving a finding, dismissing the request. */
export function CurrentModelRequests({ record }: { record: CadReturnIngestRecord | null }) {
  const jobs = useCadJobs();
  const solves = onScreenSolves(cadJobSummaries(jobs), record);
  if (!solves.length) return null;
  return <div className="cad-operations cad-operations-on-screen">
    {solves.map((operation) => <CadOperationCard key={operation.jobId ?? operation.operationId} operation={operation} showStatus={false}/>)}
  </div>;
}

/** The CAD operations still waiting or running: the solves Fusion sent, which
 * the backend prepares from each project's own setup. Only those about the
 * model on screen get a card -- on the Solve card when there is one
 * (`solves={false}` here) -- and the rest are one quiet line each. */
export function CadOperationsSection({ record, solves = true }: { record: CadReturnIngestRecord | null; solves?: boolean }) {
  const requestActions = useMemo(makeRequestActions, []);
  const host = useRef<HTMLDivElement>(null);
  const visible = usePanelVisible();
  const navigation = useSyncExternalStore(workspaceNavigation.subscribe, workspaceNavigation.getSnapshot, workspaceNavigation.getSnapshot);
  const notices = useCadRecoveryNotices((state) => state.notices);
  const jobs = useCadJobs();
  const selected = selectCadRequests({ jobs, notices, record });
  const current = selected.current.filter((item) => solves || item.operation.kind !== 'prepare_and_solve');
  const earlier = selected.earlier;
  const firstRequestId = earlier[0]?.id ?? selected.current[0]?.id;
  useEffect(() => {
    if (!visible || workspaceNavigation.getRevealTarget() !== 'cad-requests' || !firstRequestId || !host.current) return;
    // The Solve card can own the current request. Earlier rows stay here.
    const scope = host.current.closest('.cadlink-panel') ?? host.current;
    const target = [...scope.querySelectorAll<HTMLElement>('[data-request-id]')]
      .find((element) => element.dataset.requestId === firstRequestId);
    if (!target) return;
    const disclosure = target.closest('details');
    if (disclosure) disclosure.open = true;
    target.scrollIntoView?.({ block: 'nearest' });
    target.focus();
    workspaceNavigation.claimReveal('cad-requests');
  }, [navigation, firstRequestId, visible]);

  const active = selected.active.filter((item) => solves || !item.onScreen);
  if (!selected.current.length && !earlier.length && !active.length) return null;
  return <div className="cad-operations" ref={host}>
    {current.map((request) => request.source === 'job'
      ? <CadOperationCard key={request.id} operation={request.operation}/>
      : <RecoveryOperationCard key={request.id} request={request} requestActions={requestActions}/>)}
    {active.map((request) => <CadOperationCard key={request.id} operation={request.operation}/>)}
    {earlier.length > 0 && <EarlierRequests requests={earlier} requestActions={requestActions}/>}
  </div>;
}
