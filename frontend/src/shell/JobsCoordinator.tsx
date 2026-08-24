import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { compareSelection, fetchJobResults } from '../api/results';
import { planSolveDesign, submitDesign, submitImported, type ImportedSolveSubmission, type SolvePlan } from '../jobs/actions';
import { useCapabilities, useCapabilityRefreshOnReconnect } from '../jobs/useCapabilities';
import { useSolvePlan } from '../jobs/useSolvePlan';
import { JobAutomation } from '../jobs/automation';
import { exportStemForJob, exportSubdirectoryForJob } from '../jobs/exportNaming';
import { explainImportedRefusal } from '../jobs/importedRefusals';
import { buildImportedSubmission, importedSubmissionBlocker } from '../jobs/importedSubmission';
import { advanceRunSequence, nextRunLabel } from '../jobs/runNaming';
import { currentRunNameSource } from '../jobs/runNameSource';
import { preferencesStore, usePreferences } from '../prefs/preferences';
import { archiveRunToWorkspace, runWorkspaceExportBundle, saveMeshArtifactToWorkspace } from '../results/exporters';
import { resultExportSnapshot } from '../results/exportContext';
import type { ResultPayload } from '../results/types';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useCadReturnStore } from '../stores/cadReturn';
import { consumeParkedSolveCommand, parkedSolveCommandStore } from '../stores/solveCommand';
import { polarValidationError, useSolveOptionsStore, type SolveOptions } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';

interface SolveControl {
  solve(): void;
  disabled: boolean;
  submitting: boolean;
  label: string;
  title: string;
}

/** The imported path forces Metal, so an unavailable engine is not a gate the
 * user can satisfy — it is a permanent refusal on this machine. Automatic
 * callers need to tell that apart from a blocker to avoid parking forever. */
export class SolveEngineUnavailableError extends Error {}

