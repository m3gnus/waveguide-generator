import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { compareSelection, fetchJobResults } from '../api/results';
import { createSetupRevision, getSetupRevision, putProjectSetup, type CadOperationSummary, type CadSolveSetup } from '../api/cadOperations';
import { CadLinkApiError, type CadReturnIngestRecord } from '../api/cadlink';
import { importedSolvePlanRequestBody, planSolveDesign, postImportedSolvePlan, SolveSubmissionRefused, submitDesign, submitImported, type EngineSubstitution, type ImportedSolveSubmission, type SolvePlan } from '../jobs/actions';
import {
  useCapabilities,
  useCapabilityRefreshOnReconnect,
  useLegacyBeatEngineMigration,
} from '../jobs/useCapabilities';
import { useSolvePlan } from '../jobs/useSolvePlan';
import { JobAutomation } from '../jobs/automation';
import { exportStemForJob, exportSubdirectoryForJob } from '../jobs/exportNaming';
import { explainImportedRefusal } from '../jobs/importedRefusals';
import { buildImportedSubmission, importedSubmissionBlocker } from '../jobs/importedSubmission';
import { acknowledgeCadSolve, beginCadSolve, cadJobSummaries, discardUnsubmittedCadSolve, forgetPendingCadSolve, hasLegacyCadSolve, cadSolveClaim, latestCadJobs, pendingCadSolve, recoverLegacyCadSolves, releaseCadSolve, rememberCadSolve, retainCadSolvePress, solveCadAgain, submitCadSolve } from '../jobs/cadSolve';
import { useImportedSolvePlan } from '../jobs/useImportedSolvePlan';
import { advanceRunSequence, nextRunLabel } from '../jobs/runNaming';
import { currentRunNameSource } from '../jobs/runNameSource';
import { preferencesStore, usePreferences } from '../prefs/preferences';
import { archiveRunToWorkspace, runWorkspaceExportBundle, saveMeshArtifactToWorkspace } from '../results/exporters';
import { resultExportSnapshot } from '../results/exportContext';
import type { ResultPayload } from '../results/types';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useCadReturnStore } from '../stores/cadReturn';
import { useCadOperationsStore } from '../stores/cadOperations';
import { confirmDisplayedFrame, frameSolveBlocker, frameReadInFlight, useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { polarValidationError, useSolveOptionsStore, type SolveOptions } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { buildCadProjectSetup } from './cadSetupPublisher';
import { getCrossoverDraftError, useCrossoverDraftError } from '../design/crossoverDrafts';
import { inFlightWords, onScreenRequestInFlight, onScreenRequestToContinue } from './cadOnScreenSettings';
import { solveAttention, useJobAttention, useOperationAttention } from './solveAttention';
import { solvedCadModels } from './cadlink/solvedModel';

/**
 * A line of text rendered beside the Solve button, not inside its `title`.
 *
 * A disabled control cannot be hovered usefully, so a tooltip was the one place
 * this could not go -- and until now it was the only place it went. `blocked`
 * says why Solve cannot run; `substituted` says the solve will run, just not on
 * the engine that was asked for. Neither interrupts anything.
 */
export interface SolveNotice {
  tone: 'blocked' | 'substituted';
  text: string;
}

interface SolveControl {
  solve(): void;
  /** CAD Link mode: Solve solves the CAD model on screen. */
  cadMode: boolean;
  disabled: boolean;
  submitting: boolean;
  label: string;
  title: string;
  notice: SolveNotice | null;
}

/** No engine on this machine can solve imported geometry right now. The server
 * decides that from what each engine declares it can solve. It is a capability
 * the host lacks, not a verdict on the request, so an automatic caller keeps
 * the request -- blocked, with a way forward -- instead of refusing it. */
export class SolveEngineUnavailableError extends Error {}

/** The server refusal that means "no engine here can take this geometry".
 * `imported_engine_unsupported` is deliberately absent: it answers a request
 * that named an engine, which is a verdict on that request, and runImported
 * always sends AUTO. */
const IMPORTED_CAPABILITY_CODES: ReadonlySet<string> = new Set(['engine_unavailable']);

interface CoordinatorBridgeSnapshot {
  run(design: DesignDocument, designRevision?: number): Promise<void>;
  /** Resolves with the submitted job id, or null when the submission mutex
   * refused this call. */
  runImported(submission: ImportedSolveSubmission): Promise<string | null>;
  /** The single CAD solve entry point: displayed-press capture, readiness,
   * job intake or continuation, submission mutex, naming and list refresh.
   * Throws the blocking reason; returns 'busy' when a solve is already in
   * flight so an automatic caller can say so instead of silently doing
   * nothing. */
  solveCurrentCadImport(): Promise<'submitted' | 'busy'>;
  retry(jobId: string): Promise<void>;
  reportError(message: string): void;
  actionError: string | null;
}

const unavailableRun = async () => { throw new Error('Solve coordinator is unavailable'); };
let bridgeSnapshot: CoordinatorBridgeSnapshot = {
  run: unavailableRun,
  runImported: unavailableRun,
  solveCurrentCadImport: unavailableRun,
  retry: unavailableRun,
  reportError: () => undefined,
  actionError: null,
};
const bridgeListeners = new Set<() => void>();

export const jobsCoordinatorBridge = {
  getSnapshot: () => bridgeSnapshot,
  subscribe(listener: () => void) {
    bridgeListeners.add(listener);
    return () => bridgeListeners.delete(listener);
  },
};

function publishBridge(snapshot: CoordinatorBridgeSnapshot): void {
  bridgeSnapshot = snapshot;
  bridgeListeners.forEach((listener) => listener());
}

const SolveContext = createContext<SolveControl | null>(null);

export function useSolveControl(): SolveControl {
  const value = useContext(SolveContext);
  if (!value) throw new Error('useSolveControl must be used below JobsCoordinator');
  return value;
}

/** The Solve command, published for panels the dock renders in their own
 * React roots (`Workspace.tsx`), where the coordinator's context does not reach. */
let publishedSolveControl: SolveControl | null = null;
const solveControlListeners = new Set<() => void>();
const publishedSolveControlStore = {
  getSnapshot: () => publishedSolveControl,
  subscribe(listener: () => void) {
    solveControlListeners.add(listener);
    return () => { solveControlListeners.delete(listener); };
  },
};

function publishSolveControl(control: SolveControl | null): void {
  publishedSolveControl = control;
  solveControlListeners.forEach((listener) => listener());
}

/** The Solve command where one exists: the CAD Link panel's Solve card is the
 * same command as the top bar's, and is rendered on its own in some tests.
 * The card lives in a dock panel, outside the coordinator's React tree, so it
 * reads the published command when the context is absent. */
export function useOptionalSolveControl(): SolveControl | null {
  const context = useContext(SolveContext);
  const published = useSyncExternalStore(
    publishedSolveControlStore.subscribe,
    publishedSolveControlStore.getSnapshot,
    publishedSolveControlStore.getSnapshot,
  );
  return context ?? published;
}

/** What a setup revision binds for a solve, without the run's own name: the
 * content two revisions must share for a continuation to keep the one bound.
 * Defaults are filled as the server stores them, and keys sorted. */
export function solveInputsKey(setup: CadSolveSetup): string {
  const options: Record<string, unknown> = { ...(setup.options ?? {}) };
  // Imported geometry is solved in full 3-D whatever a setup says.
  delete options.solver_mode;
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.keys(value as Record<string, unknown>).sort()
        .filter((key) => (value as Record<string, unknown>)[key] !== undefined)
        .map((key) => [key, canonical((value as Record<string, unknown>)[key])]));
    }
    return value;
  };
  return JSON.stringify(canonical({
    schema_version: setup.schema_version ?? 1,
    geometry: setup.geometry ?? {},
    options,
    preparation: {
      area_drift_overrides: [], symmetry_mode: 'auto', surface_deviation_mm: null,
      ...(setup.preparation ?? {}),
    },
    driver_references: Object.fromEntries(Object.entries(setup.driver_references ?? {})
      .map(([channel, reference]) => [channel, { source: null, ...reference }])),
  }));
}

