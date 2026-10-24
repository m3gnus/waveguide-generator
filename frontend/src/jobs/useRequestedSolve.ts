import { useCallback, useEffect, useRef, useState } from 'react';
import { currentModel, type CurrentModel } from '../model/currentModel';
import { jobsSocket } from '../api/jobsSocket';
import { previewSocket } from '../api/previewSocket';
import { useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { currentDocumentLoad, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useImportedModelStore } from '../stores/importedModel';
import { useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { useMeshImportStore, fileMeshSetup } from '../stores/meshImport';
import { subscribeSolveSettingsEdits } from '../stores/solveSettingsEdits';
import { buildImportedSubmission } from './importedSubmission';
import type { EngineCapability } from './actions';
import { initialRequestedSolve, reduceRequestedSolve, type RequestedSolveEvent, type SolveRequest } from './requestedSolveMachine';

export interface ModelSolveContinuation {
  model: CurrentModel;
  engine: string;
  mode: string;
  setupRevision: string;
  signal: AbortSignal;
  requestId?: number;
  checkCadPlan?: boolean;
}
export interface HeldSolveContinuation extends ModelSolveContinuation { jobId: string }
type SolveInputs = Omit<ModelSolveContinuation, 'signal' | 'requestId'>;
export type RequestedSolve = ModelSolveContinuation & { requestId: number; jobId?: string };

/** Abort only this request's wait; the shared frame read can finish for its other consumers. */
export async function awaitSolveStep<T>(step: Promise<T>, token: ModelSolveContinuation | null): Promise<T> {
  if (!token) return step;
  const { signal } = token;
  if (signal.aborted) throw new DOMException('Solve cancelled', 'AbortError');
  return new Promise<T>((resolve, reject) => {
    const abort = () => { reject(new DOMException('Solve cancelled', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true });
    step.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

function solveInputs() {
  const model = currentModel();
  const options = model.kind === 'parametric' ? useSolveOptionsStore.getState() : useImportedSolveOptionsStore.getState();
  const imported = useImportedModelStore.getState();
  let setup: unknown = null;
  // Reuse the submission builders: setup identity includes retained physics,
  // not just the subset of settings the current controls can express.
  try {
    setup = model.kind === 'file' ? fileMeshSetup()
      : model.kind === 'cad' ? buildImportedSubmission(useCadReturnStore.getState()) : null;
  } catch { /* An incomplete edit is also caught by the control snapshot/edit signal. */ }
  const frame = model.kind === 'cad' && model.ingestId ? useCadSolverFrameStore.getState().frames[model.ingestId] : null;
  return { model, engine: options.engine, mode: workspaceModeStore.getSnapshot().mode,
    setupRevision: JSON.stringify({ options, setup, framePick: frame?.picked ? frame.axis : null,
      revision: model.kind === 'parametric' ? [currentDocumentLoad(), useDesignStore.getState().designRevision]
        : [imported.restoredRevisionId, imported.setup, imported.setupEditBase] }) };
}

export function captureSolveInputs(): SolveInputs { return solveInputs(); }

/** The one live-input guard used by observers and by every delayed POST. */
export function solveCancellationReason(request: SolveInputs & { jobId?: string }): string | null {
  const inputs = solveInputs();
  const model = inputs.model;
  if (model.key !== request.model.key || model.kind !== request.model.kind || model.ingestId !== request.model.ingestId
    || inputs.mode !== request.mode) return 'Prepare and solve was cancelled because the model on screen changed.';
  if (!model.viewportShowing) return 'Prepare and solve was cancelled because the model left the viewport.';
  if (inputs.engine !== request.engine) return 'Prepare and solve was cancelled because the selected engine changed.';
  if (inputs.setupRevision !== request.setupRevision) return 'Prepare and solve was cancelled because the solve setup changed.';
  if (request.jobId) {
    const job = jobsSocket.getSnapshot().jobs.find(job => job.id === request.jobId);
    if (job?.id !== request.jobId || job.status !== 'error' || job.cancellation_requested
      || job.cad_state?.state !== 'needs_user_input' || job.cad_state.reason !== 'engine_unavailable') {
      return 'Prepare and solve was cancelled because the waiting job changed.';
    }
  }
  return null;
}

interface SolveReadiness {
  capabilityGeneration: number;
  capabilityIssueSequence?: () => number;
  enabled: boolean;
  busy: boolean;
  pending: boolean;
  blocked: boolean;
  warmingEngine: string | null;
  error: string | null;
  engines: readonly EngineCapability[];
  heldFusion: { jobId: string; engine: string } | null;
  prepare: (engine: string) => Promise<boolean>;
  resume: (token: RequestedSolve) => Promise<unknown>;
}

function requestPlan(readiness: SolveReadiness) {
  return {
    capabilityGeneration: readiness.capabilityGeneration,
    ordinary: readiness.blocked ? 'blocked' as const : readiness.pending ? 'pending' as const : readiness.enabled ? 'ready' as const : 'disabled' as const,
    busy: readiness.busy, warmingEngine: readiness.warmingEngine, engines: readiness.engines, error: readiness.error,
  };
}

/** React is an interpreter for the pure owner: stores dispatch facts, effects do IO. */
export function useRequestedSolve(refresh: () => void | Promise<void>, reportNotice: (message: string | null) => void,
  releaseAttention: () => void, readiness: SolveReadiness) {
  const machine = useRef(initialRequestedSolve<SolveInputs>());
  const [state, setState] = useState(machine.current);
  const plan = requestPlan(readiness);
  // Observe the arrival of an intention, not every render of its readiness.
  // Terminal requests may be retried, but an unchanged held observation is no new request.
  const observedFusion = useRef<string | null>(null);
  const services = useRef({ readiness, plan, reportNotice, releaseAttention, refresh });
  services.current = { readiness, plan, reportNotice, releaseAttention, refresh };
  const observationTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const executions = useRef(new Map<number, { token: RequestedSolve; controller: AbortController }>());
  const tasks = useRef(new Map<number, Promise<unknown>>());
  const preparations = useRef(new Map<number, Promise<void>>());
  const tokenFor = useCallback((request: SolveRequest<SolveInputs>) => {
    let token = executions.current.get(request.id)?.token;
    if (!token) {
      const controller = new AbortController();
      token = { ...request.inputs, requestId: request.id, jobId: request.jobId, signal: controller.signal };
      executions.current.set(request.id, { token, controller });
    }
    return token;
  }, []);
  const dispatch = useCallback(function dispatch(event: RequestedSolveEvent<SolveInputs>) {
    const result = reduceRequestedSolve(machine.current, {
      ...event, capabilityIssueSequence: services.current.readiness.capabilityIssueSequence?.(),
    });
    machine.current = result.state; // Claim ownership before any effect or synchronous store event.
    if (result.state.mounted) setState(result.state);
    // Allocate cancellation resources before effects can synchronously notify stores.
    for (const effect of result.effects) if (effect.type === 'continue') tokenFor(effect.request);
    for (const effect of result.effects) {
      const io = services.current;
      switch (effect.type) {
        case 'notice': io.reportNotice(effect.message); break;
        case 'release': io.releaseAttention(); break;
        case 'abort': executions.current.get(effect.id)?.controller.abort(); break;
        case 'observe-start': {
          let refreshing = false;
          const observe = async () => {
            if (refreshing) return;
            refreshing = true;
            try { await services.current.refresh(); } catch { /* A transient network failure does not end the request. */ }
            finally { refreshing = false; }
          };
          void observe();
          observationTimer.current = setInterval(() => { void observe(); }, 1000);
          break;
        }
        case 'observe-stop':
          if (observationTimer.current !== null) clearInterval(observationTimer.current);
          observationTimer.current = null;
          break;
        case 'prepare': {
          const task = (async () => {
            let success = false;
            try { success = await io.readiness.prepare(effect.request.preparationEngine!); }
            finally { dispatch({ type: success ? 'preparation-done' : 'preparation-failed', id: effect.request.id }); }
          })();
          preparations.current.set(effect.request.id, task);
          void task.finally(() => preparations.current.delete(effect.request.id)).catch(() => undefined);
          break;
        }
        case 'continue': {
          const token = tokenFor(effect.request);
          const task = (async () => {
            try {
              const value = await io.readiness.resume(token);
              dispatch({ type: machine.current.active?.id === token.requestId && machine.current.phase === 'submitting' ? 'post-succeeded' : 'continuation-settled', id: token.requestId });
              return value;
            } catch (error) {
              dispatch({ type: machine.current.active?.id === token.requestId && machine.current.phase === 'submitting' ? 'post-failed' : 'continuation-settled', id: token.requestId, message: error instanceof Error ? error.message : String(error) });
              return 'blocked';
            } finally {
              tasks.current.delete(token.requestId);
              executions.current.delete(token.requestId);
            }
          })();
          tasks.current.set(token.requestId, task);
          break;
        }
        case 'post': break; // The synchronous POST boundary consumes this authorization below.
      }
    }
    return result;
  }, [tokenFor]);
  const beforePost = useCallback((token: ModelSolveContinuation | null): boolean => {
    if (!token) return true;
    if (token.signal.aborted) return false;
    const reason = solveCancellationReason(token);
    if (token.requestId === undefined) return reason === null;
    if (reason) dispatch({ type: 'inputs-changed', ids: [token.requestId], reason });
    return dispatch({ type: 'frame-resolved', id: token.requestId }).effects.some(effect => effect.type === 'post');
  }, [dispatch]);
  const cancel = useCallback(() => { dispatch({ type: 'stop' }); }, [dispatch]);
  const press = useCallback(async (prepareEngine?: string, checkCadPlan = false) => {
    dispatch({ type: 'plan-ready', plan: services.current.plan });
    const result = dispatch({ type: 'press', inputs: { ...captureSolveInputs(), checkCadPlan }, prepareEngine });
    const active = result.state.active;
    if (!active) return 'blocked' as const;
    if (prepareEngine) { await preparations.current.get(active.id); return 'pending' as const; }
    const task = tasks.current.get(active.id);
    if (task) {
      const value = await task;
      return value === 'blocked' ? 'blocked' as const : value === 'busy' ? 'busy' as const : 'submitted' as const;
    }
    return active.preparationEngine || result.state.plan.ordinary === 'pending' ? 'pending' as const : 'blocked' as const;
  }, [dispatch]);
  const fusion = useCallback(async (jobId: string, engine: string, prepare = false) => {
    const result = dispatch({ type: 'fusion-request', inputs: captureSolveInputs(), jobId, engine, prepare, plan: services.current.plan });
    if (result.state.active?.jobId === jobId) await preparations.current.get(result.state.active.id);
  }, [dispatch]);
  useEffect(() => {
    dispatch({ type: 'mount' });
    const validate = () => {
      const state = machine.current;
      const requests = [...state.queue, ...(state.active && state.phase !== 'submitting' ? [state.active] : [])];
      for (const request of requests) {
        const reason = solveCancellationReason({ ...request.inputs, jobId: request.jobId });
        if (reason) dispatch({ type: 'inputs-changed', ids: [request.id], reason: request.jobId ? reason : undefined });
      }
    };
    const stops = [jobsSocket.subscribe(validate), previewSocket.subscribe(validate), useCadReturnStore.subscribe(validate), useCadSolverFrameStore.subscribe(validate),
      workspaceModeStore.subscribe(validate), importedMeshStore.subscribe(validate), useDesignStore.subscribe(validate),
      useDocumentStore.subscribe(validate), useImportedModelStore.subscribe(validate), useMeshImportStore.subscribe(validate),
      useSolveOptionsStore.subscribe(validate), useImportedSolveOptionsStore.subscribe(validate),
      subscribeSolveSettingsEdits(edit => {
        const state = machine.current;
        const requests = [...state.queue, ...(state.active && state.phase !== 'submitting' ? [state.active] : [])];
        const edited = requests.filter(request => (edit?.source === 'solveOptions') === (request.inputs.model.kind === 'parametric'));
        dispatch({ type: 'inputs-changed', ids: edited.map(request => request.id),
          reason: edited.some(request => request.jobId) ? 'Prepare and solve was cancelled because the solve setup changed.' : undefined });
      })];
    validate();
    return () => {
      stops.forEach(stop => stop());
      dispatch({ type: 'unmount' });
      // StrictMode replays setup synchronously. Keep its request and executor;
      // a real unmount blocks POST immediately and aborts at the microtask checkpoint.
      queueMicrotask(() => { if (!machine.current.mounted) dispatch({ type: 'stop' }); });
    };
  }, [dispatch]);
  useEffect(() => {
    const held = readiness.heldFusion;
    const inputs = captureSolveInputs();
    if (held && observedFusion.current !== held.jobId && inputs.model.viewportShowing && ['auto', 'beat', held.engine].includes(inputs.engine)) {
      observedFusion.current = held.jobId;
      dispatch({ type: 'fusion-request', inputs, ...held, plan: services.current.plan });
    } else dispatch({ type: 'plan-ready', plan: services.current.plan });
    if (!held) observedFusion.current = null;
  }, [dispatch, readiness.capabilityGeneration, readiness.busy, readiness.enabled, readiness.pending, readiness.blocked, readiness.warmingEngine, readiness.engines, readiness.error, readiness.heldFusion?.jobId, readiness.heldFusion?.engine]);
  return { state, press, fusion, cancel, beforePost };
}
export type RequestedSolveOwner = ReturnType<typeof useRequestedSolve>;
