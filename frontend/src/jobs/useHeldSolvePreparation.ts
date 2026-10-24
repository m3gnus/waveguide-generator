import { currentModel, currentModelStatus } from '../model/currentModel';
import { jobsSocket } from '../api/jobsSocket';
import { dismissCadJob } from './cadSolve';
import type { EngineCapability } from './actions';
import type { RequestedSolveOwner } from './useRequestedSolve';

/** The current Fusion request held by the selected engine gate. */
export function heldEngineSolveJob() {
  const status = currentModelStatus(currentModel(), jobsSocket.getSnapshot().jobs);
  return (status.kind === 'waiting' || status.kind === 'refused') && status.job?.cad_state?.state === 'needs_user_input'
    && status.job.cad_state.reason === 'engine_unavailable' ? status.job : null;
}

/** Translate Fusion facts into events. Acceptance/deduplication belongs to the reducer. */
export function useHeldSolvePreparation(owner: RequestedSolveOwner, engines: readonly EngineCapability[],
  reportNotice: (message: string | null) => void) {
  const prepareAndSolve = async (engine: string) => {
    const job = heldEngineSolveJob();
    if (!currentModel().viewportShowing || !job) return;
    await owner.fusion(job.id, engine, !engines.some(item => item.name === engine && item.runtime_state === 'preparing'));
  };
  return { prepareAndSolve, stop: () => {
    const request = owner.state.active;
    const job = heldEngineSolveJob();
    owner.cancel();
    if (request?.jobId && owner.state.phase !== 'submitting' && job?.id === request.jobId) {
      void dismissCadJob(job).catch(error => reportNotice(error instanceof Error ? error.message : String(error)));
    }
  } };
}
