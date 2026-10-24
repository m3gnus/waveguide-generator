import { describe, expect, it } from 'vitest';
import { initialRequestedSolve, reduceRequestedSolve, type RequestPlan, type RequestedSolveEvent, type RequestedSolveState } from './requestedSolveMachine';

const cold: RequestPlan = { ordinary: 'blocked', warmingEngine: 'engine', error: null, busy: false,
  engines: [{ name: 'engine', available: false, runtime_state: 'preparing' }] };
const ready: RequestPlan = { ordinary: 'ready', warmingEngine: null, error: null, busy: false,
  engines: [{ name: 'engine', available: true, runtime_state: 'ready' }] };
type Input = { model: string; engine: string; setup: string };
const inputs: Input = { model: 'model', engine: 'engine', setup: 'setup' };
type Event = RequestedSolveEvent<Input>;
type State = RequestedSolveState<Input>;
type Step = 'press' | 'prepare' | 'Fusion A' | 'Fusion B' | 'ready' | 'prepared' | 'failed' | 'frame' | 'success' | 'post failure' | 'change' | 'stop' | 'unmount';
const alphabet: Step[] = ['press', 'prepare', 'Fusion A', 'Fusion B', 'ready', 'prepared', 'failed', 'frame', 'success', 'post failure', 'change', 'stop'];
function eventFor(step: Step, state: State): Event {
  const id = state.active?.id ?? 0;
  switch (step) {
    case 'press': return { type: 'press', inputs };
    case 'prepare': return { type: 'press', inputs, prepareEngine: 'engine' };
    case 'Fusion A': case 'Fusion B': return { type: 'fusion-request', inputs, jobId: step, engine: 'engine' };
    case 'ready': return { type: 'plan-ready', plan: ready };
    case 'prepared': return { type: 'preparation-done', id };
    case 'failed': return { type: 'preparation-failed', id };
    case 'frame': return { type: 'frame-resolved', id };
    case 'success': return { type: 'post-succeeded', id };
    case 'post failure': return { type: 'post-failed', id };
    case 'change': return { type: 'inputs-changed', ids: [...state.queue.map(request => request.id), id], reason: 'inputs changed' };
    case 'stop': case 'unmount': return { type: step };
  }
}
function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

/** Independent obligation ledger: every accepted request must post or have an explicit cancellation. */
function verify(sequence: readonly Step[]) {
  let state = { ...initialRequestedSolve<Input>(), plan: cold };
  const accepted = new Map<number, string>();
  const cancelled = new Set<number>();
  const posted = new Set<number>();
  const started = new Set<number>();
  function send(event: Event) {
    const before = state;
    const active = before.active;
    const live = [...before.queue, ...(active && before.phase !== 'submitting' ? [active] : [])];
    if (event.type === 'stop' || event.type === 'unmount' || event.type === 'inputs-changed') {
      for (const request of live) if (event.type !== 'inputs-changed' || event.ids.includes(request.id)) cancelled.add(request.id);
    }
    if (event.type === 'preparation-failed' && active?.id === event.id && active.preparation === 'pending') cancelled.add(active.id);
    const newFusion = event.type === 'fusion-request' && before.mounted && !before.seenFusion.includes(event.jobId);
    const newPress = event.type === 'press' && before.mounted && !active;
    if (newFusion || newPress) {
      accepted.set(before.nextId, newFusion && event.type === 'fusion-request' ? event.jobId : 'ordinary');
      if (newFusion && active && !active.jobId && before.phase !== 'submitting') cancelled.add(active.id);
    }
    const frozen = JSON.stringify(before);
    const result = reduceRequestedSolve(before, event);
    state = result.state;
    assert(JSON.stringify(before) === frozen, 'reducer mutated its input');
    assert(state.nextId === before.nextId + (newFusion || newPress ? 1 : 0), 'request acceptance/deduplication diverged');
    for (const effect of result.effects) {
      if (effect.type === 'continue') {
        assert(!started.has(effect.request.id), 'continuation executed twice');
        assert(!cancelled.has(effect.request.id), 'cancelled request continued');
        started.add(effect.request.id);
      }
      if (effect.type === 'post') {
        assert(before.phase === 'continuing' && before.active?.id === effect.id, 'POST without a matching continuation');
        assert(!posted.has(effect.id), 'request posted twice');
        assert(!cancelled.has(effect.id), 'cancelled request posted');
        posted.add(effect.id);
      }
      if (effect.type === 'abort') assert(cancelled.has(effect.id), 'request aborted without a cancellation or supersession');
    }
    assert(state.phase !== 'submitting' || !!state.active && posted.has(state.active.id), 'submitting without POST');
    const ids = [...state.queue.map(request => request.id), ...(state.active ? [state.active.id] : [])];
    assert(new Set(ids).size === ids.length, 'duplicate owner');
  }
  for (const step of sequence) send(eventFor(step, state));
  // Fair completion: readiness and async effects eventually answer. This detects
  // stranded waits without adding timer/poll events to rescue the machine.
  send({ type: 'plan-ready', plan: ready });
  for (let remaining = accepted.size + 2; state.active && remaining > 0; remaining--) {
    const id = state.active.id;
    switch (state.phase) {
      case 'waiting-readiness': send({ type: 'preparation-done', id }); break;
      case 'continuing': send({ type: 'frame-resolved', id }); break;
      case 'submitting': send({ type: 'post-succeeded', id }); break;
      case 'cancelling': send({ type: 'continuation-settled', id }); break;
      default: throw new Error('terminal state retains an owner');
    }
    // Each request has at most three completion stages.
    if (state.active?.id === id && state.phase === 'continuing') send({ type: 'frame-resolved', id });
    if (state.active?.id === id && state.phase === 'submitting') send({ type: 'post-succeeded', id });
  }
  assert(!state.active && !state.queue.length, 'infinite wait after readiness');
  for (const [id, kind] of accepted) assert(posted.has(id) || cancelled.has(id), `lost ${kind} request ${id}`);
  for (const id of posted) assert(accepted.has(id), 'POST for an unaccepted request');
}