/** Read naming at submission time, including a commit from the same key event. */
export function currentJobLabel(
  designName = currentRunNameSource().name,
  now = new Date(),
): string {
  return nextRunLabel(designName, preferencesStore.getSnapshot(), now);
}

/**
 * Record that a run was stored under this design's next number.
 *
 * Only the counter moves. The name itself is the document's, so a submission
 * can no longer rename the design out from under the user -- which is what the
 * old increment-and-write-back did every time the geometry changed.
 */
function acceptSubmittedLabel(designName: string): void {
  preferencesStore.update(advanceRunSequence(preferencesStore.getSnapshot(), designName));
}

const CAD_VIEWPORT_MISMATCH =
  'The displayed CAD Link mesh does not match the selected ingestion. Prepare or reselect it before solving.';

/** The complete CAD-mode readiness rule, read from live store state so the
 * Solve button, the keyboard shortcut, and every automatic caller apply one
 * gate. The viewport check is evidence about what the user is looking at; the
 * rest is the shared imported-submission blocker. */
export function cadSolveBlockerNow(): string | null {
  return cadInputBlockerNow() ?? frameSolveBlocker(useCadReturnStore.getState().ingestRecord?.ingest_id);
}

/** The same rule without the frame, which a Solve judges once the frame read
 * the card has under way has answered. */
function cadInputBlockerNow(): string | null {
  const cadReturn = useCadReturnStore.getState();
  const cad = importedMeshStore.getSnapshot().cad;
  if (cadReturn.ingestRecord !== null && cad !== null
    && (!cad.ingestId || cad.ingestId !== cadReturn.ingestRecord.ingest_id)) {
    return CAD_VIEWPORT_MISMATCH;
  }
  return importedSubmissionBlocker(cadReturn);
}

