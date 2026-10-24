import { useEffect, useRef, useState } from 'react';
import { currentModel, type CurrentModel, currentModelStatus } from '../model/currentModel';
import { jobsSocket } from '../api/jobsSocket';
import { dismissCadJob } from './cadSolve';
import { useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useImportedModelStore } from '../stores/importedModel';
import { useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import type { EngineCapability } from './actions';

export interface ModelSolveContinuation {
  model: CurrentModel;
  signal: AbortSignal;
  /** Set at the POST boundary: later job/model changes cannot cancel this press. */
  submitted?: boolean;
}

export interface HeldSolveContinuation extends ModelSolveContinuation { jobId: string }

/** Every delayed solve holds the same model guard through its POST boundary. */
export function modelSolveCancellationReason(request: ModelSolveContinuation): string | null {
  const model = currentModel();
  if (model.key !== request.model.key || model.kind !== request.model.kind || model.ingestId !== request.model.ingestId) {
    return 'Prepare and solve was cancelled because the model on screen changed.';
  }
  return model.viewportShowing ? null : 'Prepare and solve was cancelled because the model left the viewport.';
}

export function observeSolveInputs(validate: () => void): () => void {
  const stops = [jobsSocket.subscribe(validate), useCadReturnStore.subscribe(validate),
    workspaceModeStore.subscribe(validate), importedMeshStore.subscribe(validate), useDesignStore.subscribe(validate),
    useDocumentStore.subscribe(validate), useImportedModelStore.subscribe(validate),
    useSolveOptionsStore.subscribe(validate), useImportedSolveOptionsStore.subscribe(validate)];
  return () => stops.forEach(stop => stop());
}

/** The current Fusion request held by the selected engine gate. */
export function heldEngineSolveJob() {
  const status = currentModelStatus(currentModel(), jobsSocket.getSnapshot().jobs);
  return status.kind === 'refused' && status.job?.cad_state?.state === 'needs_user_input'
    && status.job.cad_state.reason === 'engine_unavailable' ? status.job : null;
}

const PREPARATION_FAILED = 'Prepare and solve was cancelled because engine preparation failed.';

/** The same reason is used by store observers and the final guard at the POST. */
export function heldSolveCancellationReason(request: HeldSolveContinuation): string | null {
  const reason = modelSolveCancellationReason(request);
  if (reason) return reason;
  const model = currentModel();
  const job = currentModelStatus(model, jobsSocket.getSnapshot().jobs).job;
  if (job?.id !== request.jobId || job.status !== 'error' || job.cancellation_requested
    || job.cad_state?.state !== 'needs_user_input' || job.cad_state.reason !== 'engine_unavailable') {
    return 'Prepare and solve was cancelled because the waiting job changed.';
  }
  return null;
}

/** Checked again synchronously at the POST, after all asynchronous reads. */
export function heldSolveStillCurrent(request: HeldSolveContinuation): boolean {
  return !request.signal.aborted && heldSolveCancellationReason(request) === null;
}

type Intention = HeldSolveContinuation & {
  controller: AbortController;
  engine: string;
  prepared: boolean;
  continuing: boolean;
};

/** A warming held request carries its existing solve intention; other gates need an explicit choice. */
export function useHeldSolvePreparation(model: CurrentModel, engines: readonly EngineCapability[],
  prepare: (engine: string) => Promise<boolean>, continueRun: (request: HeldSolveContinuation) => Promise<unknown>,
  reportNotice: (message: string | null) => void, warmingEngine: string | null = null) {
  const [held, setHeld] = useState<Intention | null>(null);
  const intention = useRef<Intention | null>(null);
  // Stop, failure or a model change must not re-arm the same Fusion request.
  const autoStarted = useRef(new Set<string>());
  const report = useRef(reportNotice);
  report.current = reportNotice;
  const cancel = (reason?: string) => {
    if (!intention.current) return;
    const request = intention.current;
    request.controller.abort();
    intention.current = null;
    setHeld(null);
    if (reason && !request.submitted) report.current(reason);
  };
  useEffect(() => {
    // Observe every transition, including leave/return within one React batch.
    const validate = () => {
      if (!intention.current) return;
      const reason = heldSolveCancellationReason(intention.current);
      if (reason) cancel(reason);
    };
    const unsubscribe = observeSolveInputs(validate);
    return () => {
      unsubscribe();
      intention.current?.controller.abort();
      intention.current = null;
    };
  }, []);
  useEffect(() => {
    if (!held || intention.current !== held) return;
    const reason = heldSolveCancellationReason(held);
    if (reason) { cancel(reason); return; }
    const engine = engines.find(engine => engine.name === held.engine);
    if (!held.prepared) return;
    if (engine?.available && engine.runtime_state === 'ready') {
      if (held.continuing) return;
      held.continuing = true; // consume once, retain the token until POST finishes
      void continueRun(held).catch(() => undefined).finally(() => {
        if (intention.current === held) cancel();
      });
    } else if (engine?.runtime_state !== 'preparing') cancel(PREPARATION_FAILED);
  }, [held, model.key, model.kind, model.viewportShowing, engines, continueRun]);
  const prepareAndSolve = async (engine: string, alreadyWarming = false) => {
    if (intention.current) return;
    const model = currentModel();
    const job = heldEngineSolveJob();
    if (!model.key || !model.viewportShowing || !job) return;
    const controller = new AbortController();
    const request: Intention = { model, jobId: job.id, engine, controller, signal: controller.signal,
      prepared: false, continuing: false };
    intention.current = request;
    report.current(null);
    setHeld(request);
    let success = false;
    try { success = alreadyWarming || engines.some(item => item.name === engine && item.runtime_state === 'preparing') || await prepare(engine); }
    finally {
      if (intention.current === request) {
        const reason = heldSolveCancellationReason(request);
        if (reason || !success) cancel(reason ?? PREPARATION_FAILED);
        else {
          const ready = { ...request, prepared: true };
          intention.current = ready;
          setHeld(ready);
        }
      }
    }
  };
  const jobId = heldEngineSolveJob()?.id;
  useEffect(() => {
    if (!jobId || !warmingEngine || !model.viewportShowing) return;
    const key = `${jobId}:${warmingEngine}`;
    if (autoStarted.current.has(key)) return;
    // The plan and capabilities refresh independently. Join an actual warm-up,
    // or a warm-up that has just finished, without issuing another prepare POST.
    const engine = engines.find(item => item.name === warmingEngine);
    if (engine?.runtime_state !== 'preparing' && engine?.runtime_state !== 'ready') return;
    autoStarted.current.add(key);
    void prepareAndSolve(warmingEngine, true);
  }, [jobId, warmingEngine, model.key, model.kind, model.viewportShowing, engines]);
  return { prepareAndSolve, waitingEngine: held && !held.submitted ? held.engine : null, stop: () => {
    const request = intention.current;
    const job = heldEngineSolveJob();
    cancel();
    if (request && !request.submitted && job?.id === request.jobId) {
      void dismissCadJob(job).catch(error => report.current(error instanceof Error ? error.message : String(error)));
    }
  } };

}
