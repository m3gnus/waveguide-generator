import { useRequestedSolve, awaitSolveStep, solveCancellationReason, type HeldSolveContinuation, type ModelSolveContinuation } from '../jobs/useRequestedSolve';
import { usePrepareEngine } from '../jobs/usePrepareEngine';
import { heldEngineSolveJob, useHeldSolvePreparation } from '../jobs/useHeldSolvePreparation';
import { messageBlocker, type SolveReadinessState } from '../jobs/solveReadiness';
import { currentModel, useCurrentModel } from '../model/currentModel';
import { importedModelBlocker, importedModelId, unchangedSetup, useImportedModelStore } from '../stores/importedModel';
import { nativeSymmetryLimitation } from '../design/nativeFamilies';
import { fileMeshSetup, fileMeshSubmission, registeredMesh, useMeshImportStore } from '../stores/meshImport';
import type { ExportFormat } from '../prefs/preferences';
import { ownedAutoExport } from '../jobs/autoExportOwner';
import { exportRunAutomatically } from '../jobs/autoExportRun';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { clearJobLink, jobIdFromLink, openLinkedJob } from '../jobs/jobDeepLink';
import { compareSelection } from '../api/results';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { importedSolvePlanRequestBody, prewarmSelectedEngine, planSolveDesign, postImportedSolvePlan, solveModel, SolveSubmissionRefused, submitDesign, type EngineSubstitution, type ImportedSolveSubmission, type ModelSolvePress, type SolvePlan } from '../jobs/actions';
import { rerunWithPatch, type RerunPatch } from '../jobs/rerun';
import {
  useCapabilities,
  useCapabilityRefreshOnReconnect,
  useLegacyBeatEngineMigration,
} from '../jobs/useCapabilities';
import { useSolvePlan } from '../jobs/useSolvePlan';
import { JobAutomation } from '../jobs/automation';
import { explainImportedRefusal } from '../jobs/importedRefusals';
import { buildImportedSubmission, importedSubmissionBlocker } from '../jobs/importedSubmission';
import { acknowledgeCadSolve, approveCadJob, solveCadAgain, type CadSolvePress, cadSolveClaim, forgetCadSolve, latestCadJobs, rememberCadSolve } from '../jobs/cadSolve';
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
import { useScopedSolveOptions } from '../stores/solveOptionsContext';
import { workspaceModeStore } from '../stores/workspaceMode';
import { buildCadProjectSetup } from './cadSetupPublisher';
import { getCrossoverDraftError, useCrossoverDraftError } from '../design/crossoverDrafts';
import { solveAttention, useJobAttention, useRecoveryNoticeAttention } from './solveAttention';
import { navigationGeneration, workspaceNavigation } from './workspaceNavigation';
import { solvedCadModels } from './cadlink/solvedModel';

/** Reveal an action failure only while the user's click still owns attention.
 * Editing, a drag, or later navigation suppresses this reveal permanently. */
function useActionErrorReveal(): (actionNavigation?: number) => void {
  const pointers = useRef(new Set<number>());

  useEffect(() => {
    const down = (event: PointerEvent) => { pointers.current.add(event.pointerId); };
    const up = (event: PointerEvent) => { pointers.current.delete(event.pointerId); };
    const blur = () => { pointers.current.clear(); };
    window.addEventListener('pointerdown', down, true);
    window.addEventListener('pointerup', up, true);
    window.addEventListener('pointercancel', up, true);
    window.addEventListener('blur', blur);
    return () => {
      pointers.current.clear();
      window.removeEventListener('pointerdown', down, true);
      window.removeEventListener('pointerup', up, true);
      window.removeEventListener('pointercancel', up, true);
      window.removeEventListener('blur', blur);
    };
  }, []);

  return useCallback((actionNavigation?: number) => {
    // An unarmed report (automation, polling, model loading) only stores an error.
    if (actionNavigation === undefined || actionNavigation !== navigationGeneration()) return;
    const focused = document.activeElement;
    if (pointers.current.size || focused?.closest(
      'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]',
    )) return;
    workspaceNavigation.activate('results');
  }, []);
}

