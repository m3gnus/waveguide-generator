import { useEffect, useRef, useState } from 'react';
import { currentModel, type CurrentModel, currentModelStatus } from '../model/currentModel';
import { jobsSocket } from '../api/jobsSocket';
import { useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import type { EngineCapability } from './actions';

export interface HeldSolveContinuation {
  jobId: string;
  model: CurrentModel;
  signal: AbortSignal;
}

/** Checked again synchronously at the POST, after all asynchronous reads. */
export function heldSolveStillCurrent(request: HeldSolveContinuation): boolean {
  const model = currentModel();
  const job = currentModelStatus(model, jobsSocket.getSnapshot().jobs).job;
  return !request.signal.aborted && model.key === request.model.key && model.kind === request.model.kind
    && model.ingestId === request.model.ingestId && model.viewportShowing
    && job?.id === request.jobId && job.status === 'error' && !job.cancellation_requested
    && job.cad_state?.state === 'needs_user_input'
    && job.cad_state.reason === 'engine_unavailable';
}

type Intention = HeldSolveContinuation & {
  controller: AbortController;
  engine: string;
  prepared: boolean;
  continuing: boolean;
};

/** Only this explicit intention may continue after runtime preparation. */
export function useHeldSolvePreparation(model: CurrentModel, engines: readonly EngineCapability[],
  prepare: (engine: string) => Promise<boolean>, continueRun: (request: HeldSolveContinuation) => Promise<unknown>) {
  const [held, setHeld] = useState<Intention | null>(null);
  const intention = useRef<Intention | null>(null);
  const cancel = () => {
    intention.current?.controller.abort();
    intention.current = null;
    setHeld(null);
  };
  useEffect(() => {
    // Observe every transition, including leave/return within one React batch.
    const validate = () => {
      if (intention.current && !heldSolveStillCurrent(intention.current)) cancel();
    };
    const unsubscribe = [jobsSocket.subscribe(validate), useCadReturnStore.subscribe(validate),
      workspaceModeStore.subscribe(validate), importedMeshStore.subscribe(validate)];
    return () => {
      unsubscribe.forEach(stop => stop());
      intention.current?.controller.abort();
      intention.current = null;
    };
  }, []);
  useEffect(() => {
    if (!held || intention.current !== held) return;
    if (!heldSolveStillCurrent(held)) { cancel(); return; }
    const engine = engines.find(engine => engine.name === held.engine);
    if (!held.prepared) return;
    if (engine?.available && engine.runtime_state === 'ready') {
      if (held.continuing) return;
      held.continuing = true; // consume once, retain the token until POST finishes
      void continueRun(held).catch(() => undefined).finally(() => {
        if (intention.current === held) cancel();
      });
    } else if (engine?.runtime_state !== 'preparing') cancel();
  }, [held, model.key, model.kind, model.viewportShowing, engines, continueRun]);
  return async (engine: string) => {
    if (intention.current) return;
    const model = currentModel();
    const status = currentModelStatus(model, jobsSocket.getSnapshot().jobs);
    const job = status.job;
    if (!model.key || !model.viewportShowing || status.kind !== 'refused' || job?.cad_state?.state !== 'needs_user_input'
      || job.cad_state.reason !== 'engine_unavailable') return;
    const controller = new AbortController();
    const request: Intention = { model, jobId: job.id, engine, controller, signal: controller.signal,
      prepared: false, continuing: false };
    intention.current = request;
    setHeld(request);
    let success = false;
    try { success = await prepare(engine); }
    finally {
      if (intention.current === request) {
        if (!success || !heldSolveStillCurrent(request)) cancel();
        else {
          const ready = { ...request, prepared: true };
          intention.current = ready;
          setHeld(ready);
        }
      }
    }
  };
}
