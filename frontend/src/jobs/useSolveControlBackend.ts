import { useSyncExternalStore } from 'react';
import { useDesignStore } from '../stores/design';
import { useSolveOptionsStore, type SolveOptions } from '../stores/solveOptions';
import { workspaceModeStore, type WorkspaceMode } from '../stores/workspaceMode';
import { activeBackendCapability } from '../design/backendSupport';
import { accuracyEngine, useCapabilities } from './useCapabilities';
import { useSolvePlan } from './useSolvePlan';
import { useImportedSolvePlan } from './useImportedSolvePlan';

/** Controls share the submission planner; routing may depend on geometry and mounting. */
export function useSolveControlBackend(modeOverride?: WorkspaceMode) {
  const workspace = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot);
  const mode = modeOverride ?? workspace.mode;
  const design = useDesignStore((state) => state.design);
  const store = useSolveOptionsStore();
  const { engines, engineSelection } = useCapabilities();
  let options: SolveOptions | null = null;
  try { options = store.options(); } catch { /* An invalid frequency list has no plan. */ }
  const parametric = useSolvePlan(design, options, mode === 'parametric');
  const imported = useImportedSolvePlan(mode === 'cad');
  const snapshot = mode === 'cad' ? imported : parametric;
  const engine = snapshot.plan?.engine
    ?? accuracyEngine(store.engine, store.accuracy, engines);
  return {
    ...snapshot,
    capability: activeBackendCapability(engine, engines, engineSelection),
  };
}