interface SolveControl {
  /** The coordinator's current parametric plan, also shared with dock panels. */
  plan: SolvePlan | null;
  planPending: boolean;
  solve(): Promise<'submitted' | 'busy' | 'blocked' | 'pending'>;
  /** CAD Link mode: Solve solves the CAD model on screen. */
  cadMode: boolean;
  disabled: boolean;
  submitting: boolean;
  label: string;
  title: string;
  readiness: SolveReadinessState | null;
  prepareEngine(engine: string): Promise<boolean>;
  prepareAndSolve(engine: string): Promise<void>;
  useEngine(engine: string): void;
  preparationError: string | null;
  actionError: string | null;
  preparingEngine: string | null;
  readinessRequested: boolean;
  waitingForEngine: boolean;
  waitingEngine: string | null;
  stopWaiting(): void;
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

interface SubmissionAcceptanceOptions {
  /** Request-specific naming or selection, after HTTP acceptance, before awaitRun. */
  onAccepted?: (jobId: string) => void;
  busyMessage?: string;
  errorNavigation?: number;
}

export type ContinueRunPatch = RerunPatch & Pick<CadSolvePress, 'approvals' | 'mesh_limit_override'> & { held?: HeldSolveContinuation };

interface CoordinatorBridgeSnapshot {
  available: boolean;
  /** Continue a specific run, under the same lock and attention as Solve. */
  continueRun(jobId: string, patch?: ContinueRunPatch): Promise<string | null>;
  /** Only a user action captures navigation before awaiting its request. */
  reportError(message: string, actionNavigation?: number): void;
  actionError: string | null;
}

const unavailableRun = async () => { throw new Error('Solve coordinator is unavailable'); };
let bridgeSnapshot: CoordinatorBridgeSnapshot = {
  available: false,
  continueRun: unavailableRun,
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
export const solveControlStore = {
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
    solveControlStore.subscribe,
    solveControlStore.getSnapshot,
    solveControlStore.getSnapshot,
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
  const opening = importedModelBlocker('cad');
  if (opening) return opening;
  const cadReturn = useCadReturnStore.getState();
  const model = currentModel();
  if (cadReturn.ingestRecord !== null && !model.viewportShowing) {
    return CAD_VIEWPORT_MISMATCH;
  }
  return importedSubmissionBlocker(cadReturn);
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
  // Parametric mode reads the persisted store, Imported mode its own instance.
  const solveOptions = useScopedSolveOptions();
  const selectedEngine = solveOptions.engine;
  useImportedModelStore();
  const cadReturn = useCadReturnStore();
  const model = useCurrentModel();
  const workspaceMode = useSyncExternalStore(
    workspaceModeStore.subscribe,
    workspaceModeStore.getSnapshot,
    workspaceModeStore.getSnapshot,
  ).mode;
  const preferences = usePreferences();
  const automation = useRef(new JobAutomation()).current;
  const {
    error: capabilityError, engines, capabilityGeneration, capabilityIssueSequence, refreshCapabilities,
  } = useCapabilities();
  useEffect(() => {
    void prewarmSelectedEngine(selectedEngine).catch(() => undefined).finally(() => refreshCapabilities());
  }, [selectedEngine, refreshCapabilities]);
  const preparation = usePrepareEngine(engines, refreshCapabilities);
  const [actionError, setActionError] = useState<string | null>(null);
  const revealActionError = useActionErrorReveal();
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
    isPending: solvePlanPending, readiness: parametricReadiness,
  } = useSolvePlan(
    design,
    currentOptions,
    workspaceMode === 'parametric',
  );
  // Mode owns solve intent. The viewport CAD slot is evidence for the mismatch
  // guard only; an empty slot must still route to the imported blocker so CAD
  // mode can truthfully say that an ingest is required.
  const fileGeometryActive = model.kind === 'file';
  const cadGeometryActive = model.kind === 'cad';
  const crossoverDraftError = useCrossoverDraftError();
  useMeshImportStore();
  let fileSubmission: ImportedSolveSubmission | null = null;
  let fileBlocker: string | null = null;
  if (fileGeometryActive) {
    try { fileSubmission = fileMeshSubmission(); }
    catch (error) { fileBlocker = error instanceof Error ? error.message : String(error); }
  }
  const cadGeometryMismatch = cadGeometryActive && model.key !== null && !model.viewportShowing;
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

  const reportError = useCallback((message: string, actionNavigation?: number) => {
    setActionError(message);
    revealActionError(actionNavigation);
  }, [revealActionError]);

  const acceptSubmission = useCallback(async (
    submit: () => Promise<string | null>, opts: SubmissionAcceptanceOptions = {},
  ): Promise<string | null> => {
    if (submissionInFlight.current) {
      if (opts.busyMessage) reportError(opts.busyMessage, opts.errorNavigation);
      return null;
    }
    submissionInFlight.current = true;
    setSubmitting(true);
    setActionError(null);
    solveAttention.armSolve();
    try {
      const jobId = await submit();
      if (jobId === null) { solveAttention.releaseSolve(); return null; }
      opts.onAccepted?.(jobId);
      compareSelection.awaitRun(jobId);
      solveAttention.bindRun(jobId);
      await jobsSocket.refresh();
      return jobId;
    } catch (error) {
      solveAttention.releaseSolve();
      if (!(error instanceof DOMException && error.name === 'AbortError')) reportError(error instanceof Error ? error.message : String(error), opts.errorNavigation);
      throw error;
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [reportError]);

  const solveAvailable = cadGeometryActive || fileGeometryActive
    ? importedEngine !== null
    : solvePlan !== null && !solvePlanPending && solvePlanError === null;
  const planPending = cadGeometryActive || fileGeometryActive ? importedPlan.isPending : solvePlanPending;
  const planError = cadGeometryActive || fileGeometryActive ? importedPlan.error ?? (importedPlan.plan && !importedPlan.plan.engine ? importedPlan.plan.reason : null) : solvePlanError;
  // A press during the debounce or request belongs to the current inputs,
  // never the previous plan. Repeated presses retain just one intent.
  const serverReadiness = cadGeometryActive || fileGeometryActive ? importedPlan.plan?.readiness : parametricReadiness;
  const engineBlocked = !!serverReadiness?.engine;
  const warming = engineBlocked && serverReadiness?.remedies?.some(remedy => remedy.kind === 'wait');
  const solveEnabled = (solveAvailable || planPending || engineBlocked) && (fileGeometryActive || !solveOptionsError)
    && (!planError || engineBlocked || planPending) && !submitting && !solveBlocker;
  // The job retains its warming intention even if capabilities finish and the
  // plan cache refreshes before the held Fusion request reaches this browser.
  const heldJob = heldEngineSolveJob();
  const heldReadiness = heldJob?.cad_state?.blocker;
  const heldWarmingEngine = heldReadiness?.remedies?.some(remedy => remedy.kind === 'wait') ? heldReadiness.engine
    : !planPending && warming && (heldReadiness?.engine ?? heldJob?.solve_options?.engine) === serverReadiness?.engine
      ? serverReadiness?.engine ?? null : null;
  const requested = useRequestedSolve(refreshCapabilities, setActionError, solveAttention.releaseSolve, {
    capabilityGeneration, capabilityIssueSequence, enabled: solveEnabled, busy: submitting, pending: planPending, blocked: engineBlocked,
    warmingEngine: warming ? serverReadiness?.engine ?? selectedEngine : null,
    error: planError, engines, heldFusion: heldJob && heldWarmingEngine ? { jobId: heldJob.id, engine: heldWarmingEngine } : null, prepare: preparation.prepare,
    resume: token => token.jobId ? continueRun(token.jobId, { held: token as HeldSolveContinuation }) : performRequestedSolve(token),
  });
  const { beforePost } = requested;

  const run = useCallback(async (nextDesign: DesignDocument, nextRevision = revision, continuation: ModelSolveContinuation | null = null) => {
    let designName = '';
    await acceptSubmission(async () => {
      // Solve leaves the live document alone. The server applies BEMPP's
      // closed-wall default to its own stored request, not the editor's design.
      const options = useSolveOptionsStore.getState().options();
      const symmetryError = nativeSymmetryLimitation(nextDesign.formula, options.symmetry);
      if (symmetryError) throw new Error(symmetryError);
      const document = useDocumentStore.getState();
      await awaitSolveStep(planSolveDesign(nextDesign, options), continuation);
      designName = document.designName;
      const label = nextRunLabel(designName, preferencesStore.getSnapshot(), now());
      if (!beforePost(continuation)) return null;
      return submitDesign(nextDesign, options, fetch, { label, designRevision: nextRevision, designUid: document.designUid, designId: document.identity?.designId, designName, legacyDesignName: document.legacyDesignName });
    }, { onAccepted: () => acceptSubmittedLabel(designName) });
  }, [acceptSubmission, now, revision]);

  // One press of a file model's Solve: one POST /api/models/{id}/solve.
  const solveFileModel = useCallback(async (continuation: ModelSolveContinuation | null = null) => {
    let designName = '';
    const jobId = await acceptSubmission(async () => {
      const setup = fileMeshSetup();
      const record = registeredMesh()!;
      designName = useMeshImportStore.getState().filename;
      const label = nextRunLabel(designName, preferencesStore.getSnapshot(), now());
      // An unedited reopened model keeps the setup it was opened with.
      const unchanged = unchangedSetup('file', record.ingest_id);
      const press: ModelSolvePress = {
        prepared_id: record.ingest_id, client_request_id: `mesh-${crypto.randomUUID()}`, label,
        ...(unchanged ?? { setup: { schema_version: 1, ...setup } as unknown as ModelSolvePress['setup'] }),
      };
      if (!beforePost(continuation)) return null;
      return solveModel(record.model_id, press)
        .catch((error) => { throw importedSolveError(error, unchanged !== null); });
    }, { onAccepted: () => acceptSubmittedLabel(designName) });
    return jobId === null ? 'busy' as const : 'submitted' as const;
  }, [acceptSubmission, now]);

  // The CAD press captures the axis shown and the findings reviewed, then
  // makes one call; the server prepares, continues or answers the job in flight.
  const solveCurrentCadImport = useCallback(async (
    clicked?: { cad: ReturnType<typeof useCadReturnStore.getState>; checkPlan: () => Promise<void> },
    continuation: ModelSolveContinuation | null = null,
  ) => {
    if (submissionInFlight.current) return 'busy' as const;
    const cad = clicked?.cad ?? useCadReturnStore.getState();
    const record = cad.ingestRecord;
    if (!record) throw new Error('Ingest a CAD return before solving.');
    const ingestId = record.ingest_id;
    const frameAtPress = useCadSolverFrameStore.getState().frames[ingestId];
    const blocker = cadInputBlockerNow();
    if (blocker) throw new Error(blocker);
    // The settings on screen, or the setup an unedited reopened run was opened with.
    const unchanged = unchangedSetup('cad', ingestId);
    const built = unchanged ? null : buildCadProjectSetup(cad, undefined, importedModelId(record));
    if (!unchanged && !built) throw new Error('The CAD solve settings are incomplete. Review the Simulation settings and try again.');
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
      if (reading) await awaitSolveStep(reading, continuation);
      const view = frameAtPress?.status === 'ready' ? frameAtPress : useCadSolverFrameStore.getState().frames[ingestId];
      const frameAxis = view && !view.linked && view.status === 'ready' ? view.axis : null;
      if (clicked) await awaitSolveStep(clicked.checkPlan(), continuation);
      const approvals = reviewedFindings(record);
      const press: ModelSolvePress = {
        prepared_id: ingestId, client_request_id: clientRequestId, label,
        ...(unchanged ?? { setup: built!.setup }),
        ...(frameAxis ? { frame_axis: frameAxis } : {}),
        ...(approvals ? { approvals } : {}),
      };
      // The completion claim exists before HTTP: an event may beat the response.
      if (!beforePost(continuation)) return 'blocked' as const;
      rememberCadSolve(key, designName, ingestId);
      solveAttention.bindOperation(key);
      const jobId = await solveModel(importedModelId(record), press).catch((error) => {
        if (error instanceof SolveSubmissionRefused) {
          // Refused: no job exists for this press, so nothing may claim it.
          forgetCadSolve(key);
          solveAttention.releaseOperation(key);
        }
        throw importedSolveError(error, unchanged !== null);
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

  const continueRun = useCallback(async (jobId: string, patch: ContinueRunPatch = {}) => {
    const errorNavigation = patch.held ? undefined : navigationGeneration();
    const cad = patch.approvals !== undefined || patch.held !== undefined || patch.mesh_limit_override !== undefined;
    return acceptSubmission(async () => {
      if (cad) {
        const approvals = patch.approvals;
        if (patch.held) {
          const reading = frameReadInFlight(patch.held.model.ingestId);
          if (reading) await awaitSolveStep(reading, patch.held);
        }
        solveAttention.bindOperation(jobId);
        let child;
        try {
          if (approvals) await approveCadJob(jobId, approvals);
          // The token and held-job guard survive every await through the POST.
          // No overrides: the server continues this job’s saved request.
          if (patch.held && (patch.held.jobId !== jobId || !beforePost(patch.held))) {
            solveAttention.releaseOperation(jobId);
            // An observer may already have reported and latched the cancellation.
            // Unmount also aborts the token, and must stay silent.
            if (!patch.held.signal.aborted) setActionError(solveCancellationReason(patch.held)
              ?? 'Prepare and solve was cancelled because the waiting job changed.');
            return null;
          }
          child = await solveCadAgain(jobId, { approvals, ...(patch.mesh_limit_override !== undefined ? { mesh_limit_override: patch.mesh_limit_override } : {}) });
        } catch (error) {
          solveAttention.releaseOperation(jobId);
          throw error;
        }
        const parent = jobsSocket.getSnapshot().jobs.find((job) => job.id === jobId);
        const claim = parent ? cadSolveClaim(parent) : null;
        rememberCadSolve(child.job_id, claim?.designName ?? '', claim?.sourceIngestId ?? null);
        return child.job_id;
      }
      return patch.options ? rerunWithPatch(jobId, patch) : jobsSocket.retryJob(jobId);
    }, {
      busyMessage: 'Another solve is being submitted. Try again in a moment.',
      errorNavigation,
      onAccepted: (id) => {
        if (patch.options) compareSelection.followLatest(jobId);
        if (cad) {
          solveAttention.followJob(jobId, id);
          const job = jobsSocket.getSnapshot().jobs.find((candidate) => candidate.id === id);
          if (job) completeCadSolve(job);
        }
      },
    });
  }, [acceptSubmission, beforePost, completeCadSolve]);

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
    publishBridge({ available: true, continueRun, reportError, actionError });
    return () => publishBridge({
      available: false,
      continueRun: unavailableRun,
      reportError: () => undefined,
      actionError: null,
    });
  }, [actionError, reportError, continueRun]);

  useEffect(() => {
    const retryExport = (event: Event) => automation.retryExport((event as CustomEvent<string>).detail);
    window.addEventListener('wg-retry-auto-export', retryExport);
    return () => window.removeEventListener('wg-retry-auto-export', retryExport);
  }, [automation]);
  useEffect(() => {
    void automation.process(jobs, preferences, {
      downloadMesh: (job, reportNotice) => saveMeshArtifactToWorkspace(job, reportNotice),
      markMeshDownloaded: (job, filename) => jobsSocket.patchMetadata(job.id, { mesh_artifact_file: filename }),
      exportCompleted: (job, formats) => ownedAutoExport(job, formats, (job, formats, fetcher) => exportRunAutomatically(job, formats, {
        fetcher,
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
  const requestCurrent = requested.state.active !== null;
  const performRequestedSolve = async (continuation: ModelSolveContinuation) => {
    if (fileGeometryActive) return solveFileModel(continuation);
    if (cadGeometryActive) {
      solveAttention.armSolve();
      const draftError = getCrossoverDraftError();
      if (draftError) throw new Error(draftError);
      if (continuation.checkCadPlan) {
        const cad = useCadReturnStore.getState();
        const body = importedSolvePlanRequestBody(buildImportedSubmission(cad));
        return solveCurrentCadImport({ cad, checkPlan: async () => {
          const freshPlan = await postImportedSolvePlan(body);
          if (!freshPlan.engine) throw new Error(freshPlan.reason || 'No engine can solve these CAD settings.');
        } }, continuation);
      }
      return solveCurrentCadImport(undefined, continuation);
    }
    const current = useDesignStore.getState();
    await run(current.design, current.designRevision, continuation);
    return 'submitted' as const;
  };
  const solve = useCallback(async () => {
    if (submissionInFlight.current) return 'busy' as const;
    if (engineBlocked) workspaceNavigation.activate('results');
    const focused = document.activeElement;
    const commitCrossover = cadGeometryActive && focused instanceof HTMLInputElement && focused.matches('[data-crossover-frequency]');
    if (commitCrossover) focused.blur();
    const held = heldEngineSolveJob();
    if (held && engineBlocked) {
      await requested.fusion(held.id, serverReadiness?.engine ?? selectedEngine);
      return 'blocked' as const;
    }
    if (warming || planPending) solveAttention.armSolve();
    return requested.press(undefined, commitCrossover);
  }, [requested.press, requested.fusion, serverReadiness, selectedEngine, engineBlocked, warming, cadGeometryActive, planPending]);
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

  const heldPreparation = useHeldSolvePreparation(requested, engines, setActionError);
  const prepareAndSolve = async (engine: string) => {
    if (heldEngineSolveJob()) {
      await heldPreparation.prepareAndSolve(engine);
      return;
    }
    await requested.press(engine);
  };
  const readiness = solveBlocker || solveOptionsError
    ? messageBlocker(solveBlocker || solveOptionsError!)
    : serverReadiness ?? (planError ? messageBlocker(planError) : null);
  const control = useMemo<SolveControl>(() => ({
    readiness,
    readinessRequested: requestCurrent,
    waitingForEngine: !!requested.state.active?.preparationEngine && !['submitting', 'cancelling'].includes(requested.state.phase),
    waitingEngine: requested.state.active?.preparationEngine ?? null,
    stopWaiting: heldPreparation.stop,
    prepareEngine: preparation.prepare,
    prepareAndSolve,
    preparationError: preparation.error,
    actionError,
    preparingEngine: preparation.starting ?? engines.find(engine => engine.name === selectedEngine && engine.runtime_state === 'preparing')?.name ?? null,
    useEngine: (engine: string) => solveOptions.setEngine(engine),
    plan: solvePlan,
    planPending: solvePlanPending,
    solve,
    cadMode: cadGeometryActive,
    disabled: !solveEnabled,
    submitting,
    label: 'Solve',
    title: readiness?.message ?? (submitting
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
                : parametricUnavailable),
  }), [requested.state, requestCurrent, warming, actionError, engines, readiness, preparation.prepare, preparation.error, preparation.starting, prepareAndSolve, solveOptions, cadGeometryActive, capabilityError, fileBlocker, fileGeometryActive, importedUnavailable, importedEngineLabel, parametricUnavailable, planError, planPending, selectedEngine, solve, solveEnabled, solveBlocker, solvePlan, solvePlanPending, submitting]);

  useEffect(() => {
    publishSolveControl(control);
    return () => publishSolveControl(null);
  }, [control]);

  return <SolveContext.Provider value={control}>{children}<JobAnnouncer jobs={jobs}/></SolveContext.Provider>;
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
    return `Solve current design with Auto (${resolved.toUpperCase()})`;
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
