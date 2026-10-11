import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { useShallow } from 'zustand/react/shallow';
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
  const store = useSolveOptionsStore(useShallow((state) => ({
    engine: state.engine, accuracy: state.accuracy,
    adaptiveFrequencySampling: state.adaptiveFrequencySampling,
    solverMode: state.solverMode, symmetry: state.symmetry,
    meshValidationMode: state.meshValidationMode, verbose: state.verbose,
    frequencySpacing: state.frequencySpacing, frequencyMode: state.frequencyMode,
    frequencyListText: state.frequencyListText, polar: state.polar,
    groundPlane: state.groundPlane, options: state.options,
  })));
  const { engines, engineSelection } = useCapabilities();
  const options = useMemo<SolveOptions | null>(() => {
    try { return store.options(); } catch { return null; }
  }, [store]);
  const parametric = useSolvePlan(design, options, mode === 'parametric');
  const imported = useImportedSolvePlan(mode === 'cad');
  const snapshot = mode === 'cad' ? imported : parametric;
  // A control may keep its last routing answer during edits; submission must
  // still use only the current plan returned by the planner hooks.
  const routingKey = `${mode}/${store.engine}/${store.accuracy}`;
  const settled = useRef<{ key: string; engine: string } | null>(null);
  useEffect(() => {
    if (snapshot.plan?.engine) settled.current = { key: routingKey, engine: snapshot.plan.engine };
  }, [snapshot.plan, routingKey]);
  const engine = snapshot.plan?.engine
    ?? (snapshot.isPending && settled.current?.key === routingKey ? settled.current.engine : null)
    ?? accuracyEngine(store.engine, store.accuracy, engines);
  return {
    ...snapshot,
    capability: snapshot.isPending && !snapshot.plan && settled.current?.key !== routingKey
      && store.engine === 'auto' && store.accuracy === 'fast'
      ? null : activeBackendCapability(engine, engines, engineSelection),
  };
}
