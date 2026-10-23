import { useEffect, useRef, useState } from 'react';
import { currentModel, type CurrentModel, currentModelStatus } from '../model/currentModel';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import type { EngineCapability } from './actions';

/** Only this explicit intention may continue after runtime preparation. */
export function useHeldSolvePreparation(model: CurrentModel, engines: readonly EngineCapability[],
  prepare: (engine: string) => Promise<boolean>, continueRun: (id: string) => Promise<unknown>) {
  const [held, setHeld] = useState<{ key: string; kind: string; jobId: string; engine: string; prepared: boolean } | null>(null);
  const intention = useRef(held);
  const cancel = () => { intention.current = null; setHeld(null); };
  useEffect(() => {
    if (!held) return;
    if (model.key !== held.key || model.kind !== held.kind || !model.viewportShowing) { cancel(); return; }
    const job = jobsSocket.getSnapshot().jobs.find(job => job.id === held.jobId);
    if (job?.cad_state?.state !== 'needs_user_input') { cancel(); return; }
    const engine = engines.find(engine => engine.name === held.engine);
    if (!held.prepared) return;
    if (engine?.available && engine.runtime_state === 'ready') {
      cancel(); // consume before continuation; rerenders cannot replay it
      void continueRun(held.jobId).catch(() => undefined);
    } else if (engine?.runtime_state !== 'preparing') cancel();
  }, [held, model.key, model.kind, model.viewportShowing, engines, continueRun]);
  useEffect(() => () => { intention.current = null; }, []);
  return async (engine: string) => {
    if (intention.current) return;
    const model = currentModel();
    const status = currentModelStatus(model, jobsSocket.getSnapshot().jobs);
    const job: JobItem | undefined = status.job;
    if (!model.key || !model.viewportShowing || status.kind !== 'refused' || job?.cad_state?.state !== 'needs_user_input'
      || job.cad_state.reason !== 'engine_unavailable') return;
    const request = { key: model.key, kind: model.kind, jobId: job.id, engine, prepared: false };
    intention.current = request;
    setHeld(request);
    const success = await prepare(engine);
    if (intention.current !== request) return;
    if (!success || currentModel().key !== request.key || !currentModel().viewportShowing) { cancel(); return; }
    const ready = { ...request, prepared: true };
    intention.current = ready;
    setHeld(ready);
  };
}
