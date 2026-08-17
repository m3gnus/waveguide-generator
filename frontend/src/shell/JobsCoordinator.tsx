import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { fetchJobResults } from '../api/results';
import { resolveEngine, submitDesign, submitImported, type ImportedSolveSubmission } from '../jobs/actions';
import { useCapabilities, useCapabilityRefreshOnReconnect } from '../jobs/useCapabilities';
import { JobAutomation } from '../jobs/automation';
import { exportStemForJob } from '../jobs/exportNaming';
import { buildImportedSubmission, importedSubmissionBlocker } from '../jobs/importedSubmission';
import { decorateRunName, nextRunName } from '../jobs/runNaming';
import { projectSubmittedDesign, projectSubmittedImport, submittedProjectionsEqual, type SubmittedDesignProjection } from '../jobs/submittedProjection';
import { preferencesStore, usePreferences } from '../prefs/preferences';
import { runWorkspaceExportBundle, saveMeshArtifactToWorkspace } from '../results/exporters';
import { resultExportSnapshot } from '../results/exportContext';
import type { ResultPayload } from '../results/types';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useCadReturnStore } from '../stores/cadReturn';
import { useSolveOptionsStore, type SolveOptions } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';

interface SolveControl {
  solve(): void;
  disabled: boolean;
  submitting: boolean;
  label: string;
  title: string;
}

interface CoordinatorBridgeSnapshot {
  run(design: DesignDocument, designRevision?: number): Promise<void>;
  runImported(submission: ImportedSolveSubmission): Promise<void>;
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
  design = useDesignStore.getState().design,
  options = useSolveOptionsStore.getState().options(),
  filename = useDocumentStore.getState().filename,
  now = new Date(),
): string {
  const preferences = preferencesStore.getSnapshot();
  return decorateRunName(nextRunName(
    preferences,
    projectSubmittedDesign(design, options),
    filename,
  ), preferences, now);
}

function sameNamingIntent(
  expected: Pick<ReturnType<typeof preferencesStore.getSnapshot>, 'outputName' | 'nameSourceProjection'>,
): boolean {
  const current = preferencesStore.getSnapshot();
  if (current.outputName !== expected.outputName) return false;
  if (current.nameSourceProjection === expected.nameSourceProjection) return true;
  if (!current.nameSourceProjection || !expected.nameSourceProjection
    || current.nameSourceProjection === 'unbound' || expected.nameSourceProjection === 'unbound') return false;
  return submittedProjectionsEqual(current.nameSourceProjection, expected.nameSourceProjection);
}

