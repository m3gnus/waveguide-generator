import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { jobsSocket } from '../api/jobsSocket';
import { fetchJobResults } from '../api/results';
import { getCapabilities, resolveEngine, submitDesign, type EngineCapability } from '../jobs/actions';
import { JobAutomation } from '../jobs/automation';
import { hydrateJobDesign } from '../jobs/jobDesign';
import { exportBaseName, preferencesStore, usePreferences } from '../prefs/preferences';
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
  reportError(message: string): void;
  actionError: string | null;
}

const unavailableRun = async () => { throw new Error('Solve coordinator is unavailable'); };
let bridgeSnapshot: CoordinatorBridgeSnapshot = {
  run: unavailableRun,
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

export function JobsCoordinator({ children }: { children: ReactNode }) {
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const selectedEngine = useSolveOptionsStore((state) => state.engine);
  const preferences = usePreferences();
  const automation = useRef(new JobAutomation()).current;
  const [capabilities, setCapabilities] = useState<EngineCapability[]>([]);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => { jobsSocket.start(); return () => jobsSocket.stop(); }, []);
  useEffect(() => {
    let live = true;
    void getCapabilities().then((value) => {
      if (live) setCapabilities(value.engines);
    }).catch((error) => {
      if (live) setCapabilityError(error instanceof Error ? error.message : String(error));
    });
    return () => { live = false; };
  }, []);

  let effectiveEngine = selectedEngine;
  if (selectedEngine === 'auto') {
    try { effectiveEngine = resolveEngine('auto', { engines: capabilities }, design.simulation.solver_mode); } catch { /* surfaced below */ }
  }
  const capability = capabilities.find((engine) => engine.name.toLowerCase() === effectiveEngine.toLowerCase()) ?? null;

  const run = useCallback(async (nextDesign: DesignDocument, nextRevision = revision) => {
    if (!capability?.available) throw new Error(capability?.reason ?? capabilityError ?? `${selectedEngine} engine is unavailable`);
    setSubmitting(true);
    setActionError(null);
    try {
      await submitDesign(
        nextDesign,
        useSolveOptionsStore.getState().options(),
        fetch,
        { label: exportBaseName(preferences), designRevision: nextRevision },
      );
      await jobsSocket.refresh();
    } finally {
      setSubmitting(false);
    }
  }, [capability, capabilityError, preferences, revision, selectedEngine]);

  const reportError = useCallback((message: string) => setActionError(message), []);
  useEffect(() => {
    publishBridge({ run, reportError, actionError });
    return () => publishBridge({ run: unavailableRun, reportError: () => undefined, actionError: null });
  }, [actionError, reportError, run]);

  useEffect(() => {
    void automation.process(jobs, preferences, {
      downloadMesh: (job) => downloadMeshArtifact(job.id),
      exportCompleted: async (job, formats) => runExportBundle({
        result: await fetchJobResults(job.id) as ResultPayload,
        design: hydrateJobDesign(job) ?? undefined,
        designRevision: job.design_revision,
        preferences,
      }, formats),
      markExported: async (job, files, formats, completedAt) => jobsSocket.patchMetadata(job.id, {
        exported_files: [...new Set([...(job.exported_files ?? []), ...files])],
        auto_export_formats: formats,
        auto_export_completed_at: completedAt,
      }),
      incrementCounter: () => preferencesStore.update({ counter: Math.min(999_999, preferencesStore.getSnapshot().counter + 1) }),
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

  return <SolveContext.Provider value={control}>{children}</SolveContext.Provider>;
}
