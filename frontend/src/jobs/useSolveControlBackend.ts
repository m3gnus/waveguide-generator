import { useCurrentModel } from '../model/currentModel';
import { planSubmission } from './importedSubmission';
import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useDesignStore } from '../stores/design';
import { useStore } from 'zustand';
import type { SolveOptions } from '../stores/solveOptions';
import { solveOptionsStoreForMode } from '../stores/solveOptionsContext';
import { workspaceModeStore, type WorkspaceMode } from '../stores/workspaceMode';
import { activeBackendCapability } from '../design/backendSupport';
import { useCapabilities } from './useCapabilities';
import { useSolvePlan } from './useSolvePlan';
import { activeReadyMeshSubmission, useImportedSolvePlan } from './useImportedSolvePlan';

/** Controls share the submission planner; routing may depend on geometry and mounting. */
export function useSolveControlBackend(modeOverride?: WorkspaceMode) {
  const workspace = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot);
  const mode = modeOverride ?? workspace.mode;
  const design = useDesignStore((state) => state.design);
  const store = useStore(solveOptionsStoreForMode(mode), useShallow((state) => ({
    engine: state.engine, accuracy: state.accuracy,
    adaptiveFrequencySampling: state.adaptiveFrequencySampling,
    solverMode: state.solverMode, symmetry: state.symmetry,
    meshValidationMode: state.meshValidationMode, verbose: state.verbose,
    frequencySpacing: state.frequencySpacing, frequencyMode: state.frequencyMode,
    frequencyListText: state.frequencyListText, polar: state.polar,
    groundPlane: state.groundPlane, options: state.options,
  })));
  const { engines } = useCapabilities();
  const options = useMemo<SolveOptions | null>(() => {
    try { return store.options(); } catch { return null; }
  }, [store]);
  const model = useCurrentModel();
  const planningModel = mode === workspace.mode ? model
    : { kind: mode === 'parametric' ? 'parametric' as const : 'cad' as const };
  const readyMesh = activeReadyMeshSubmission(planningModel);
  const readyMeshActive = readyMesh !== null;
  const submission = readyMesh && typeof readyMesh !== 'string' && !(readyMesh instanceof Error) ? planSubmission(readyMesh) : null;
  const parametric = useSolvePlan(design, options, mode === 'parametric' && !readyMeshActive);
  const imported = useImportedSolvePlan(mode === 'cad' || submission !== null, readyMeshActive ? submission : undefined);
  const snapshot = mode === 'cad' || readyMeshActive ? imported : parametric;
  // A control may keep its last routing answer during edits; submission must
  // still use only the current plan returned by the planner hooks.
  const routingKey = `${mode}/${store.engine}/${store.accuracy}`;
  const settled = useRef<{ key: string; engine: string } | null>(null);
  useEffect(() => {
    if (snapshot.plan?.engine) settled.current = { key: routingKey, engine: snapshot.plan.engine };
  }, [snapshot.plan, routingKey]);
  const engine = snapshot.plan?.engine
    ?? (snapshot.isPending && settled.current?.key === routingKey ? settled.current.engine : null)
    ?? null;
  return {
    ...snapshot,
    capability: snapshot.error || !engine ? null : activeBackendCapability(engine, engines),
  };
}
