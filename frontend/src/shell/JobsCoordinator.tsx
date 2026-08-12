import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { fetchJobResults } from '../api/results';
import { resolveEngine, submitDesign, submitImported, type ImportedSolveSubmission } from '../jobs/actions';
import { useCapabilities, useCapabilityRefreshOnReconnect } from '../jobs/useCapabilities';
import { JobAutomation } from '../jobs/automation';
import { hydrateJobDesign } from '../jobs/jobDesign';
import { exportStemForJob } from '../jobs/exportNaming';
import { jobBaseName, preferencesStore, usePreferences } from '../prefs/preferences';
import { downloadMeshArtifact, runExportBundle } from '../results/exporters';
import type { ResultPayload } from '../results/types';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { useSolveOptionsStore } from '../stores/solveOptions';

interface SolveControl {
  solve(): void;
  disabled: boolean;
  submitting: boolean;
  title: string;
}

interface CoordinatorBridgeSnapshot {
  run(design: DesignDocument, designRevision?: number): Promise<void>;
  runImported(submission: ImportedSolveSubmission): Promise<void>;
  retry(jobId: string): Promise<void>;
  reportError(message: string): void;
  actionError: string | null;
}

const unavailableRun = async () => { throw new Error('Solve coordinator is unavailable'); };
let bridgeSnapshot: CoordinatorBridgeSnapshot = {
  run: unavailableRun,
  runImported: unavailableRun,
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

export function incrementJobVersion(): void {
  const current = preferencesStore.getSnapshot().jobVersion;
  preferencesStore.update({ jobVersion: Math.min(999_999, current + 1) });
}

/** Read naming at submission time, including a commit from the same key event. */
export function currentJobLabel(): string {
  return jobBaseName(preferencesStore.getSnapshot());
}

const jobsConnection = () => jobsSocket.getSnapshot().connection;

export function JobsCoordinator({ children }: { children: ReactNode }) {
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  // This component owns the jobs socket, so it is where a reconnect is visible.
  useCapabilityRefreshOnReconnect(useSyncExternalStore(jobsSocket.subscribe, jobsConnection, jobsConnection));
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const selectedEngine = useSolveOptionsStore((state) => state.engine);
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

  const run = useCallback(async (nextDesign: DesignDocument, nextRevision = revision) => {
    if (submissionInFlight.current) return;
    submissionInFlight.current = true;
    try {
      if (!capability?.available) throw new Error(capability?.reason ?? capabilityError ?? `${selectedEngine} engine is unavailable`);
      setSubmitting(true);
      setActionError(null);
      await submitDesign(
        nextDesign,
        useSolveOptionsStore.getState().options(),
        fetch,
        { label: currentJobLabel(), designRevision: nextRevision },
      );
      incrementJobVersion();
      await jobsSocket.refresh();
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [capability, capabilityError, preferences, revision, selectedEngine]);

  const runImported = useCallback(async (submission: ImportedSolveSubmission) => {
    if (submissionInFlight.current) return;
    submissionInFlight.current = true;
    try {
      const metal = capabilities.find((engine) => engine.name.toLowerCase() === 'metal');
      if (!metal?.available) throw new Error(metal?.reason ?? capabilityError ?? 'Metal engine is unavailable');
      setSubmitting(true);
      setActionError(null);
      await submitImported(
        { ...submission, options: { ...submission.options, engine: 'metal', symmetry: 'auto' } },
        fetch,
        currentJobLabel(),
      );
      incrementJobVersion();
      await jobsSocket.refresh();
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }, [capabilities, capabilityError, preferences]);

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
    publishBridge({ run, runImported, retry, reportError, actionError });
    return () => publishBridge({ run: unavailableRun, runImported: unavailableRun, retry: unavailableRun, reportError: () => undefined, actionError: null });
  }, [actionError, reportError, retry, run, runImported]);

  useEffect(() => {
    void automation.process(jobs, preferences, {
      downloadMesh: (job) => downloadMeshArtifact(job),
      markMeshDownloaded: (job, filename) => jobsSocket.patchMetadata(job.id, { mesh_artifact_file: filename }),
      exportCompleted: async (job, formats) => runExportBundle({
        result: await fetchJobResults(job.id) as ResultPayload,
        design: hydrateJobDesign(job) ?? undefined,
        designRevision: job.design_revision,
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
  const solve = useCallback(() => {
    void run(design, revision).catch((error) => reportError(error instanceof Error ? error.message : String(error)));
  }, [design, reportError, revision, run]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && capability?.available && !submitting) {
        event.preventDefault();
        solve();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [capability, solve, submitting]);

  const control = useMemo<SolveControl>(() => ({
    solve,
    disabled: !capability?.available || submitting,
    submitting,
    title: capability?.available
      ? (submitting ? 'Submitting solve…' : `Solve current design with ${selectedEngine === 'auto' ? `AUTO (${capability.name})` : capability.name}`)
      : unavailable,
  }), [capability, selectedEngine, solve, submitting, unavailable]);

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
