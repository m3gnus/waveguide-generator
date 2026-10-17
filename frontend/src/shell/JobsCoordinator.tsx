import { importedRunRestoreBlocker, restoredCadRevision, restoredCadSubmission, useImportedRunRestoration } from '../jobs/importedRunRestoration';
import { nativeSymmetryLimitation } from '../design/nativeFamilies';
import { durableSettings } from '../stores/durableSettings';
import { fileMeshSubmission, restoreSavedMeshSetup, useMeshImportStore } from '../stores/meshImport';
import type { ExportFormat } from '../prefs/preferences';
import { ownedAutoExport } from '../jobs/autoExportOwner';
import { exportRunAutomatically } from '../jobs/autoExportRun';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { clearJobLink, jobIdFromLink, openLinkedJob } from '../jobs/jobDeepLink';
import { compareSelection } from '../api/results';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { importedSolvePlanRequestBody, planSolveDesign, postImportedSolvePlan, solveModel, SolveSubmissionRefused, submitDesign, type EngineSubstitution, type ImportedSolveSubmission, type ModelSolvePress, type SolvePlan } from '../jobs/actions';
import { setupOfSubmission } from '../jobs/importedSetup';
import {
  useCapabilities,
  useCapabilityRefreshOnReconnect,
  useLegacyBeatEngineMigration,
} from '../jobs/useCapabilities';
import { useSolvePlan } from '../jobs/useSolvePlan';
import { JobAutomation } from '../jobs/automation';
import { explainImportedRefusal } from '../jobs/importedRefusals';
import { buildImportedSubmission, importedSubmissionBlocker } from '../jobs/importedSubmission';
import { acknowledgeCadSolve, cadSolveClaim, forgetCadSolve, latestCadJobs, rememberCadSolve } from '../jobs/cadSolve';
import { useImportedSolvePlan } from '../jobs/useImportedSolvePlan';
import { advanceRunSequence, nextRunLabel } from '../jobs/runNaming';
import { currentRunNameSource } from '../jobs/runNameSource';
import { preferencesStore, usePreferences } from '../prefs/preferences';
import { archiveRunToWorkspace, saveMeshArtifactToWorkspace } from '../results/exporters';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useCadReturnStore } from '../stores/cadReturn';
import { useCadRecoveryNotices } from '../stores/cadRecoveryNotices';
import { frameSolveBlocker, frameReadInFlight, useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { polarValidationError, useSolveOptionsStore, type SolveOptions } from '../stores/solveOptions';
import { solveOptionsStoreForJob, useScopedSolveOptions } from '../stores/solveOptionsContext';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { buildCadProjectSetup } from './cadSetupPublisher';
import { getCrossoverDraftError, useCrossoverDraftError } from '../design/crossoverDrafts';
import { solveAttention, useJobAttention, useRecoveryNoticeAttention } from './solveAttention';
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
 * that named an engine, which is a verdict on that request. */
const IMPORTED_CAPABILITY_CODES: ReadonlySet<string> = new Set(['engine_unavailable']);

interface CoordinatorBridgeSnapshot {
  run(design: DesignDocument, designRevision?: number): Promise<void>;
  /** The single CAD solve entry point: the displayed axis and reviewed findings,
   * readiness, one `POST /api/models/{id}/solve`, the submission mutex, naming
   * and list refresh. Throws the blocking reason; returns 'busy' when a solve
   * is already in flight so an automatic caller can say so instead of
   * silently doing nothing. */
  solveCurrentCadImport(): Promise<'submitted' | 'busy'>;
  retry(jobId: string): Promise<void>;
  /** Run one submission under the same mutex as Solve. Resolves null, without
   * calling `submit`, when another submission is in flight. */
  runExclusive<T>(submit: () => Promise<T>): Promise<T | null>;
  reportError(message: string): void;
  actionError: string | null;
}

const unavailableRun = async () => { throw new Error('Solve coordinator is unavailable'); };
let bridgeSnapshot: CoordinatorBridgeSnapshot = {
  run: unavailableRun,
  solveCurrentCadImport: unavailableRun,
  retry: unavailableRun,
  runExclusive: unavailableRun,
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
  const restoring = importedRunRestoreBlocker('cad');
  if (restoring) return restoring;
  const cadReturn = useCadReturnStore.getState();
  const cad = importedMeshStore.getSnapshot().cad;
  if (cadReturn.ingestRecord !== null && cad !== null
    && (!cad.ingestId || cad.ingestId !== cadReturn.ingestRecord.ingest_id)) {
    return CAD_VIEWPORT_MISMATCH;
  }
  return importedSubmissionBlocker(cadReturn);
}

/** The model a CAD preparation is filed under, as a Solve names it: the reserved
 * `unfiled` for one the server files under none (server/jobs/model_solve.py). */
function cadModelId(record: CadReturnIngestRecord): string {
  if (record.model_id !== undefined) return record.model_id || 'unfiled';
  return record.project?.lineage_id || 'unfiled';
}

/** The blocking findings already reviewed on the refused solve of this
 * snapshot that a Solve continues, so the press carries them. */
function reviewedFindings(record: CadReturnIngestRecord): ModelSolvePress['approvals'] {
  const refused = latestCadJobs(jobsSocket.getSnapshot().jobs).find((job) => job.cad_state?.state === 'needs_user_input'
    && job.cad_state.snapshot?.manifest_sha256 === record.manifest_sha256);
  const preparation = refused?.cad_state?.preparation;
  if (!refused?.cad_state || !preparation) return undefined;
  const findingIds = refused.cad_state.approvals
    .filter((approval) => approval.preparation_id === preparation.preparation_id
      && preparation.blocking_finding_ids.includes(approval.finding_id))
    .map((approval) => approval.finding_id);
  return findingIds.length ? { preparation_id: preparation.preparation_id, finding_ids: findingIds } : undefined;
}

/** Refusals of a restored run's own saved inputs: the run cannot be solved again as it was.
 * An unedited restored run resends the same inputs, so Solve alone would only repeat the refusal. */
const RESTORED_INPUT_HINTS: Readonly<Record<string, string>> = {
  setup_required: 'This run’s saved setup is no longer available. Change a setting, or open the model again, then press Solve.',
  prepared_unavailable: 'This run’s prepared mesh is no longer available. Import or prepare the model again, then press Solve.',
  model_mismatch: 'This run belongs to another model. Open that model, or import this one again, then press Solve.',
};

/** A refused Solve, in the words the user acts on; "no engine here" stays its own kind.
 * The envelope's code stays on the error (`code`), never at the front of its words. */
function importedSolveError(error: unknown, restored = false): unknown {
  if (error instanceof SolveSubmissionRefused && error.code !== null) {
    const prefix = `${error.code}: `;
    const words = error.message.startsWith(prefix) ? error.message.slice(prefix.length) : error.message;
    if (IMPORTED_CAPABILITY_CODES.has(error.code)) return new SolveEngineUnavailableError(words);
    if (restored && RESTORED_INPUT_HINTS[error.code]) {
      return new SolveSubmissionRefused(RESTORED_INPUT_HINTS[error.code], error.status, error.code);
    }
    const explained = explainImportedRefusal(`${prefix}${words}`);
    return new SolveSubmissionRefused(explained === `${prefix}${words}` ? words : explained, error.status, error.code);
  }
  return error instanceof Error ? new Error(explainImportedRefusal(error.message)) : error;
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
  const jobsSnapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  const jobs = jobsSnapshot.jobs;
  const jobsLoaded = jobsSnapshot.cursor !== null;
  // `?job=<id>` is the stable link to one run (CLI, Fusion, bookmarks). Read
  // once at start-up; handled as soon as the list has loaded, then dropped
  // from the address bar so a reload does not re-trigger it.
  const linkedJobId = useRef<string | null | undefined>(undefined);
  if (linkedJobId.current === undefined) linkedJobId.current = jobIdFromLink(window.location.search);
  // This component owns the jobs socket, so it is where a reconnect is visible.
  useCapabilityRefreshOnReconnect(useSyncExternalStore(jobsSocket.subscribe, jobsConnection, jobsConnection));
  useLegacyBeatEngineMigration();
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const fileRecovery = useRef({ done: false, inFlight: false, revision, generation: importedMeshStore.captureGeneration() });
  const designName = useDocumentStore((state) => state.designName);
  // Parametric mode reads the persisted store, Imported mode its own instance.
  const solveOptions = useScopedSolveOptions();
  const selectedEngine = solveOptions.engine;
  useImportedRunRestoration();
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
  const recoveryNotices = useCadRecoveryNotices((state) => state.notices);
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
  useRecoveryNoticeAttention(recoveryNotices);

  let currentOptions: SolveOptions | null = null;
  let solveOptionsError: string | null = null;
  try {
    currentOptions = solveOptions.options();
    if (workspaceMode === 'parametric') {
      solveOptionsError = nativeSymmetryLimitation(design.formula, currentOptions.symmetry);
      if (solveOptionsError) currentOptions = null;
    }
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
  // Mode owns solve intent. The viewport CAD slot is evidence for the mismatch
  // guard only; an empty slot must still route to the imported blocker so CAD
  // mode can truthfully say that an ingest is required.
  const fileGeometryActive = workspaceMode === 'cad' && workspaceModeStore.getSnapshot().importedInput === 'file';
  const cadGeometryActive = workspaceMode === 'cad' && !fileGeometryActive;
  const crossoverDraftError = useCrossoverDraftError();
  useMeshImportStore();
  let fileSubmission: ImportedSolveSubmission | null = null;
  let fileBlocker: string | null = null;
  if (fileGeometryActive) {
    try { fileSubmission = fileMeshSubmission(); }
    catch (error) { fileBlocker = error instanceof Error ? error.message : String(error); }
  }
  const cadViewportGeometry = viewportGeometry.cad;
  const cadGeometryMismatch = cadGeometryActive && cadReturn.ingestRecord !== null && cadViewportGeometry !== null && (
    !cadViewportGeometry.ingestId
    || cadViewportGeometry.ingestId !== cadReturn.ingestRecord?.ingest_id
  );
  const cadSolveBlocker = cadGeometryMismatch
    ? CAD_VIEWPORT_MISMATCH
    : cadGeometryActive
      ? importedSubmissionBlocker(cadReturn, solveOptions) ?? frameSolveBlocker(cadReturn.ingestRecord?.ingest_id)
      : null;
  const directivityError = polarValidationError(solveOptions.polar);
  const solveBlocker = fileGeometryActive ? fileBlocker : (cadGeometryActive ? crossoverDraftError : null) ?? cadSolveBlocker ?? directivityError;
  useEffect(() => {
    if (crossoverDraftError) setActionError(crossoverDraftError);
    else setActionError((current) => current?.includes('frequency must be greater than 0 Hz') ? null : current);
  }, [crossoverDraftError]);
  // Imported geometry takes the same engine choice as a parametric design. The
  // server says, per engine, which can take this return and where the user's
  // choice resolves; Solve follows that one answer.
  const importedPlan = useImportedSolvePlan(
    (cadGeometryActive && cadSolveBlocker === null) || (fileGeometryActive && fileSubmission !== null),
    fileGeometryActive ? fileSubmission : undefined,
  );
  const importedEngine = importedPlan.plan?.engine ?? null;
  const importedEngineLabel = importedEngine
    ? importedPlan.plan?.engines.find((verdict) => verdict.name === importedEngine)?.label || importedEngine
    : null;
  const importedUnavailable = importedPlan.isPending
    ? 'Checking which engines can solve this imported mesh…'
    : importedPlan.plan?.reason ?? importedPlan.error;

  const run = useCallback(async (nextDesign: DesignDocument, nextRevision = revision) => {
    if (submissionInFlight.current) return;
    submissionInFlight.current = true;
    try {
      setSubmitting(true);
      setActionError(null);
      // Pressing Solve never edits the design. BEMPP's own closed-wall default
      // for a bare free-standing horn is applied by the server, on the copy it
      // stores as the run's design and snapshot (server/jobs/runtime/core.py's
      // `_apply_bempp_wall_default`). Doing it here as well used to rewrite the
      // live document: choosing "Bare shell" and pressing Solve flipped the
      // Outer body control back to "Thickened waveguide (freestanding)",
      // discarded any expression bound to the field, bumped the revision, and
      // marked the file unsaved -- for a correction the run had already made.
      const options = useSolveOptionsStore.getState().options();
      const symmetryError = nativeSymmetryLimitation(nextDesign.formula, options.symmetry);
      if (symmetryError) throw new Error(symmetryError);
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

  // One press of an imported model's Solve: one POST /api/models/{id}/solve.
  // The server owns what follows -- a job already in flight for the same
  // setup, the CAD preparation lane, the frame and findings gates.
  const solveFileModel = useCallback(async () => {
    if (submissionInFlight.current) return 'busy' as const;
    const submission = fileMeshSubmission();
    const state = useMeshImportStore.getState();
    const record = state.registered!;
    const designName = state.filename;
    const label = nextRunLabel(designName, preferencesStore.getSnapshot(), now());
    // An unedited restored run is solved again with the setup it recorded.
    const revision = state.exactSubmission ? state.exactRevisionId : null;
    const press: ModelSolvePress = {
      prepared_id: record.ingest_id, client_request_id: `mesh-${crypto.randomUUID()}`, label,
      ...(revision ? { setup_revision_id: revision } : { setup: setupOfSubmission(submission) }),
    };
    submissionInFlight.current = true;
    try {
      setSubmitting(true);
      setActionError(null);
      const jobId = await solveModel(record.model_id, press)
        .catch((error) => { throw importedSolveError(error, state.exactSubmission !== null); });
      acceptSubmittedLabel(designName);
      compareSelection.awaitRun(jobId);
      solveAttention.bindRun(jobId);
      await jobsSocket.refresh();
      return 'submitted' as const;
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [now, preferences]);

  // The CAD press captures the axis shown and the findings reviewed, then
  // makes one call; the server prepares, continues or answers the job in flight.
  const solveCurrentCadImport = useCallback(async (
    clicked?: { cad: ReturnType<typeof useCadReturnStore.getState>; checkPlan: () => Promise<void> },
  ) => {
    if (submissionInFlight.current) return 'busy' as const;
    const cad = clicked?.cad ?? useCadReturnStore.getState();
    const record = cad.ingestRecord;
    if (!record) throw new Error('Ingest a CAD return before solving.');
    const ingestId = record.ingest_id;
    const frameAtPress = useCadSolverFrameStore.getState().frames[ingestId];
    const blocker = cadInputBlockerNow();
    if (blocker) throw new Error(blocker);
    // The settings on screen, or an unedited restored run's own revision.
    const frozen = restoredCadSubmission(ingestId);
    const revision = frozen ? restoredCadRevision(ingestId) : null;
    const built = frozen ? null : buildCadProjectSetup(cad, undefined, undefined, cadModelId(record));
    if (!frozen && !built) throw new Error('The CAD solve settings are incomplete. Review the Simulation settings and try again.');
    const setup = revision ? null : frozen ? setupOfSubmission(frozen) : built!.setup;
    const designName = currentRunNameSource().name;
    const label = nextRunLabel(designName, preferencesStore.getSnapshot(), now());
    const clientRequestId = `cad-${crypto.randomUUID()}`;
    const key = `cad-solve:manual-solve:${clientRequestId}`;
    submissionInFlight.current = true;
    try {
      setSubmitting(true);
      setActionError(null);
      // The axis this card shows -- a read under way answers first; a later
      // pick cannot replace it. The server confirms it as part of the press.
      const reading = frameReadInFlight(ingestId);
      if (reading) await reading;
      const view = frameAtPress?.status === 'ready' ? frameAtPress : useCadSolverFrameStore.getState().frames[ingestId];
      const frameAxis = view && !view.linked && view.status === 'ready' ? view.axis : null;
      if (clicked) await clicked.checkPlan();
      const approvals = reviewedFindings(record);
      const press: ModelSolvePress = {
        prepared_id: ingestId, client_request_id: clientRequestId, label,
        ...(revision ? { setup_revision_id: revision } : { setup: setup! }),
        ...(frameAxis ? { frame_axis: frameAxis } : {}),
        ...(approvals ? { approvals } : {}),
      };
      // The completion claim exists before HTTP: an event may beat the response.
      rememberCadSolve(key, designName, ingestId);
      solveAttention.bindOperation(key);
      const jobId = await solveModel(cadModelId(record), press).catch((error) => {
        if (error instanceof SolveSubmissionRefused) {
          // Refused: no job exists for this press, so nothing may claim it.
          forgetCadSolve(key);
          solveAttention.releaseOperation(key);
        }
        throw importedSolveError(error, frozen !== null);
      });
      rememberCadSolve(jobId, designName, ingestId);
      solveAttention.followJob(key, jobId);
      solveAttention.bindRun(jobId, key);
      if (frameAxis && view?.frame?.confirmed?.axis !== frameAxis) void useCadSolverFrameStore.getState().load(ingestId);
      await jobsSocket.refresh();
      const job = jobsSocket.getSnapshot().jobs.find((candidate) => candidate.id === jobId);
      if (job) completeCadSolve(job);
      return 'submitted' as const;
    } finally {
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

  const runExclusive = useCallback(async <T,>(submit: () => Promise<T>): Promise<T | null> => {
    if (submissionInFlight.current) return null;
    submissionInFlight.current = true;
    try {
      setSubmitting(true);
      setActionError(null);
      return await submit();
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, []);

  const reportError = useCallback((message: string) => setActionError(message), []);
  useEffect(() => {
    const recovery = fileRecovery.current;
    if (!jobsLoaded) return;
    const recover = () => {
      if (recovery.done || recovery.inFlight || (workspaceMode === 'cad' && workspaceModeStore.getSnapshot().importedInput !== 'file') || linkedJobId.current || revision !== recovery.revision
        || !importedMeshStore.isCurrentGeneration(recovery.generation)
        || importedMeshStore.getSnapshot().showing !== 'parametric') return;
      recovery.inFlight = true;
      const generation = importedMeshStore.captureGeneration();
      void Promise.resolve(importedMeshStore.isCurrentGeneration(generation) && restoreSavedMeshSetup())
        .then(restored => { recovery.done = Boolean(restored); })
        .catch(error => { recovery.done = true; reportError(error instanceof Error ? error.message : String(error)); })
        .finally(() => { recovery.inFlight = false; });
    };
    recover();
    // A new origin may mount before the settings request finishes. Its later
    // saved pointer can resume only while the original empty view still owns intent.
    return durableSettings.subscribe('meshImport', recover);
  }, [jobsLoaded, reportError, revision, workspaceMode]);
  // An empty `?job=` names no run; drop it at once.
  useEffect(() => {
    if (linkedJobId.current === null) clearJobLink();
  }, []);
  useEffect(() => {
    const id = linkedJobId.current;
    if (!id || !jobsLoaded) return;
    linkedJobId.current = null;
    openLinkedJob(id, jobs, reportError);
    clearJobLink();
  }, [jobs, jobsLoaded, reportError]);
  useEffect(() => {
    publishBridge({ run, solveCurrentCadImport, retry, runExclusive, reportError, actionError });
    return () => publishBridge({
      run: unavailableRun,
      solveCurrentCadImport: unavailableRun,
      retry: unavailableRun,
      runExclusive: unavailableRun,
      reportError: () => undefined,
      actionError: null,
    });
  }, [actionError, reportError, retry, run, runExclusive, solveCurrentCadImport]);

  useEffect(() => {
    const retryExport = (event: Event) => automation.retryExport((event as CustomEvent<string>).detail);
    window.addEventListener('wg-retry-auto-export', retryExport);
    return () => window.removeEventListener('wg-retry-auto-export', retryExport);
  }, [automation]);
  useEffect(() => {
    void automation.process(jobs, preferences, {
      downloadMesh: (job) => saveMeshArtifactToWorkspace(job),
      markMeshDownloaded: (job, filename) => jobsSocket.patchMetadata(job.id, { mesh_artifact_file: filename }),
      exportCompleted: (job, formats) => ownedAutoExport(job, formats, (job, formats, fetcher) => exportRunAutomatically(job, formats, {
        fetcher,
        normalizationAngle: solveOptionsStoreForJob(job).getState().polar.normAngle,
        preferences,
      }), preferences.autoExportFormats),
      markExported: async (job, files, formats, completedAt) => jobsSocket.patchMetadata(job.id, {
        exported_files: [...new Set([...(job.exported_files ?? []), ...files])],
        auto_export_formats: formats,
        auto_export_completed_at: completedAt,
      }),
      archiveCompleted: async (job) => {
        // Archiving is another automatic bundle and shares the backend lane.
        await ownedAutoExport(job, ['run_archive' as ExportFormat], async (freshJob, _formats, fetcher) => ({
          files: await archiveRunToWorkspace(freshJob, preferences, fetcher), failures: [],
        }));
      },
      markArchived: async () => undefined,
      reportError,
    });
  }, [automation, jobs, preferences, reportError]);

  const parametricUnavailable = solveOptionsError
    ?? solvePlanError
    ?? (solvePlanPending ? 'Planning solve for the current design…' : 'Solve plan is unavailable');
  const solveAvailable = cadGeometryActive || fileGeometryActive
    ? importedEngine !== null
    : solvePlan !== null && !solvePlanPending && solvePlanError === null;
  const planPending = cadGeometryActive || fileGeometryActive ? importedPlan.isPending : solvePlanPending;
  const planError = cadGeometryActive || fileGeometryActive ? importedPlan.error ?? (importedPlan.plan && !importedPlan.plan.engine ? importedPlan.plan.reason : null) : solvePlanError;
  // A press during the debounce or request belongs to the current inputs,
  // never the previous plan. Repeated presses retain just one intent.
  const [pendingSolve, setPendingSolve] = useState<'cad' | 'parametric' | 'file' | null>(null);
  const solveEnabled = (solveAvailable || planPending) && (fileGeometryActive || !solveOptionsError)
    && !planError && !submitting && !solveBlocker;
  const solve = useCallback(() => {
    // The button, the shortcut and the palette all arrive here: one command.
    if (submissionInFlight.current || !solveEnabled) return;
    if (planPending) {
      solveAttention.armSolve();
      setPendingSolve(fileGeometryActive ? 'file' : cadGeometryActive ? 'cad' : 'parametric');
      return;
    }
    setPendingSolve(null);
    solveAttention.armSolve();
    const action = async () => {
      if (fileGeometryActive) {
        await solveFileModel();
        return;
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
  }, [cadGeometryActive, fileGeometryActive, planPending, reportError, run, solveCurrentCadImport, solveEnabled, solveFileModel]);
  useEffect(() => {
    if (pendingSolve === null) return;
    if ((pendingSolve === 'file' ? !fileGeometryActive : pendingSolve === 'cad' ? !cadGeometryActive : pendingSolve !== workspaceMode) || !solveEnabled) {
      setPendingSolve(null);
      if (planError) reportError(planError);
      return;
    }
    if (planPending) return;
    // Clear before invoking: settlement and further presses cannot replay it.
    setPendingSolve(null);
    solve();
  }, [cadGeometryActive, fileGeometryActive, pendingSolve, planError, planPending, reportError, solve, solveEnabled, workspaceMode]);
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
    () => fileGeometryActive ? null : cadGeometryActive && crossoverDraftError
      ? { tone: 'blocked', text: crossoverDraftError }
      : solveNotice({
      cadGeometryActive,
      plan: solvePlan,
      pending: solvePlanPending,
      optionsError: solveOptionsError,
      planError: solvePlanError,
    }),
    [cadGeometryActive, fileGeometryActive, crossoverDraftError, solveOptionsError, solvePlan, solvePlanError, solvePlanPending],
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
        ? fileBlocker ?? planError ?? (planPending ? 'Checking the imported mesh…' : 'Solve the prepared imported mesh')
        : solveBlocker
          ? solveBlocker
          : cadGeometryActive && importedEngineLabel
            ? `Solve the displayed CAD Link model with ${importedEngineLabel}`
            : solvePlan
              ? solvePlanTitle(solvePlan, selectedEngine)
              : cadGeometryActive
                ? capabilityError ?? importedUnavailable ?? 'No engine can solve imported CAD geometry here'
                : parametricUnavailable,
  }), [cadGeometryActive, capabilityError, fileBlocker, fileGeometryActive, importedUnavailable, importedEngineLabel, notice, parametricUnavailable, planError, planPending, selectedEngine, solve, solveEnabled, solveBlocker, solvePlan, submitting]);

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