/** The request for the snapshot a Solve was given for that the backend is
 * preparing or submitting, and the words that say so. Always the snapshot
 * captured at the press, never whatever is selected by the time it is asked. */
function heldOnRequest(record: CadReturnIngestRecord | null): { operation: CadOperationSummary; words: string } | null {
  if (record && pendingCadSolve(record.ingest_id)?.press) return null;
  const operation = onScreenRequestInFlight(cadJobSummaries(jobsSocket.getSnapshot().jobs), record);
  return operation ? { operation, words: inFlightWords(operation) } : null;
}

/** A Solve given while that request is in flight -- a press that raced the
 * button's hold -- is that request's: its arm follows it, so the gate it stops
 * at or the result it ends in follows the user. Nothing new is created. */
function holdOnRequest(held: { operation: CadOperationSummary }): 'submitted' {
  solveAttention.bindOperation(held.operation.jobId!);
  return 'submitted';
}

const jobsConnection = () => jobsSocket.getSnapshot().connection;

const systemNow = () => new Date();

/**
 * Re-read a terminal job before creating its permanent archive.
 *
 * The completion event is intentionally small and can reach the browser just
 * before the following metadata event advertises retained pressure bases,
 * radiation impedance, final timings, and artifact byte counts. Archiving the
 * event snapshot makes that ordering permanent: the archive is marked done
 * without files that already exist on the server. A list refresh is the
 * server's canonical, fully serialized job view and closes that race.
 */
export async function refreshedArchiveJob(job: JobItem): Promise<JobItem> {
  await jobsSocket.refresh();
  return jobsSocket.getSnapshot().jobs.find((candidate) => candidate.id === job.id) ?? job;
}

