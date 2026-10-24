/** Pure request ownership. Async work may only advance the matching request ID. */
export type SolvePhase = 'idle' | 'waiting-readiness' | 'continuing' | 'cancelling' | 'submitting' | 'done' | 'cancelled';
export interface RequestPlan {
  /** Issue sequence of the capability fetch, independent of response arrival. */
  capabilityGeneration: number;
  ordinary: 'ready' | 'pending' | 'blocked' | 'disabled';
  warmingEngine: string | null;
  engines: readonly { name: string; available: boolean; runtime_state?: string | null }[];
  error: string | null;
  busy: boolean;
}
export interface SolveRequest<T> {
  id: number;
  acceptedCapabilityGeneration: number;
  inputs: T;
  jobId?: string;
  preparationEngine?: string;
  preparation: 'none' | 'pending' | 'done';
  /** The user action that asked for this solve: the explicit-navigation
   * count captured once, at the press (`solveAttention.armSolve`). Every
   * continuation, readiness wait and submission inherits it and every failure
   * reports with it; it is never captured again. A later press that joins
   * this request (it is already active or queued) is the user's latest
   * action, so its token replaces this one. Absent for a request nobody
   * pressed (a Fusion request observed arriving). */
  attention?: number;
}
export interface RequestedSolveState<T> {
  phase: SolvePhase;
  active: SolveRequest<T> | null;
  queue: readonly SolveRequest<T>[];
  nextId: number;
  mounted: boolean;
  plan: RequestPlan;
}
export type RequestedSolveEvent<T> = (
  | { type: 'press'; inputs: T; prepareEngine?: string; attention?: number }
  | { type: 'fusion-request'; inputs: T; jobId: string; engine: string; prepare?: boolean; plan?: RequestPlan; attention?: number }
  | { type: 'plan-ready'; plan: RequestPlan }
  | { type: 'preparation-done' | 'preparation-failed' | 'frame-resolved' | 'post-succeeded' | 'post-failed' | 'continuation-settled'; id: number; message?: string }
  | { type: 'inputs-changed'; ids: readonly number[]; reason?: string }
  | { type: 'stop' | 'unmount' | 'mount' }
) & {
  /** Latest issued fetch at acceptance/preparation completion, including in-flight work. */
  capabilityIssueSequence?: number;
};
export type RequestedSolveEffect<T> =
  | { type: 'continue' | 'prepare'; request: SolveRequest<T> }
  | { type: 'abort' | 'post'; id: number }
  /** `attention`: the action the message reports on (`SolveRequest.attention`).
   * `indicate`: the user's own edit replaced that action, so the message may
   * only be indicated, never move the dock. */
  | { type: 'notice'; message: string | null; attention?: number; indicate?: boolean }
  | { type: 'release' | 'observe-start' | 'observe-stop' };