function* permutations<T>(items: readonly T[]): Generator<T[]> {
  if (!items.length) { yield []; return; }
  for (let index = 0; index < items.length; index++) {
    for (const tail of permutations(items.filter((_, i) => i !== index))) yield [items[index], ...tail];
  }
}

describe('requested-solve pure reducer', () => {
  it('exhausts all 271,453 event words of length 0–5, including repeated/out-of-order callbacks', () => {
    let count = 0;
    function visit(prefix: Step[]) {
      try { verify(prefix); } catch (error) { throw new Error(`${prefix.join(' → ')}: ${String(error)}`); }
      count++;
      if (prefix.length < 5) for (const step of alphabet) visit([...prefix, step]);
    }
    visit([]);
    expect(count).toBe(271_453);
  }, 60_000);

  it.each(['stop', 'change', 'unmount'] as const)('exhausts 40,320 eight-event permutations with %s', cancellation => {
    let count = 0;
    for (const sequence of permutations<Step>(['press', 'prepare', 'Fusion A', 'ready', 'prepared', 'frame', 'success', cancellation])) {
      try { verify(sequence); } catch (error) { throw new Error(`${sequence.join(' → ')}: ${String(error)}`); }
      count++;
    }
    expect(count).toBe(40_320);
  }, 60_000);

  it('covers the reported pre-POST order and Fusion during/after ordinary POST', () => {
    verify(['press', 'ready', 'Fusion A', 'frame']);
    verify(['press', 'ready', 'frame', 'Fusion A', 'success']);
    verify(['press', 'ready', 'frame', 'success', 'Fusion A']);
  });

  it.each(['engine', 'setup', 'model'] as const)('latches %s changes and rejects stale callbacks for the superseded request', reason => {
    let state = { ...initialRequestedSolve<Input>(), plan: ready };
    state = reduceRequestedSolve(state, { type: 'press', inputs }).state;
    const id = state.active!.id;
    state = reduceRequestedSolve(state, { type: 'fusion-request', inputs, jobId: 'Fusion', engine: 'engine' }).state;
    const fusionId = state.queue[0].id;
    state = reduceRequestedSolve(state, { type: 'inputs-changed', ids: [id, fusionId], reason }).state;
    state = reduceRequestedSolve(state, { type: 'continuation-settled', id }).state;
    for (const type of ['frame-resolved', 'preparation-done', 'post-succeeded', 'post-failed'] as const) {
      const result = reduceRequestedSolve(state, { type, id });
      expect(result.effects).toEqual([]);
      state = result.state;
    }
    expect(state.active).toBeNull();
    expect(state.queue).toEqual([]);
    expect(state.phase).toBe('cancelled');
  });

  it('treats identical readiness snapshots as a no-op, including freshly allocated capability arrays', () => {
    const state = { ...initialRequestedSolve<Input>(), plan: ready };
    const result = reduceRequestedSolve(state, { type: 'plan-ready', plan: { ...ready, engines: ready.engines.map(engine => ({ ...engine })) } });
    expect(result.state).toBe(state);
    expect(result.effects).toEqual([]);
  });

  it('can explicitly prepare an already accepted blocked Fusion request and retry after failure', () => {
    let state = { ...initialRequestedSolve<Input>(), plan: cold };
    const event = { type: 'fusion-request' as const, inputs, jobId: 'Fusion', engine: 'engine' };
    state = reduceRequestedSolve(state, event).state;
    const preparing = reduceRequestedSolve(state, { ...event, prepare: true });
    expect(preparing.state.active!.id).toBe(state.active!.id);
    expect(preparing.effects.map(effect => effect.type)).toEqual(['prepare']);
    state = reduceRequestedSolve(preparing.state, { type: 'preparation-failed', id: state.active!.id }).state;
    expect(reduceRequestedSolve(state, event).state.active).toBeNull(); // observations do not retry
    const retry = reduceRequestedSolve(state, { ...event, prepare: true });
    expect(retry.state.active!.id).toBeGreaterThan(preparing.state.active!.id);
    expect(retry.effects.some(effect => effect.type === 'prepare')).toBe(true);
  });

  it.each(['waiting-readiness', 'continuing', 'submitting'] as const)('preparation failure cancels pre-POST work while preserving posted work: %s', phase => {
    let state = reduceRequestedSolve({ ...initialRequestedSolve<Input>(), plan: cold }, { type: 'press', inputs }).state;
    if (phase !== 'waiting-readiness') state = reduceRequestedSolve(state, { type: 'plan-ready', plan: ready }).state;
    const id = state.active!.id;
    if (phase === 'submitting') state = reduceRequestedSolve(state, { type: 'frame-resolved', id }).state;
    const failed = reduceRequestedSolve(state, { type: 'plan-ready', plan: { ...cold, engines: [{ name: 'engine', available: false, runtime_state: 'failed' }] } });
    expect(failed.state.phase).toBe(phase === 'submitting' ? 'submitting' : phase === 'continuing' ? 'cancelling' : 'cancelled');
    expect(failed.effects.some(effect => effect.type === 'abort')).toBe(phase !== 'submitting');
  });

  it('does not replay a cached runtime failure against a new Fusion request or successful preparation', () => {
    const failedPlan: RequestPlan = { ...cold, engines: [{ name: 'engine', available: false, runtime_state: 'failed' }] };
    let state = { ...initialRequestedSolve<Input>(), plan: failedPlan };
    state = reduceRequestedSolve(state, { type: 'fusion-request', inputs, jobId: 'Fusion', engine: 'engine', prepare: true }).state;
    state = reduceRequestedSolve(state, { type: 'preparation-done', id: state.active!.id }).state;
    expect(state.phase).toBe('waiting-readiness');
    expect(reduceRequestedSolve(state, { type: 'plan-ready', plan: ready }).state.phase).toBe('continuing');
    expect(reduceRequestedSolve(state, { type: 'plan-ready', plan: failedPlan }).state.phase).toBe('cancelled');

    const automatic = reduceRequestedSolve({ ...initialRequestedSolve<Input>(), plan: failedPlan },
      { type: 'fusion-request', inputs, jobId: 'late-Fusion', engine: 'engine' });
    expect(automatic.state.active?.jobId).toBe('late-Fusion');
    expect(reduceRequestedSolve(automatic.state, { type: 'plan-ready', plan: ready }).state.phase).toBe('continuing');
  });

  it('cancels pre-POST failures and reports them through effects', () => {
    const started = reduceRequestedSolve({ ...initialRequestedSolve<Input>(), plan: ready }, { type: 'press', inputs });
    const result = reduceRequestedSolve(started.state, { type: 'continuation-settled', id: started.state.active!.id, message: 'preflight failed' });
    expect(result.state.phase).toBe('cancelled');
    expect(result.effects).toContainEqual({ type: 'notice', message: 'preflight failed' });
  });

  it('waits for an external submission lock and resumes Fusion when it is released', () => {
    let state = { ...initialRequestedSolve<Input>(), plan: { ...ready, busy: true } };
    const held = reduceRequestedSolve(state, { type: 'fusion-request', inputs, jobId: 'Fusion', engine: 'engine' });
    expect(held.effects.some(effect => effect.type === 'continue')).toBe(false);
    state = held.state;
    expect(reduceRequestedSolve(state, { type: 'plan-ready', plan: ready }).effects.map(effect => effect.type)).toEqual(['continue', 'observe-stop']);
  });
});