export function JobsCoordinator({ children, now = systemNow }: { children: ReactNode; now?: () => Date }) {
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  // This component owns the jobs socket, so it is where a reconnect is visible.
  useCapabilityRefreshOnReconnect(useSyncExternalStore(jobsSocket.subscribe, jobsConnection, jobsConnection));
  useLegacyBeatEngineMigration();
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const designName = useDocumentStore((state) => state.designName);
  const solveOptions = useSolveOptionsStore();
  const selectedEngine = solveOptions.engine;
  const cadReturn = useCadReturnStore();
  const viewportGeometry = useSyncExternalStore(
    importedMeshStore.subscribe,
    importedMeshStore.getSnapshot,
    importedMeshStore.getSnapshot,
  );
  const workspaceMode = useSyncExternalStore(
    workspaceModeStore.subscribe,
    workspaceModeStore.getSnapshot,
    workspaceModeStore.getSnapshot,
  ).mode;
  const preferences = usePreferences();
  const automation = useRef(new JobAutomation()).current;
  const {
    error: capabilityError,
  } = useCapabilities();
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submissionInFlight = useRef(false);
  const cadOperations = useCadOperationsStore((state) => state.operations);
  // The frame the Solve card shows for the model on screen: part of what Solve
  // confirms, so part of whether it can.
  useCadSolverFrameStore((state) => state.frames);

  useEffect(() => { jobsSocket.start(); return () => jobsSocket.stop(); }, []);

  const completeCadSolve = useCallback((job: JobItem) => {
    if (job.cad_state?.state !== 'accepted') return;
    const claim = acknowledgeCadSolve(job, 'completion');
    if (!claim) return;
    if (claim.designName) acceptSubmittedLabel(claim.designName);
    compareSelection.awaitRun(job.id);
    if (job.client_request_id) solveAttention.followJob(job.client_request_id, job.id);
    solveAttention.bindRun(job.id, job.id);
    // The run names the model its preparation made; show that model once the
    // solve claims its run, while the same return is still on screen.
    solvedCadModels.claim(job.id, {
      manifestSha256: job.cad_state.snapshot?.manifest_sha256 ?? null,
      sourceIngestId: claim.sourceIngestId ?? null,
    });
  }, []);

  useEffect(() => {
    for (const job of latestCadJobs(jobs)) {
      const parent = jobs.find((candidate) => candidate.id === job.parent_job_id);
      const inherited = parent ? cadSolveClaim(parent) : null;
      // Merge, not only create: an Approve may have claimed the child first, without the ingestion.
      if (inherited) rememberCadSolve(job.id, inherited.designName, inherited.sourceIngestId ?? null);
      if (job.cad_state?.state === 'received' || job.cad_state?.state === 'processing') {
        if (!cadSolveClaim(job)) rememberCadSolve(job.id, '');
      }
      completeCadSolve(job);
      if (job.cad_state?.state === 'rejected') {
        if (!acknowledgeCadSolve(job, 'refusal')) continue;
        setActionError(`The solve of ${job.cad_state.snapshot?.document_name ?? 'the model'} was refused: ${job.cad_state.message ?? job.cad_state.reason ?? 'no reason given'}`);
      }
    }
  }, [jobs, completeCadSolve]);

  useJobAttention(jobs);
  useOperationAttention(Object.fromEntries(Object.entries(cadOperations)
    .filter(([, operation]) => operation.kind !== 'prepare_and_solve')));

  let currentOptions: SolveOptions | null = null;
  let solveOptionsError: string | null = null;
  try {
    currentOptions = solveOptions.options();
  } catch (error) {
    solveOptionsError = error instanceof Error ? error.message : String(error);
  }
  const {
    plan: solvePlan,
    error: solvePlanError,
    isPending: solvePlanPending,
  } = useSolvePlan(
    design,
    currentOptions,
    workspaceMode === 'parametric',
  );
  const visibleImported = viewportGeometry.showing === 'cad'
    ? viewportGeometry.cad
    : viewportGeometry.showing === 'file'
      ? viewportGeometry.file
      : null;
  // Mode owns solve intent. The viewport CAD slot is evidence for the mismatch
  // guard only; an empty slot must still route to the imported blocker so CAD
  // mode can truthfully say that an ingest is required.
  const cadGeometryActive = workspaceMode === 'cad';
  const crossoverDraftError = useCrossoverDraftError();
  const fileGeometryActive = !cadGeometryActive && visibleImported?.source === 'file';
  const cadViewportGeometry = viewportGeometry.cad;
  const cadGeometryMismatch = cadGeometryActive && cadReturn.ingestRecord !== null && cadViewportGeometry !== null && (
    !cadViewportGeometry.ingestId
    || cadViewportGeometry.ingestId !== cadReturn.ingestRecord?.ingest_id
  );
  const inFlight = cadGeometryActive && !pendingCadSolve(cadReturn.ingestRecord?.ingest_id ?? '')?.press
    ? onScreenRequestInFlight(cadJobSummaries(jobs), cadReturn.ingestRecord) : null;
  const cadSolveBlocker = cadGeometryMismatch
    ? CAD_VIEWPORT_MISMATCH
    : cadGeometryActive
      ? (inFlight ? inFlightWords(inFlight) : null)
        ?? importedSubmissionBlocker(cadReturn, solveOptions) ?? frameSolveBlocker(cadReturn.ingestRecord?.ingest_id)
      : null;
  const directivityError = polarValidationError(solveOptions.polar);
  const solveBlocker = (cadGeometryActive ? crossoverDraftError : null) ?? cadSolveBlocker ?? directivityError;
  useEffect(() => {
    if (crossoverDraftError) setActionError(crossoverDraftError);
    else setActionError((current) => current?.includes('frequency must be greater than 0 Hz') ? null : current);
  }, [crossoverDraftError]);
  // Imported geometry takes the same engine choice as a parametric design. The
  // server says, per engine, which can take this return and where the user's
  // choice resolves; Solve follows that one answer.
  const importedPlan = useImportedSolvePlan(cadGeometryActive && cadSolveBlocker === null);
  const importedEngine = importedPlan.plan?.engine ?? null;
  const importedEngineLabel = importedEngine
    ? importedPlan.plan?.engines.find((verdict) => verdict.name === importedEngine)?.label || importedEngine
    : null;
  const importedUnavailable = importedPlan.isPending
    ? 'Checking which engines can solve this CAD model…'
    : importedPlan.plan?.reason ?? importedPlan.error;

  const run = useCallback(async (nextDesign: DesignDocument, nextRevision = revision) => {
    if (submissionInFlight.current) return;
    submissionInFlight.current = true;
    try {
      setSubmitting(true);
      setActionError(null);
      // Pressing Solve never edits the design. BEMPP's own closed-wall default
      // for a bare free-standing horn is applied by the server, on the copy it
      // stores as the run's design and snapshot (server/jobs/runtime.py's
      // `_apply_bempp_wall_default`). Doing it here as well used to rewrite the
      // live document: choosing "Bare shell" and pressing Solve flipped the
      // Outer body control back to "Thickened waveguide (freestanding)",
      // discarded any expression bound to the field, bumped the revision, and
      // marked the file unsaved -- for a correction the run had already made.
      const options = useSolveOptionsStore.getState().options();
      await planSolveDesign(nextDesign, options);
      const designName = useDocumentStore.getState().designName;
      const label = nextRunLabel(designName, preferencesStore.getSnapshot(), now());
      const jobId = await submitDesign(
        nextDesign,
        options,
        fetch,
        { label, designRevision: nextRevision },
      );
      // Pressing Solve is a request to see that solve. Claiming the primary
      // slot for it here is what makes the finished run the one on screen even
      // when a result had been pinned for comparison; without it, a pin taken
      // at any point in the session quietly kept every later solve hidden.
      compareSelection.awaitRun(jobId);
      solveAttention.bindRun(jobId);
      acceptSubmittedLabel(designName);
      await jobsSocket.refresh();
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [designName, now, preferences]);

  const runImported = useCallback(async (submission: ImportedSolveSubmission) => {
    if (submissionInFlight.current) return null;
    submissionInFlight.current = true;
    try {
      setSubmitting(true);
      setActionError(null);
      // The user's engine choice goes to the server as it is, exactly as for a
      // parametric design; the server resolves AUTO from what each engine
      // declares it can solve (`resolve_imported_submission`) and refuses an
      // explicit engine that cannot, with the reason. The formulation is not
      // a choice here: imported geometry solves in full 3-D only, and the
      // domain is the one the ingestion record describes.
      const options: SolveOptions = { ...submission.options, solver_mode: 'full_3d', symmetry: 'auto' };
      const effectiveSubmission = { ...submission, options };
      // The CAD document names its own runs; see jobs/runNameSource.
      const designName = currentRunNameSource().name;
      const label = nextRunLabel(designName, preferencesStore.getSnapshot(), now());
      // A typed refusal is the server naming a condition; the user needs the
      // remedy. Translating here covers every imported entry point at once.
      const jobId = await submitImported(effectiveSubmission, fetch, label).catch((error) => {
        // No engine here can solve imported geometry: a capability this
        // machine lacks, which an automatic caller keeps rather than refuses.
        if (
          error instanceof SolveSubmissionRefused
          && error.code !== null
          && IMPORTED_CAPABILITY_CODES.has(error.code)
        ) {
          throw new SolveEngineUnavailableError(error.message);
        }
        throw error instanceof Error
          ? new Error(explainImportedRefusal(error.message))
          : error;
      });
      // Fusion's "Solve in WG" commands are the backend's to prepare and submit
      // (under cad-solve:<id>), so a solve from here never carries a command key.
      acceptSubmittedLabel(designName);
      compareSelection.awaitRun(jobId);
      solveAttention.bindRun(jobId);
      await jobsSocket.refresh();
      return jobId;
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [now, preferences]);

  // Capture the displayed press before any plan, frame or setup request waits.
  const solveCurrentCadImport = useCallback(async (
    clicked?: { cad: ReturnType<typeof useCadReturnStore.getState>; checkPlan: () => Promise<void> },
  ) => {
    if (submissionInFlight.current) return 'busy' as const;
    const cad = clicked?.cad ?? useCadReturnStore.getState();
    const ingestId = cad.ingestRecord?.ingest_id;
    if (!ingestId) throw new Error('Ingest a CAD return before solving.');
    const frameAtPress = useCadSolverFrameStore.getState().frames[ingestId];
    const project = cad.ingestRecord?.project?.lineage_id ?? cad.projectLineageId ?? null;
    const built = buildCadProjectSetup(cad, undefined, undefined, project ?? 'manual-solve');
    const designName = currentRunNameSource().name;
    if (hasLegacyCadSolve(ingestId)) {
      submissionInFlight.current = true;
      try {
        await jobsSocket.refresh();
        await recoverLegacyCadSolves(jobsSocket.getSnapshot().jobs, ingestId);
      } finally { submissionInFlight.current = false; }
    }
    const retained = pendingCadSolve(ingestId);
    if (retained?.recoveredJobId) {
      const recovered = jobsSocket.getSnapshot().jobs.find((job) => job.id === retained.recoveredJobId);
      releaseCadSolve(ingestId, retained, retained.recoveredJobId);
      if (recovered) completeCadSolve(recovered);
      return 'submitted' as const;
    }
    const heldAtPress = retained ? null : heldOnRequest(cad.ingestRecord);
    if (heldAtPress) return holdOnRequest(heldAtPress);
    const blocker = cadInputBlockerNow();
    if (blocker && !retained?.press) throw new Error(blocker);
    const selectWaiting = () => {
      const waiting = onScreenRequestToContinue(cadJobSummaries(jobsSocket.getSnapshot().jobs), cad.ingestRecord);
      if (waiting?.snapshot?.projectLineageId && waiting.snapshot.projectLineageId !== project) {
        throw new Error('The model on screen is filed under another project than this request names. Open that project first.');
      }
      return waiting;
    };
    if (!retained?.press) selectWaiting();
    if (!built && !retained?.press) throw new Error('The CAD solve settings are incomplete. Review the Simulation settings and try again.');
    const identity = retained ?? beginCadSolve(ingestId, designName, nextRunLabel(designName, preferencesStore.getSnapshot(), now()));
    submissionInFlight.current = true;
    try {
      setSubmitting(true);
      setActionError(null);
      if (!identity.press) {
        // Start confirmation now: a later pick cannot replace the displayed axis.
        const reading = frameReadInFlight(ingestId);
        if (reading) await reading;
        const frameBlocker = frameSolveBlocker(ingestId);
        if (frameBlocker) throw new Error(frameBlocker);
        const frameConfirmation = confirmDisplayedFrame(ingestId, fetch, frameAtPress?.status === 'ready' ? frameAtPress : undefined);
        const frameAxis = await frameConfirmation;
        if (clicked) await clicked.checkPlan();
        if (project) await putProjectSetup(built!);
        const heldNow = heldOnRequest(cad.ingestRecord);
        if (heldNow && heldNow.operation.jobId !== identity.jobId) {
          discardUnsubmittedCadSolve(ingestId);
          return holdOnRequest(heldNow);
        }
        const setup = {
          ...built!.setup, label: identity.label,
          options: { ...built!.setup.options, solver_mode: 'full_3d', symmetry: 'auto' },
        };
        let waiting = selectWaiting();
        const bound = waiting?.setupRevisionId;
        const keepBound = bound && await getSetupRevision(bound)
          .then((revision) => solveInputsKey(revision.setup) === solveInputsKey(setup), () => false);
        const revisionId = keepBound ? bound : (await createSetupRevision(setup)).revisionId;
        // A Fusion request can reach a gate during any of the above awaits.
        // Select it against the snapshot captured at this press, just before
        // persisting the target and immutable press together.
        waiting = selectWaiting();
        const heldAfterSettings = heldOnRequest(cad.ingestRecord);
        if (heldAfterSettings && heldAfterSettings.operation.jobId !== identity.jobId) {
          discardUnsubmittedCadSolve(ingestId);
          return holdOnRequest(heldAfterSettings);
        }
        identity.jobId = waiting?.jobId ?? identity.jobId;
        const waitingJob = jobsSocket.getSnapshot().jobs.find((job) => job.id === identity.jobId);
        const preparation = waitingJob?.cad_state?.preparation;
        const approved = revisionId === waiting?.setupRevisionId && preparation ? waitingJob?.cad_state?.approvals
          .filter((approval) => approval.preparation_id === preparation.preparation_id
            && preparation.blocking_finding_ids.includes(approval.finding_id))
          .map((approval) => approval.finding_id) : [];
        identity.press = {
          client_request_id: identity.requestId, ingest_id: ingestId,
          setup_revision_id: revisionId, label: identity.label, submit: true,
          ...(frameAxis ? { frame_axis: frameAxis } : {}),
          ...(approved?.length && preparation ? { approvals: { preparation_id: preparation.preparation_id, finding_ids: approved } } : {}),
        };
        retainCadSolvePress(ingestId, identity);
      }
      // The completion claim exists before HTTP: an event may beat the response.
      rememberCadSolve(`cad-solve:manual-solve:${identity.requestId}`, identity.designName, ingestId);
      if (identity.jobId) rememberCadSolve(identity.jobId, identity.designName, ingestId);
      solveAttention.bindOperation(identity.jobId ?? `cad-solve:manual-solve:${identity.requestId}`);
      const response = identity.jobId
        ? await solveCadAgain(identity.jobId, identity.press)
        : await submitCadSolve(identity.press);
      rememberCadSolve(response.job_id, identity.designName, ingestId);
      solveAttention.followJob(identity.jobId ?? `cad-solve:manual-solve:${identity.requestId}`, response.job_id);
      solveAttention.bindRun(response.job_id, identity.jobId ?? `cad-solve:manual-solve:${identity.requestId}`);
      releaseCadSolve(ingestId, identity, response.job_id);
      await jobsSocket.refresh();
      const job = jobsSocket.getSnapshot().jobs.find((candidate) => candidate.id === response.job_id);
      if (job) completeCadSolve(job);
      return 'submitted' as const;
    } catch (reason) {
      // A definitive refusal answered this press; transport failures retain it.
      if (reason instanceof CadLinkApiError && reason.status && reason.status >= 400 && reason.status < 500) {
        forgetPendingCadSolve(ingestId, identity.requestId);
      }
      throw reason;
    } finally {
      discardUnsubmittedCadSolve(ingestId);
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [completeCadSolve, now]);

  const retry = useCallback(async (jobId: string) => {
    if (submissionInFlight.current) return;
    submissionInFlight.current = true;
    try {
      setSubmitting(true);
      setActionError(null);
      await jobsSocket.retryJob(jobId);
      compareSelection.awaitRun(jobId);
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, []);

  const reportError = useCallback((message: string) => setActionError(message), []);
  useEffect(() => {
    publishBridge({ run, runImported, solveCurrentCadImport, retry, reportError, actionError });
    return () => publishBridge({
      run: unavailableRun,
      runImported: unavailableRun,
      solveCurrentCadImport: unavailableRun,
      retry: unavailableRun,
      reportError: () => undefined,
      actionError: null,
    });
  }, [actionError, reportError, retry, run, runImported, solveCurrentCadImport]);

  useEffect(() => {
    void automation.process(jobs, preferences, {
      downloadMesh: (job) => saveMeshArtifactToWorkspace(job),
      markMeshDownloaded: (job, filename) => jobsSocket.patchMetadata(job.id, { mesh_artifact_file: filename }),
      exportCompleted: async (job, formats) => runWorkspaceExportBundle({
        result: await fetchJobResults(job.id) as ResultPayload,
        ...resultExportSnapshot(job),
        jobId: job.id,
        jobStem: exportStemForJob(job),
        hasRadiationImpedanceArtifact: job.has_radiation_impedance_artifact,
        workspaceSubdirectory: exportSubdirectoryForJob(job),
        designName: job.label ?? undefined,
        normalizationAngle: useSolveOptionsStore.getState().polar.normAngle,
        preferences,
      }, formats),
      markExported: async (job, files, formats, completedAt) => jobsSocket.patchMetadata(job.id, {
        exported_files: [...new Set([...(job.exported_files ?? []), ...files])],
        auto_export_formats: formats,
        auto_export_completed_at: completedAt,
      }),
      archiveCompleted: async (job) => {
        await archiveRunToWorkspace(await refreshedArchiveJob(job), preferences);
      },
      markArchived: (job, archivedAt) => jobsSocket.patchMetadata(job.id, { archived_at: archivedAt }),
      reportError,
    });
  }, [automation, jobs, preferences, reportError]);

  const parametricUnavailable = solveOptionsError
    ?? solvePlanError
    ?? (solvePlanPending ? 'Planning solve for the current design…' : 'Solve plan is unavailable');
  const solveAvailable = cadGeometryActive
    ? importedEngine !== null
    : solvePlan !== null && !solvePlanPending && solvePlanError === null;
  const planPending = cadGeometryActive ? importedPlan.isPending : solvePlanPending;
  const planError = cadGeometryActive ? importedPlan.error : solvePlanError;
  // A press during the debounce or request belongs to the current inputs,
  // never the previous plan. Repeated presses retain just one intent.
  const [pendingSolve, setPendingSolve] = useState<'cad' | 'parametric' | null>(null);
  const solveEnabled = (solveAvailable || planPending) && !solveOptionsError
    && !planError && !submitting && !solveBlocker && !fileGeometryActive;
  const solve = useCallback(() => {
    // The button, the shortcut and the palette all arrive here: one command.
    if (submissionInFlight.current || !solveEnabled) return;
    if (planPending) {
      solveAttention.armSolve();
      setPendingSolve(cadGeometryActive ? 'cad' : 'parametric');
      return;
    }
    setPendingSolve(null);
    solveAttention.armSolve();
    const action = async () => {
      if (fileGeometryActive) {
        throw new Error('A standalone imported mesh is for viewport inspection only. Show Parametric to solve the WG design.');
      }
      if (cadGeometryActive) {
        const draftError = getCrossoverDraftError();
        if (draftError) throw new Error(draftError);
        const focused = document.activeElement;
        if (focused instanceof HTMLInputElement && focused.matches('[data-crossover-frequency]')) {
          // Commit the complete draft before capturing the click's settings.
          focused.blur();
          if (getCrossoverDraftError()) throw new Error(getCrossoverDraftError()!);
          const cad = useCadReturnStore.getState();
          const body = importedSolvePlanRequestBody(buildImportedSubmission(cad));
          await solveCurrentCadImport({ cad, checkPlan: async () => {
            const freshPlan = await postImportedSolvePlan(body);
            if (!freshPlan.engine) throw new Error(freshPlan.reason || 'No engine can solve these CAD settings.');
          } });
          return;
        }
        await solveCurrentCadImport();
        return;
      }
      const current = useDesignStore.getState();
      await run(current.design, current.designRevision);
    };
    void action().catch((error) => reportError(error instanceof Error ? error.message : String(error)));
  }, [cadGeometryActive, fileGeometryActive, planPending, reportError, run, solveCurrentCadImport, solveEnabled]);
  useEffect(() => {
    if (pendingSolve === null) return;
    if (pendingSolve !== workspaceMode || !solveEnabled) {
      setPendingSolve(null);
      if (planError) reportError(planError);
      return;
    }
    if (planPending) return;
    // Clear before invoking: settlement and further presses cannot replay it.
    setPendingSolve(null);
    solve();
  }, [pendingSolve, planError, planPending, reportError, solve, solveEnabled, workspaceMode]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && solveEnabled) {
        event.preventDefault();
        solve();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [solve, solveEnabled]);

  const notice = useMemo<SolveNotice | null>(
    () => cadGeometryActive && crossoverDraftError
      ? { tone: 'blocked', text: crossoverDraftError }
      : solveNotice({
      cadGeometryActive,
      plan: solvePlan,
      pending: solvePlanPending,
      optionsError: solveOptionsError,
      planError: solvePlanError,
    }),
    [cadGeometryActive, crossoverDraftError, solveOptionsError, solvePlan, solvePlanError, solvePlanPending],
  );
  const control = useMemo<SolveControl>(() => ({
    solve,
    cadMode: cadGeometryActive,
    disabled: !solveEnabled,
    notice,
    submitting,
    label: 'Solve',
    title: submitting
      ? 'Submitting solve…'
      : fileGeometryActive
        ? 'Standalone imported meshes are viewport-only. Show Parametric to solve the WG design.'
        : solveBlocker
          ? solveBlocker
          : cadGeometryActive && importedEngineLabel
            ? `Solve the displayed CAD Link model with ${importedEngineLabel}`
            : solvePlan
              ? solvePlanTitle(solvePlan, selectedEngine)
              : cadGeometryActive
                ? capabilityError ?? importedUnavailable ?? 'No engine can solve imported CAD geometry here'
                : parametricUnavailable,
  }), [cadGeometryActive, capabilityError, fileGeometryActive, importedUnavailable, importedEngineLabel, notice, parametricUnavailable, selectedEngine, solve, solveEnabled, solveBlocker, solvePlan, submitting]);

  useEffect(() => {
    publishSolveControl(control);
    return () => publishSolveControl(null);
  }, [control]);

  return <SolveContext.Provider value={control}>{children}<JobAnnouncer jobs={jobs}/></SolveContext.Provider>;
}

/**
 * The notice shown beside Solve: why it cannot run, or what it swapped.
 *
 * Only the parametric path produces one. The CAD path already renders the
 * chosen engine's availability text in its rail, and duplicating it here
 * would put the same sentence on screen twice.
 *
 * A pending plan is deliberately silent. Planning is debounced and re-runs on
 * every parameter keystroke, so reporting "no plan yet" would flash a warning
 * during ordinary editing.
 */
export function solveNotice(input: {
  cadGeometryActive: boolean;
  plan: SolvePlan | null;
  pending: boolean;
  optionsError: string | null;
  planError: string | null;
}): SolveNotice | null {
  if (input.cadGeometryActive) return null;
  const blocked = input.optionsError ?? input.planError;
  if (blocked) return { tone: 'blocked', text: blocked };
  if (input.pending) return null;
  const substitution = input.plan?.engine_substitution;
  if (substitution) {
    return { tone: 'substituted', text: engineSubstitutionNotice(substitution) };
  }
  return null;
}

/**
 * Names the swap, its cause, and the fact that the preference survives it.
 *
 * The last clause is the point: without it the honest reading of this message
 * is that the app has overwritten a setting, and the next thing the user does
 * is go and set it back.
 */
export function engineSubstitutionNotice(substitution: EngineSubstitution): string {
  const requested = substitution.requested.trim().toUpperCase();
  const resolved = substitution.resolved.trim().toUpperCase();
  return `${requested} is not available on this computer, so this solve will use `
    + `${resolved} instead. ${substitution.reason} Your ${requested} preference is kept `
    + `and will be used again as soon as it can run here.`;
}

export function solvePlanTitle(plan: SolvePlan, requestedEngine: string): string {
  const requested = requestedEngine.trim().toLowerCase();
  const resolved = plan.engine.trim().toLowerCase();
  if (requested === 'auto') {
    return `Solve current design with AUTO (${resolved.toUpperCase()})`;
  }
  if (plan.engine_substitution) {
    // This reports a requested backend that does not exist on this machine.
    return `${engineSubstitutionNotice(plan.engine_substitution)} Solve now.`;
  }
  if (requested !== resolved) {
    return `Solve current design with ${resolved.toUpperCase()} (requested ${requested.toUpperCase()} full-3D fallback)`;
  }
  return `Solve current design with ${resolved.toUpperCase()}`;
}

/**
 * The one thing in this application that announces itself.
 *
 * A solve takes minutes and lands asynchronously: the charts repaint, the run
 * list grows, and the selected run changes underneath whatever the user was
 * reading. Sighted users have the badge row, the `Latest` chip and the jobs
 * rail to notice that with. Before this, a screen-reader user had nothing --
 * the only aria-live regions in the document belonged to dockview.
 *
 * Polite, and only on transitions: it reports a run entering and leaving the
 * running state, never the progress percentage, which would talk over the user
 * every second for the length of the solve.
 */
export function jobAnnouncement(
  previous: ReadonlyMap<string, JobItem['status']>,
  jobs: readonly JobItem[],
): string | null {
  const changed = jobs.filter((job) => previous.has(job.id) && previous.get(job.id) !== job.status);
  const started = changed.filter((job) => job.status === 'running');
  const finished = changed.filter((job) => job.status === 'complete');
  const failed = changed.filter((job) => job.status === 'error');
  const label = (job: JobItem) => job.label || job.id.slice(0, 6);
  const parts: string[] = [];
  if (started.length) parts.push(`${started.length === 1 ? `Solve started: ${label(started[0])}` : `${started.length} solves started`}.`);
  if (finished.length) parts.push(`${finished.length === 1 ? `Solve finished: ${label(finished[0])}` : `${finished.length} solves finished`}.`);
  if (failed.length) parts.push(`${failed.length === 1 ? `Solve failed: ${label(failed[0])}` : `${failed.length} solves failed`}.`);
  return parts.length ? parts.join(' ') : null;
}

function JobAnnouncer({ jobs }: { jobs: readonly JobItem[] }) {
  const [message, setMessage] = useState('');
  const seen = useRef<Map<string, JobItem['status']> | null>(null);
  useEffect(() => {
    const current = new Map(jobs.map((job) => [job.id, job.status] as const));
    // The first snapshot is the existing history, not news. Announcing it would
    // read the whole run list aloud on load.
    if (seen.current === null) {
      seen.current = current;
      return;
    }
    const next = jobAnnouncement(seen.current, jobs);
    seen.current = current;
    if (next) setMessage(next);
  }, [jobs]);
  return <p className="sr-only" role="status" aria-live="polite">{message}</p>;
}