function acceptSubmittedName(
  coreName: string,
  projection: SubmittedDesignProjection,
  expected: Pick<ReturnType<typeof preferencesStore.getSnapshot>, 'outputName' | 'nameSourceProjection'>,
): void {
  // A submission owns the baseline it started from, but a later name edit is
  // newer user intent and must not be rolled back when the request completes.
  if (!sameNamingIntent(expected)) return;
  preferencesStore.update({ outputName: coreName, nameSourceProjection: projection });
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

export function JobsCoordinator({ children, now = systemNow }: { children: ReactNode; now?: () => Date }) {
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  // This component owns the jobs socket, so it is where a reconnect is visible.
  useCapabilityRefreshOnReconnect(useSyncExternalStore(jobsSocket.subscribe, jobsConnection, jobsConnection));
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const filename = useDocumentStore((state) => state.filename);
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
  const { engines: capabilities, error: capabilityError } = useCapabilities();
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submissionInFlight = useRef(false);

  useEffect(() => { jobsSocket.start(); return () => jobsSocket.stop(); }, []);

  let effectiveEngine = selectedEngine;
  if (selectedEngine === 'auto') {
    try { effectiveEngine = resolveEngine('auto', { engines: capabilities }, design.simulation.solver_mode); } catch { /* surfaced below */ }
  }
  const capability = capabilities.find((engine) => engine.name.toLowerCase() === effectiveEngine.toLowerCase()) ?? null;
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

  const run = useCallback(async (nextDesign: DesignDocument, nextRevision = revision) => {
    if (submissionInFlight.current) return;
    submissionInFlight.current = true;
    try {
      if (!capability?.available) throw new Error(capability?.reason ?? capabilityError ?? `${selectedEngine} engine is unavailable`);
      setSubmitting(true);
      setActionError(null);
      let submittedDesign = nextDesign;
      let submittedRevision = nextRevision;
      const wallThickness = nextDesign.mesh.wall_thickness;
      if (
        effectiveEngine.toLowerCase() === 'bempp'
        && nextDesign.simulation.sim_type !== 'infinite-baffle'
        && nextDesign.enclosure.depth <= 0
        && (wallThickness == null || wallThickness === 0)
      ) {
        useDesignStore.getState().updateValue('mesh.wall_thickness', 5);
        const corrected = useDesignStore.getState();
        submittedDesign = corrected.design;
        submittedRevision = corrected.designRevision;
      }
      const options = useSolveOptionsStore.getState().options();
      const projection = projectSubmittedDesign(submittedDesign, options);
      const naming = preferencesStore.getSnapshot();
      const coreName = nextRunName(naming, projection, filename);
      const label = decorateRunName(coreName, naming, now());
      await submitDesign(
        submittedDesign,
        options,
        fetch,
        { label, designRevision: submittedRevision },
      );
      acceptSubmittedName(coreName, projection, naming);
      await jobsSocket.refresh();
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [capability, capabilityError, effectiveEngine, filename, now, preferences, revision, selectedEngine]);

  const runImported = useCallback(async (submission: ImportedSolveSubmission) => {
    if (submissionInFlight.current) return;
    submissionInFlight.current = true;
    try {
      const metal = capabilities.find((engine) => engine.name.toLowerCase() === 'metal');
      if (!metal?.available) throw new Error(metal?.reason ?? capabilityError ?? 'Metal engine is unavailable');
      setSubmitting(true);
      setActionError(null);
      const options: SolveOptions = { ...submission.options, engine: 'metal', symmetry: 'auto' };
      const effectiveSubmission = { ...submission, options };
      // The ingested artifact is the solve truth. Parametric edits can request
      // the next CAD rebuild, but they must not rename a byte-identical solve.
      const projection = projectSubmittedImport(effectiveSubmission);
      const naming = preferencesStore.getSnapshot();
      const coreName = nextRunName(naming, projection, filename);
      const label = decorateRunName(coreName, naming, now());
      await submitImported(
        effectiveSubmission,
        fetch,
        label,
      );
      acceptSubmittedName(coreName, projection, naming);
      await jobsSocket.refresh();
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [capabilities, capabilityError, filename, now, preferences]);

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
        jobStem: exportStemForJob(job),
        preferences,
      }, formats),
      markExported: async (job, files, formats, completedAt) => jobsSocket.patchMetadata(job.id, {
        exported_files: [...new Set([...(job.exported_files ?? []), ...files])],
        auto_export_formats: formats,
        auto_export_completed_at: completedAt,
      }),
      reportError,
    });
  }, [automation, jobs, preferences, reportError]);

  const unavailable = capability?.reason ?? capabilityError ?? 'Checking solver engine capability…';
  const activeCapability = cadGeometryActive ? metalCapability : capability;
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
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && activeCapability?.available && !submitting && !cadSolveBlocker && !fileGeometryActive) {
        event.preventDefault();
        solve();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [activeCapability, cadSolveBlocker, fileGeometryActive, solve, submitting]);

  const control = useMemo<SolveControl>(() => ({
    solve,
    disabled: !activeCapability?.available || submitting || Boolean(cadSolveBlocker) || fileGeometryActive,
    submitting,
    label: cadGeometryActive ? 'Solve CAD Link' : 'Solve',
    title: submitting
      ? 'Submitting solve…'
      : fileGeometryActive
        ? 'Standalone imported meshes are viewport-only. Show Parametric to solve the WG design.'
        : cadSolveBlocker
          ? cadSolveBlocker
          : cadGeometryActive && activeCapability?.available
            ? 'Solve the displayed CAD Link model with Metal'
            : activeCapability?.available
              ? `Solve current design with ${selectedEngine === 'auto' ? `AUTO (${activeCapability.name})` : activeCapability.name}`
              : cadGeometryActive
                ? metalCapability?.reason ?? capabilityError ?? 'Metal engine is unavailable'
                : unavailable,
  }), [activeCapability, cadGeometryActive, cadSolveBlocker, capabilityError, fileGeometryActive, metalCapability?.reason, selectedEngine, solve, submitting, unavailable]);

  return <SolveContext.Provider value={control}>{children}<JobAnnouncer jobs={jobs}/></SolveContext.Provider>;
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
