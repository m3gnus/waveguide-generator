import { useEffect, useRef } from 'react';
import { currentModel, currentModelStatus, type CurrentModel } from '../model/currentModel';
import { jobsSocket } from '../api/jobsSocket';
import { dismissCadJob } from './cadSolve';
import type { EngineCapability } from './actions';
import { captureSolveInputs, type RequestedSolveOwner } from './useRequestedSolve';

/** The current Fusion request held by the selected engine gate. */
export function heldEngineSolveJob() {
  const status = currentModelStatus(currentModel(), jobsSocket.getSnapshot().jobs);
  return status.kind === 'refused' && status.job?.cad_state?.state === 'needs_user_input'
    && status.job.cad_state.reason === 'engine_unavailable' ? status.job : null;
}

/** Fusion adapts its saved request to the same owner as an ordinary Solve. */
export function useHeldSolvePreparation(owner: RequestedSolveOwner, model: CurrentModel, engines: readonly EngineCapability[],
  prepare: (engine: string) => Promise<boolean>,
  reportNotice: (message: string | null) => void, warmingEngine: string | null = null) {
  const autoStarted = useRef(new Set<string>());
  const prepareAndSolve = async (engine: string, alreadyWarming = false) => {
    const existing = owner.current.current;
    if (existing?.continuing || existing?.submitted) return;
    const model = currentModel();
    const job = heldEngineSolveJob();
    if (!model.key || !model.viewportShowing || !job) return;
    if (existing?.jobId === job.id && existing.waiting) return;
    const token = owner.begin(job.id);
    autoStarted.current.add(`${job.id}:${engine}`);
    await owner.prepare(token, engine, async () => alreadyWarming
      || engines.some(item => item.name === engine && item.runtime_state === 'preparing') || await prepare(engine));
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