interface CoordinatorBridgeSnapshot {
  run(design: DesignDocument, designRevision?: number): Promise<void>;
  /** Resolves with the submitted job id, or null when the submission mutex
   * refused this call. */
  runImported(submission: ImportedSolveSubmission): Promise<string | null>;
  /** The single CAD solve entry point: readiness gate, submission build, and
   * submit through runImported so every caller inherits the Metal capability
   * check, the submission mutex, run naming, and the job-list refresh.
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
  const cadReturn = useCadReturnStore.getState();
  const cad = importedMeshStore.getSnapshot().cad;
  if (cadReturn.ingestRecord !== null && cad !== null
    && (!cad.ingestId || cad.ingestId !== cadReturn.ingestRecord.ingest_id)) {
    return CAD_VIEWPORT_MISMATCH;
  }
  return importedSubmissionBlocker(cadReturn);
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
    engines: capabilities,
    error: capabilityError,
  } = useCapabilities();
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submissionInFlight = useRef(false);

  useEffect(() => { jobsSocket.start(); return () => jobsSocket.stop(); }, []);

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
  const metalCapability = capabilities.find((engine) => engine.name.toLowerCase() === 'metal') ?? null;
  const visibleImported = viewportGeometry.showing === 'cad'
    ? viewportGeometry.cad
    : viewportGeometry.showing === 'file'
      ? viewportGeometry.file
      : null;
  // Mode owns solve intent. The viewport CAD slot is evidence for the mismatch
  // guard only; an empty slot must still route to the imported blocker so CAD
  // mode can truthfully say that an ingest is required.
  const cadGeometryActive = workspaceMode === 'cad';
  const fileGeometryActive = !cadGeometryActive && visibleImported?.source === 'file';
  const cadViewportGeometry = viewportGeometry.cad;
  const cadGeometryMismatch = cadGeometryActive && cadReturn.ingestRecord !== null && cadViewportGeometry !== null && (
    !cadViewportGeometry.ingestId
    || cadViewportGeometry.ingestId !== cadReturn.ingestRecord?.ingest_id
  );
  const cadSolveBlocker = cadGeometryMismatch
    ? CAD_VIEWPORT_MISMATCH
    : cadGeometryActive
      ? importedSubmissionBlocker(cadReturn, solveOptions)
      : null;
  const directivityError = polarValidationError(solveOptions.polar);
  const solveBlocker = cadSolveBlocker ?? directivityError;

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
      const metal = capabilities.find((engine) => engine.name.toLowerCase() === 'metal');
      if (!metal?.available) throw new SolveEngineUnavailableError(metal?.reason ?? capabilityError ?? 'Metal engine is unavailable');
      setSubmitting(true);
      setActionError(null);
      const options: SolveOptions = { ...submission.options, engine: 'metal', symmetry: 'auto' };
      const effectiveSubmission = { ...submission, options };
      // The CAD document names its own runs; see jobs/runNameSource.
      const designName = currentRunNameSource().name;
      const label = nextRunLabel(designName, preferencesStore.getSnapshot(), now());
      const parked = parkedSolveCommandStore.getSnapshot().command;
      const selectedBundlePath = useCadReturnStore.getState().selectedBundle?.bundlePath;
      const clientRequestId = parked && parked.bundlePath === selectedBundlePath
        ? `cad-solve:${parked.commandId}`
        : undefined;
      // A typed refusal is the server naming a condition; the user needs the
      // remedy. Translating here covers every imported entry point at once.
      const jobId = await submitImported(effectiveSubmission, fetch, label, clientRequestId).catch((error) => {
        throw error instanceof Error
          ? new Error(explainImportedRefusal(error.message))
          : error;
      });
      // A Fusion-authored request is retired by its ledger entry, not by the
      // marker on disk. Reporting the job from the one place every imported
      // solve passes through is what makes a manual Solve consume the parked
      // command instead of leaving it to replay into a duplicate run.
      await consumeParkedSolveCommand(jobId);
      // Do not advance the label sequence until the CAD acknowledgement is
      // durable. A replay after an acknowledgement failure must hash to the
      // identical solve request and recover this same job id.
      acceptSubmittedLabel(designName);
      compareSelection.awaitRun(jobId);
      await jobsSocket.refresh();
      return jobId;
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [capabilities, capabilityError, now, preferences]);

  // Nothing awaits between the mutex read and runImported's own claim of it,
  // so a busy report here cannot race a submission into existence.
  const solveCurrentCadImport = useCallback(async () => {
    if (submissionInFlight.current) return 'busy' as const;
    const blocker = cadSolveBlockerNow();
    if (blocker) throw new Error(blocker);
    await runImported(buildImportedSubmission(useCadReturnStore.getState()));
    return 'submitted' as const;
  }, [runImported]);

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
    ? Boolean(metalCapability?.available)
    : solvePlan !== null && !solvePlanPending && solvePlanError === null;
  const solve = useCallback(() => {
    const action = async () => {
      if (fileGeometryActive) {
        throw new Error('A standalone imported mesh is for viewport inspection only. Show Parametric to solve the WG design.');
      }
      if (cadGeometryActive) {
        await solveCurrentCadImport();
        return;
      }
      await run(design, revision);
    };
    void action().catch((error) => reportError(error instanceof Error ? error.message : String(error)));
  }, [cadGeometryActive, design, fileGeometryActive, reportError, revision, run, solveCurrentCadImport]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && solveAvailable && !submitting && !solveBlocker && !fileGeometryActive) {
        event.preventDefault();
        solve();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [fileGeometryActive, solve, solveAvailable, solveBlocker, submitting]);

  const control = useMemo<SolveControl>(() => ({
    solve,
    disabled: !solveAvailable || submitting || Boolean(solveBlocker) || fileGeometryActive,
    submitting,
    label: cadGeometryActive ? 'Solve CAD Link' : 'Solve',
    title: submitting
      ? 'Submitting solve…'
      : fileGeometryActive
        ? 'Standalone imported meshes are viewport-only. Show Parametric to solve the WG design.'
        : solveBlocker
          ? solveBlocker
          : cadGeometryActive && metalCapability?.available
            ? 'Solve the displayed CAD Link model with Metal'
            : solvePlan
              ? solvePlanTitle(solvePlan, selectedEngine)
              : cadGeometryActive
                ? metalCapability?.reason ?? capabilityError ?? 'Metal engine is unavailable'
                : parametricUnavailable,
  }), [cadGeometryActive, capabilityError, fileGeometryActive, metalCapability?.available, metalCapability?.reason, parametricUnavailable, selectedEngine, solve, solveAvailable, solveBlocker, solvePlan, submitting]);

  return <SolveContext.Provider value={control}>{children}<JobAnnouncer jobs={jobs}/></SolveContext.Provider>;
}

export function solvePlanTitle(plan: SolvePlan, requestedEngine: string): string {
  const requested = requestedEngine.trim().toLowerCase();
  const resolved = plan.engine.trim().toLowerCase();
  if (requested === 'auto') {
    return `Solve current design with AUTO (${resolved.toUpperCase()})`;
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
