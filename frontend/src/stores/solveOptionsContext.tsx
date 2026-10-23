import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';
import { useStore, type StoreApi, type UseBoundStore } from 'zustand';
import { runNormalizationAngle } from '../results/normalization';
import { workspaceModeStore, type WorkspaceMode } from './workspaceMode';
import { useImportedSolveOptionsStore, useSolveOptionsStore, type SolveOptionsStore } from './solveOptions';

export type SolveOptionsStoreApi = UseBoundStore<StoreApi<SolveOptionsStore>>;

/** The store a mode's solve controls read: parametric is persisted, imported is not. */
export function solveOptionsStoreForMode(mode: WorkspaceMode): SolveOptionsStoreApi {
  return mode === 'cad' ? useImportedSolveOptionsStore : useSolveOptionsStore;
}

/** Legacy run displays follow the live options for their geometry kind. */
export function solveOptionsStoreForJob(job: { config_summary?: Record<string, unknown> | null }): SolveOptionsStoreApi {
  return job.config_summary?.geometry_type === 'imported' ? useImportedSolveOptionsStore : useSolveOptionsStore;
}

const SolveOptionsStoreContext = createContext<SolveOptionsStoreApi | null>(null);

export function SolveOptionsStoreProvider({ mode, children }: { mode: WorkspaceMode; children: ReactNode }) {
  return <SolveOptionsStoreContext.Provider value={solveOptionsStoreForMode(mode)}>
    {children}
  </SolveOptionsStoreContext.Provider>;
}

/**
 * The solve-options store for this part of the tree: the one a provider names,
 * else the one for the current workspace mode (Imported uses its own instance).
 */
export function useScopedSolveOptionsStore(): SolveOptionsStoreApi {
  const provided = useContext(SolveOptionsStoreContext);
  const workspace = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot);
  return provided ?? solveOptionsStoreForMode(workspace.mode);
}

/** Drop-in for `useSolveOptionsStore(selector)` that follows the scope. */
export function useScopedSolveOptions(): SolveOptionsStore;
export function useScopedSolveOptions<T>(selector: (state: SolveOptionsStore) => T): T;
export function useScopedSolveOptions<T>(selector?: (state: SolveOptionsStore) => T): T | SolveOptionsStore {
  const store = useScopedSolveOptionsStore();
  return useStore(store, selector as (state: SolveOptionsStore) => T);
}

/** Recorded run reference, legacy run's live reference, or the scope's draft. */
export function useRunNormAngle(job?: (Parameters<typeof runNormalizationAngle>[0]
  & Parameters<typeof solveOptionsStoreForJob>[0]) | null): number {
  const scoped = useScopedSolveOptionsStore();
  const draft = useStore(job ? solveOptionsStoreForJob(job) : scoped, (state) => state.polar.normAngle);
  return job ? runNormalizationAngle(job, draft) : draft;
}
