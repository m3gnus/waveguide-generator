/** Pure request ownership. Async work may only advance the matching request ID. */
export type SolvePhase = 'idle' | 'waiting-readiness' | 'continuing' | 'cancelling' | 'submitting' | 'done' | 'cancelled';
export interface RequestPlan {
  ordinary: 'ready' | 'pending' | 'blocked' | 'disabled';
  warmingEngine: string | null;
  engines: readonly { name: string; available: boolean; runtime_state?: string | null }[];
  error: string | null;
  busy: boolean;
}
export interface SolveRequest<T> {
  id: number;
  inputs: T;
  jobId?: string;
  preparationEngine?: string;
  preparation: 'none' | 'pending' | 'done';
}
export interface RequestedSolveState<T> {
  phase: SolvePhase;
  active: SolveRequest<T> | null;
  queue: readonly SolveRequest<T>[];
  /** Job identities belong to the owner, including cancelled/completed intentions. */
  seenFusion: readonly string[];
  nextId: number;
  mounted: boolean;
  plan: RequestPlan;
}
export type RequestedSolveEvent<T> =
  | { type: 'press'; inputs: T; prepareEngine?: string }
  | { type: 'fusion-request'; inputs: T; jobId: string; engine: string; prepare?: boolean }
  | { type: 'plan-ready'; plan: RequestPlan }
  | { type: 'preparation-done' | 'preparation-failed' | 'frame-resolved' | 'post-succeeded' | 'post-failed' | 'continuation-settled'; id: number; message?: string }
  | { type: 'inputs-changed'; ids: readonly number[]; reason?: string }
  | { type: 'stop' | 'unmount' | 'mount' };
export type RequestedSolveEffect<T> =
  | { type: 'continue' | 'prepare'; request: SolveRequest<T> }
  | { type: 'abort' | 'post'; id: number }
  | { type: 'notice'; message: string | null }
  | { type: 'release' | 'observe-start' | 'observe-stop' };