export const PREPARATION_FAILED = 'Prepare and solve was cancelled because engine preparation failed.';
export function initialRequestedSolve<T>(): RequestedSolveState<T> {
  return { phase: 'idle', active: null, queue: [], nextId: 1, mounted: true,
    plan: { capabilityGeneration: 0, ordinary: 'pending', warmingEngine: null, engines: [], error: null, busy: false } };
}
export function reduceRequestedSolve<T>(previous: RequestedSolveState<T>, event: RequestedSolveEvent<T>):
  { state: RequestedSolveState<T>; effects: RequestedSolveEffect<T>[] } {
  let state = previous;
  const effects: RequestedSolveEffect<T>[] = [];
  const set = (patch: Partial<RequestedSolveState<T>>) => { state = { ...state, ...patch }; };
  const activate = (request: SolveRequest<T>) => {
    set({ active: request, phase: 'waiting-readiness' });
    effects.push({ type: 'notice', message: null });
    if (request.preparation === 'pending') effects.push({ type: 'prepare', request });
  };
  const advance = (phase: 'done' | 'cancelled') => {
    set({ active: null, phase });
    if (state.mounted && state.queue.length) {
      const [next, ...queue] = state.queue;
      set({ queue });
      activate(next);
    }
  };
  /** `ownEdit`: the user's own edit replaced the press's intention, so its
   * notice is indicated, never revealed. */
  const cancel = (reason?: string, ownEdit = false) => {
    const active = state.active;
    if (!active || state.phase === 'submitting' || state.phase === 'cancelling') return;
    effects.push({ type: 'abort', id: active.id }, { type: 'release' });
    if (reason) effects.push({ type: 'notice', message: reason, attention: active.attention, ...(ownEdit ? { indicate: true } : {}) });
    // Wait for the aborted executor to release the shared submission mutex.
    if (state.phase === 'continuing') set({ phase: 'cancelling' });
    else advance('cancelled');
  };
  const updatePlan = (plan: RequestPlan) => {
    const a = state.plan;
    if (plan.capabilityGeneration < a.capabilityGeneration) return;
    // Older/equal snapshot identities cannot replace newer capability evidence.
    const b = plan.capabilityGeneration > a.capabilityGeneration ? plan
      : { ...plan, engines: a.engines, capabilityGeneration: a.capabilityGeneration };
    if (a.capabilityGeneration !== b.capabilityGeneration || a.ordinary !== b.ordinary || a.warmingEngine !== b.warmingEngine || a.error !== b.error || a.busy !== b.busy
      || a.engines.length !== b.engines.length || a.engines.some((engine, index) => {
        const next = b.engines[index];
        return engine.name !== next.name || engine.available !== next.available || engine.runtime_state !== next.runtime_state;
      })) set({ plan: b });
  };
  // A user action joining an existing request gives it its identity: the
  // latest press is what the user holds now. Nobody's arrival changes nothing.
  const adopt = (request: SolveRequest<T>): SolveRequest<T> => (event.type === 'press' || event.type === 'fusion-request')
    && event.attention !== undefined && request.attention !== event.attention
    ? { ...request, attention: event.attention } : request;
  switch (event.type) {
    case 'mount':
      if (!state.mounted) {
        set({ mounted: true });
        if (!state.active && state.queue.length) advance('done');
      }
      break;
    case 'press':
    case 'fusion-request': {
      if (!state.mounted) break;
      const fusion = event.type === 'fusion-request';
      if (fusion && event.plan) updatePlan(event.plan);
      if (fusion && (state.active?.jobId === event.jobId || state.queue.some(request => request.jobId === event.jobId))) {
        if (!event.prepare) {
          // The same request, pressed again: it carries this press's token.
          if (state.active?.jobId === event.jobId) set({ active: adopt(state.active) });
          else set({ queue: state.queue.map(request => request.jobId === event.jobId ? adopt(request) : request) });
          break;
        }
        if (state.active?.jobId === event.jobId) {
          set({ active: adopt(state.active) });
          if (state.phase === 'waiting-readiness' && state.active.preparation !== 'pending') {
            const request = { ...state.active, preparation: 'pending' as const, preparationEngine: event.engine };
            set({ active: request });
            effects.push({ type: 'prepare', request });
          }
          break;
        }
        if (state.queue.some(request => request.jobId === event.jobId)) {
          set({ queue: state.queue.map(request => request.jobId === event.jobId
            ? { ...adopt(request), preparation: 'pending', preparationEngine: event.engine } : request) });
          break;
        }
      }
      // A press while a request is being cancelled is a new request, queued
      // behind the cancellation; any other press joins the active request,
      // which carries its token from now on.
      if (!fusion && state.active && state.phase !== 'cancelling') {
        set({ active: adopt(state.active) });
        if (event.prepareEngine && state.phase === 'waiting-readiness' && state.active.preparation !== 'pending') {
          const request = { ...state.active, preparation: 'pending' as const, preparationEngine: event.prepareEngine };
          set({ active: request });
          effects.push({ type: 'prepare', request });
        }
        break;
      }
      const request: SolveRequest<T> = { id: state.nextId, acceptedCapabilityGeneration: Math.max(event.capabilityIssueSequence ?? 0, state.plan.capabilityGeneration), inputs: event.inputs,
        ...(fusion ? { jobId: event.jobId, preparationEngine: event.engine }
          : { preparationEngine: event.prepareEngine ?? state.plan.warmingEngine ?? undefined }),
        preparation: (fusion ? event.prepare : event.prepareEngine) ? 'pending' : 'none',
        ...(event.attention !== undefined ? { attention: event.attention } : {}) };
      set({ nextId: state.nextId + 1 });
      if (!state.active) activate(request);
      else {
        set({ queue: [...state.queue, request] });
        // A Fusion request replaces ordinary work until the POST boundary.
        if (!state.active.jobId) cancel();
      }
      break;
    }
    case 'plan-ready': updatePlan(event.plan); break;
    case 'preparation-done':
      if (state.active?.id === event.id && state.phase === 'waiting-readiness' && state.active.preparation === 'pending') {
        // A successful explicit preparation supersedes any failure cached during it.
        set({ active: { ...state.active, preparation: 'done', acceptedCapabilityGeneration: Math.max(event.capabilityIssueSequence ?? 0, state.plan.capabilityGeneration) } });
      }
      break;
    case 'preparation-failed':
      if (state.active?.id === event.id && state.active.preparation === 'pending') cancel(PREPARATION_FAILED);
      break;
    // All pre-POST async steps have completed; authorize the matching POST once.
    case 'frame-resolved':
      if (state.mounted && state.active?.id === event.id && state.phase === 'continuing') {
        set({ phase: 'submitting' });
        effects.push({ type: 'post', id: event.id });
      }
      break;
    case 'continuation-settled':
      if (state.active?.id === event.id && ['continuing', 'cancelling'].includes(state.phase)) {
        if (state.phase === 'continuing') {
          effects.push({ type: 'release' });
          if (event.message && state.mounted) effects.push({ type: 'notice', message: event.message, attention: state.active.attention });
        }
        advance('cancelled');
      }
      break;
    case 'post-succeeded':
    case 'post-failed':
      if (state.active?.id === event.id && state.phase === 'submitting') {
        if (event.type === 'post-failed' && event.message && state.mounted) effects.push({ type: 'notice', message: event.message, attention: state.active.attention });
        advance('done');
      }
      break;
    case 'inputs-changed':
      set({ queue: state.queue.filter(request => !event.ids.includes(request.id)) });
      // The user's own edit replaced the intention: its notice is
      // indicated, never revealed in the press's name.
      if (state.active && event.ids.includes(state.active.id)) cancel(event.reason, true);
      break;
    // Effect cleanup suspends ownership. A replay mounts the same live request;
    // the adapter sends stop after cleanup only if no remount followed.
    case 'unmount':
      if (state.mounted) set({ mounted: false });
      break;
    case 'stop':
      set({ queue: [] });
      cancel();
      break;
  }
  // Cancellation can activate a successor. Settle that successor against the
  // same readiness fact immediately; no extra render or poll is needed.
  for (let remaining = state.queue.length + 2; remaining > 0; remaining--) {
    const request = state.active;
    if (!state.mounted || !request || !['waiting-readiness', 'continuing'].includes(state.phase) || request.preparation === 'pending') break;
    const engine = state.plan.engines.find(engine => engine.name === request.preparationEngine);
    // Failure evidence must postdate acceptance, even for queued requests.
    if (state.plan.capabilityGeneration > request.acceptedCapabilityGeneration && request.preparationEngine && (engine?.runtime_state === 'failed' || engine?.runtime_state === 'unavailable')) cancel(PREPARATION_FAILED);
    else if (state.phase === 'waiting-readiness') {
      if (!request.jobId && !!request.preparationEngine && request.preparation === 'none' && state.plan.ordinary === 'blocked' && !state.plan.warmingEngine) cancel();
      else if (!request.jobId && !state.plan.busy && state.plan.ordinary === 'disabled') cancel(state.plan.error ?? undefined);
      else {
        if (!request.jobId && state.plan.warmingEngine && request.preparationEngine !== state.plan.warmingEngine) {
          set({ active: { ...request, preparationEngine: state.plan.warmingEngine } });
        }
        const ready = request.jobId ? engine?.available && engine.runtime_state === 'ready'
          : state.plan.ordinary === 'ready' && !state.plan.warmingEngine;
        if (ready && !state.plan.busy) {
          set({ phase: 'continuing' });
          effects.push({ type: 'continue', request: state.active! });
        }
      }
    }
    if (state.active?.id === request.id || !state.active) break;
  }
  const observing = (value: RequestedSolveState<T>) => value.mounted && value.phase === 'waiting-readiness'
    && !!value.active && (!!value.active.preparationEngine || value.plan.ordinary === 'pending');
  if (observing(previous) !== observing(state)) effects.push({ type: observing(state) ? 'observe-start' : 'observe-stop' });
  return { state, effects };
}
