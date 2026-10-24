import { useEffect, useRef } from 'react';
import { currentModel, currentModelStatus, type CurrentModel } from '../model/currentModel';
import { jobsSocket } from '../api/jobsSocket';
import { dismissCadJob } from './cadSolve';
import type { EngineCapability } from './actions';
import { captureSolveInputs, type HeldSolveContinuation, type RequestedSolveOwner } from './useRequestedSolve';

/** The current Fusion request held by the selected engine gate. */
export function heldEngineSolveJob() {
  const status = currentModelStatus(currentModel(), jobsSocket.getSnapshot().jobs);
  return status.kind === 'refused' && status.job?.cad_state?.state === 'needs_user_input'
    && status.job.cad_state.reason === 'engine_unavailable' ? status.job : null;
}

const PREPARATION_FAILED = 'Prepare and solve was cancelled because engine preparation failed.';

/** Fusion adapts its saved request to the same owner as an ordinary Solve. */
export function useHeldSolvePreparation(owner: RequestedSolveOwner, model: CurrentModel, engines: readonly EngineCapability[],
  prepare: (engine: string) => Promise<boolean>, continueRun: (request: HeldSolveContinuation) => Promise<unknown>,
  reportNotice: (message: string | null) => void, warmingEngine: string | null = null) {
  const autoStarted = useRef(new Set<string>());
  const held = owner.request?.jobId ? owner.request : null;
  useEffect(() => {
    const token = owner.current.current;
    if (!held || !token?.jobId || token.preparing) return;
    const engine = engines.find(engine => engine.name === held.preparationEngine);
    if (engine?.available && engine.runtime_state === 'ready') {
      if (token.continuing) return;
      owner.update(token, { continuing: true });
      void continueRun(token as HeldSolveContinuation).catch(() => undefined).finally(() => owner.finish(token));
    } else if (engine?.runtime_state === 'failed' || engine?.runtime_state === 'unavailable') owner.cancel(PREPARATION_FAILED);
  }, [held, engines, continueRun, owner.update, owner.finish, owner.cancel]);
  const prepareAndSolve = async (engine: string, alreadyWarming = false) => {
    if (owner.current.current?.waiting || owner.current.current?.continuing) return;
    const model = currentModel();
    const job = heldEngineSolveJob();
    if (!model.key || !model.viewportShowing || !job) return;
    const token = owner.begin(job.id);
    autoStarted.current.add(`${job.id}:${engine}`);
    owner.update(token, { waiting: true, preparing: true, preparationEngine: engine });
    let success = false;
    try { success = alreadyWarming || engines.some(item => item.name === engine && item.runtime_state === 'preparing') || await prepare(engine); }
    finally {
      if (owner.current.current === token) {
        if (!success) owner.cancel(PREPARATION_FAILED);
        else owner.update(token, { preparing: false });
      }
    }
  };
  const jobId = heldEngineSolveJob()?.id;
  useEffect(() => {
    if (!jobId || !warmingEngine || !model.viewportShowing) return;
    const key = `${jobId}:${warmingEngine}`;
    if (autoStarted.current.has(key)) return;
    const selected = captureSolveInputs().engine;
    if (selected !== 'auto' && selected !== 'beat' && selected !== warmingEngine) return;
    autoStarted.current.add(key);
    void prepareAndSolve(warmingEngine, true);
  }, [jobId, warmingEngine, model.key, model.kind, model.viewportShowing, engines]);
  return { prepareAndSolve, stop: () => {
    const token = owner.current.current;
    const job = heldEngineSolveJob();
    owner.cancel();
    if (token?.jobId && !token.submitted && job?.id === token.jobId) {
      void dismissCadJob(job).catch(error => reportNotice(error instanceof Error ? error.message : String(error)));
    }
  } };
}