export const PREPARATION_FAILED = 'Prepare and solve was cancelled because engine preparation failed.';
export function initialRequestedSolve<T>(): RequestedSolveState<T> {
  return { phase: 'idle', active: null, queue: [], seenFusion: [], nextId: 1, mounted: true,
    plan: { ordinary: 'pending', warmingEngine: null, engines: [], error: null, busy: false } };
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
    const [next, ...queue] = state.queue;
    set({ active: null, phase, queue });
    if (next && state.mounted) activate(next);
  };
  const cancel = (reason?: string) => {
    const active = state.active;
    if (!active || state.phase === 'submitting' || state.phase === 'cancelling') return;
    effects.push({ type: 'abort', id: active.id }, { type: 'release' });
    if (reason) effects.push({ type: 'notice', message: reason });
    // Wait for the aborted executor to release the shared submission mutex.
    if (state.phase === 'continuing') set({ phase: 'cancelling' });
    else advance('cancelled');
  };
  switch (event.type) {
    case 'mount': if (!state.mounted) set({ mounted: true }); break;
    case 'press':
    case 'fusion-request': {
      if (!state.mounted) break;
      const fusion = event.type === 'fusion-request';
      if (fusion && state.seenFusion.includes(event.jobId)) {
        if (!event.prepare) break;
        if (state.active?.jobId === event.jobId) {
          if (state.phase === 'waiting-readiness' && state.active.preparation !== 'pending') {
            const request = { ...state.active, preparation: 'pending' as const, preparationEngine: event.engine };
            set({ active: request });
            effects.push({ type: 'prepare', request });
          }
          break;
        }
        if (state.queue.some(request => request.jobId === event.jobId)) {
          set({ queue: state.queue.map(request => request.jobId === event.jobId
            ? { ...request, preparation: 'pending', preparationEngine: event.engine } : request) });
          break;
        }
        // A deliberate Prepare press can retry a terminal request. Repeated
        // automatic job observations still cannot resurrect a cancelled intent.
      }
      if (!fusion && state.active) {
        if (event.prepareEngine && state.phase === 'waiting-readiness' && state.active.preparation !== 'pending') {
          const request = { ...state.active, preparation: 'pending' as const, preparationEngine: event.prepareEngine };
          set({ active: request });
          effects.push({ type: 'prepare', request });
        }
        break;
      }
      const request: SolveRequest<T> = { id: state.nextId, inputs: event.inputs,
        ...(fusion ? { jobId: event.jobId, preparationEngine: event.engine }
          : { preparationEngine: event.prepareEngine ?? state.plan.warmingEngine ?? undefined }),
        preparation: (fusion ? event.prepare : event.prepareEngine) ? 'pending' : 'none' };
      set({ nextId: state.nextId + 1,
        seenFusion: fusion && !state.seenFusion.includes(event.jobId) ? [...state.seenFusion, event.jobId] : state.seenFusion });
      if (!state.active) activate(request);
      else {
        set({ queue: [...state.queue, request] });
        // A Fusion request replaces ordinary work until the POST boundary.
        if (!state.active.jobId) cancel();
      }
      break;
    }
    case 'plan-ready': {
      const a = state.plan, b = event.plan;
      if (a.ordinary !== b.ordinary || a.warmingEngine !== b.warmingEngine || a.error !== b.error || a.busy !== b.busy
        || a.engines.length !== b.engines.length || a.engines.some((engine, index) => {
          const next = b.engines[index];
          return engine.name !== next.name || engine.available !== next.available || engine.runtime_state !== next.runtime_state;
        })) set({ plan: b });
      break;
    }
    case 'preparation-done':
      if (state.active?.id === event.id && state.phase === 'waiting-readiness' && state.active.preparation === 'pending') {
        set({ active: { ...state.active, preparation: 'done' } });
      }
      break;
    case 'preparation-failed':
      if (state.active?.id === event.id && state.active.preparation === 'pending') cancel(PREPARATION_FAILED);
      break;
    // All pre-POST async steps have completed; authorize the matching POST once.
    case 'frame-resolved':
      if (state.active?.id === event.id && state.phase === 'continuing') {
        set({ phase: 'submitting' });
        effects.push({ type: 'post', id: event.id });
      }
      break;
    case 'continuation-settled':
      if (state.active?.id === event.id && ['continuing', 'cancelling'].includes(state.phase)) {
        if (state.phase === 'continuing') {
          effects.push({ type: 'release' });
          if (event.message && state.mounted) effects.push({ type: 'notice', message: event.message });
        }
        advance('cancelled');
      }
      break;
    case 'post-succeeded':
    case 'post-failed':
      if (state.active?.id === event.id && state.phase === 'submitting') {
        if (event.type === 'post-failed' && event.message && state.mounted) effects.push({ type: 'notice', message: event.message });
        advance('done');
      }
      break;
    case 'inputs-changed':
      set({ queue: state.queue.filter(request => !event.ids.includes(request.id)) });
      if (state.active && event.ids.includes(state.active.id)) cancel(event.reason);
      break;
    case 'stop':
    case 'unmount':
      set({ queue: [], ...(event.type === 'unmount' ? { mounted: false } : {}) });
      cancel();
      break;
  }
  // Cancellation can activate a successor. Settle that successor against the
  // same readiness fact immediately; no extra render or poll is needed.
  for (let remaining = state.queue.length + 2; remaining > 0; remaining--) {
    const request = state.active;
    if (!request || !['waiting-readiness', 'continuing'].includes(state.phase) || request.preparation === 'pending') break;
    const engine = state.plan.engines.find(engine => engine.name === request.preparationEngine);
    // Only a readiness event supplies a new failure fact. A cached failure
    // must not cancel a new request or a successful preparation completion.
    if (event.type === 'plan-ready' && request.preparationEngine && (engine?.runtime_state === 'failed' || engine?.runtime_state === 'unavailable')) cancel(PREPARATION_FAILED);
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
