import { describe, expect, it } from 'vitest';
import { issueCapabilityRequest, latestCapabilityRequest } from './capabilityRequestSequence';
import { initialRequestedSolve, reduceRequestedSolve, type RequestPlan, type RequestedSolveEvent, type RequestedSolveState } from './requestedSolveMachine';

const cold: RequestPlan = { capabilityGeneration: 1, ordinary: 'blocked', warmingEngine: 'engine', error: null, busy: false,
  engines: [{ name: 'engine', available: false, runtime_state: 'preparing' }] };
const ready: RequestPlan = { capabilityGeneration: 2, ordinary: 'ready', warmingEngine: null, error: null, busy: false,
  engines: [{ name: 'engine', available: true, runtime_state: 'ready' }] };
type Input = { model: string; engine: string; setup: string };
const inputs: Input = { model: 'model', engine: 'engine', setup: 'setup' };
type Event = RequestedSolveEvent<Input>;
type State = RequestedSolveState<Input>;
type TransportStep = `fetch-start ${'A' | 'B'}` | `fetch-response ${'A' | 'B'}`;
type Step = TransportStep | 'cold' | 'cached' | 'stale failed' | 'fresh failed' | 'stale ready' | 'settled' | 'stale settled' | 'mount' | 'press' | 'prepare' | 'Fusion A' | 'Fusion B' | 'ready' | 'prepared' | 'failed' | 'frame' | 'success' | 'post failure' | 'change' | 'stop' | 'unmount';
const alphabet: Step[] = ['press', 'prepare', 'Fusion A', 'Fusion B', 'ready', 'prepared', 'failed', 'frame', 'success', 'post failure', 'change', 'stop', 'cold', 'cached', 'stale failed', 'fresh failed', 'stale ready', 'settled', 'stale settled', 'mount', 'unmount'];
/** Each user action captures one fresh token (its explicit-navigation count);
 * Fusion A arrives by itself (no token), Fusion B is a user's click. */
let nextToken = 1000;
function eventFor(step: Step, state: State): Event {
  const id = state.active?.id ?? 0;
  switch (step) {
    case 'press': return { type: 'press', inputs, attention: nextToken++ };
    case 'prepare': return { type: 'press', inputs, prepareEngine: 'engine', attention: nextToken++ };
    case 'Fusion A': return { type: 'fusion-request', inputs, jobId: step, engine: 'engine' };
    case 'Fusion B': return { type: 'fusion-request', inputs, jobId: step, engine: 'engine', attention: nextToken++ };
    case 'cold': case 'ready': case 'fresh failed': case 'stale failed': case 'stale ready':
      return { type: 'plan-ready', plan: {
        ...(step.includes('failed') ? { ...cold, engines: [{ name: 'engine', available: false, runtime_state: 'failed' }] }
          : step === 'cold' ? cold : ready),
        capabilityGeneration: state.plan.capabilityGeneration + (step.startsWith('stale') ? -1 : 1),
      } };
    case 'cached': return { type: 'plan-ready', plan: state.plan };
    case 'settled': case 'stale settled': return { type: 'continuation-settled', id: step === 'settled' ? id : id - 1 };
    case 'prepared': return { type: 'preparation-done', id };
    case 'failed': return { type: 'preparation-failed', id };
    case 'frame': return { type: 'frame-resolved', id };
    case 'success': return { type: 'post-succeeded', id };
    case 'post failure': return { type: 'post-failed', id };
    case 'change': return { type: 'inputs-changed', ids: [...state.queue.map(request => request.id), id], reason: 'inputs changed' };
    case 'stop': case 'unmount': case 'mount': return { type: step };
    default: throw new Error('Transport events require a fetch-start record');
  }
}
function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function freeze<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) freeze(child);
  }
  return value;
}

/** Independent obligation ledger: every accepted request must post or have an explicit cancellation. */
function verify(sequence: readonly Step[], responses?: Record<'A' | 'B', string>) {
  const scope = {};
  issueCapabilityRequest(scope); // Initial cached snapshot.
  let time = 0;
  const issuedAt = new Map([[1, 0]]);
  const inFlight = new Map<string, number>();
  const acceptedAt = new Map<number, number>();
  let state = { ...initialRequestedSolve<Input>(), plan: cold };
  const accepted = new Map<number, string>();
  const acceptedGeneration = new Map<number, number>();
  const cancelled = new Set<number>();
  const posted = new Set<number>();
  const started = new Set<number>();
  // The token invariant: a request's token comes from the user action that
  // created it, or the latest that joined it; nothing else ever changes it;
  // no token belongs to two requests; and every press's token ends up on a
  // request (or reports a visible outcome), never dropped.
  const tokens = new Map<number, number | undefined>();
  const owners = new Map<number, number>();
  function send(event: Event) {
    time++;
    if (responses) event = { ...event, capabilityIssueSequence: latestCapabilityRequest(scope) };
    const before = state;
    const active = before.active;
    const live = [...before.queue, ...(active && before.phase !== 'submitting' ? [active] : [])];
    if (event.type === 'stop' || event.type === 'inputs-changed') {
      for (const request of live) if (event.type !== 'inputs-changed' || event.ids.includes(request.id)) cancelled.add(request.id);
    }
    if (event.type === 'preparation-failed' && active?.id === event.id && active.preparation === 'pending') cancelled.add(active.id);
    if (event.type === 'continuation-settled' && active?.id === event.id && ['continuing', 'cancelling'].includes(before.phase)) cancelled.add(active.id);
    if (event.type === 'preparation-done' && active?.id === event.id && active.preparation === 'pending' && before.phase === 'waiting-readiness') {
      acceptedGeneration.set(active.id, before.plan.capabilityGeneration);
      acceptedAt.set(active.id, time);
    }
    const newFusion = event.type === 'fusion-request' && before.mounted
      && ![...before.queue, ...(active ? [active] : [])].some(request => request.jobId === event.jobId);
    // A press while a request is being cancelled is a new request behind it.
    const newPress = event.type === 'press' && before.mounted && (!active || before.phase === 'cancelling');
    if (newFusion || newPress) {
      acceptedGeneration.set(before.nextId, before.plan.capabilityGeneration);
      acceptedAt.set(before.nextId, time);
      accepted.set(before.nextId, newFusion && event.type === 'fusion-request' ? event.jobId : 'ordinary');
      if (newFusion && active && !active.jobId && before.phase !== 'submitting') cancelled.add(active.id);
    }
    freeze(before); // Mutation throws, including nested inputs and capability arrays.
    const result = reduceRequestedSolve(before, event);
    state = result.state;
    if (event.type === 'plan-ready' && event.plan.capabilityGeneration < before.plan.capabilityGeneration) {
      assert(state.plan === before.plan, 'out-of-order response replaced newer evidence');
    }
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
      if (effect.type === 'abort') {
        const request = live.find(request => request.id === effect.id);
        const plan = event.type === 'plan-ready' && event.plan.capabilityGeneration > before.plan.capabilityGeneration ? event.plan : before.plan;
        const failure = request && request.preparation !== 'pending'
          && (responses ? issuedAt.get(plan.capabilityGeneration)! > acceptedAt.get(effect.id)!
            : plan.capabilityGeneration > acceptedGeneration.get(effect.id)!)
          && plan.engines.some(engine => engine.runtime_state === 'failed' || engine.runtime_state === 'unavailable');
        assert(cancelled.has(effect.id) || failure, 'request aborted without a cancellation, supersession or fresh failure');
        cancelled.add(effect.id);
      }
    }
    assert(state.phase !== 'submitting' || !!state.active && posted.has(state.active.id), 'submitting without POST');
    const eventToken = event.type === 'press' || event.type === 'fusion-request' ? event.attention : undefined;
    for (const request of [...state.queue, ...(state.active ? [state.active] : [])]) {
      const known = tokens.has(request.id);
      const previous = tokens.get(request.id);
      if (!known) assert(request.attention === eventToken, 'a new request did not take its press token');
      else assert(request.attention === previous || (eventToken !== undefined && request.attention === eventToken),
        'a request token changed without a user action joining it');
      tokens.set(request.id, request.attention);
      if (request.attention !== undefined) {
        const owner = owners.get(request.attention);
        assert(owner === undefined || owner === request.id, 'one token owns two requests');
        owners.set(request.attention, request.id);
      }
    }
    if (eventToken !== undefined && before.mounted) {
      const held = [...state.queue, ...(state.active ? [state.active] : [])].some(request => request.attention === eventToken);
      const reported = result.effects.some(effect => effect.type === 'notice' && effect.message !== null && effect.attention === eventToken);
      assert(held || reported, 'a press lost its token');
    }
    for (const effect of result.effects) {
      // The user's own edit replaced the press: its notice is indicated, never revealed.
      if (effect.type === 'notice' && effect.message !== null && event.type === 'inputs-changed') assert(effect.indicate === true, 'an edit revealed its cancellation');
      if (effect.type === 'notice' && effect.message !== null && effect.attention !== undefined) {
        assert(owners.has(effect.attention), 'a failure reported with a token no request holds');
      }
      if (effect.type === 'continue') assert(effect.request.attention === tokens.get(effect.request.id), 'continuation lost its token');
    }
    const ids = [...state.queue.map(request => request.id), ...(state.active ? [state.active.id] : [])];
    assert(new Set(ids).size === ids.length, 'duplicate owner');
  }
  for (const step of sequence) {
    if (step.startsWith('fetch-start')) {
      const generation = issueCapabilityRequest(scope);
      issuedAt.set(generation, ++time);
      inFlight.set(step.slice(-1), generation);
    } else if (step.startsWith('fetch-response')) {
      const name = step.slice(-1) as 'A' | 'B';
      const generation = inFlight.get(name);
      assert(generation !== undefined, 'response before fetch-start');
      const runtime_state = responses![name];
      send({ type: 'plan-ready', plan: { ...(runtime_state === 'ready' ? ready : cold), capabilityGeneration: generation,
        engines: [{ name: 'engine', available: runtime_state === 'ready', runtime_state }] } });
      inFlight.delete(name);
    } else send(eventFor(step, state));
  }
  // Fair completion: readiness and async effects eventually answer. This detects
  // stranded waits without adding timer/poll events to rescue the machine.
  send({ type: 'mount' });
  const finalGeneration = responses ? issueCapabilityRequest(scope) : state.plan.capabilityGeneration + 1;
  issuedAt.set(finalGeneration, ++time);
  send({ type: 'plan-ready', plan: { ...ready, capabilityGeneration: finalGeneration } });
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
  it.each([
    { depth: 5, events: alphabet.slice(0, 12), count: 271_453, label: 'original events' },
    { depth: 4, events: alphabet, count: 204_205, label: 'fresh/stale snapshots, settlement and mount replay' },
  ])('exhausts $count event words of length 0–$depth with $label', ({ depth, events, count: expected }) => {
    let count = 0;
    function visit(prefix: Step[]) {
      try { verify(prefix); } catch (error) { throw new Error(`${prefix.join(' → ')}: ${String(error)}`); }
      count++;
      if (prefix.length < depth) for (const step of events) visit([...prefix, step]);
    }
    visit([]);
    expect(count).toBe(expected);
  }, 20_000);

  it.each(['stop', 'change', 'unmount'] as const)('exhausts 40,320 eight-event permutations with %s', cancellation => {
    let count = 0;
    for (const sequence of permutations<Step>(['press', 'prepare', 'Fusion A', 'ready', 'prepared', 'frame', 'success', cancellation])) {
      try { verify(sequence); } catch (error) { throw new Error(`${sequence.join(' → ')}: ${String(error)}`); }
      count++;
    }
    expect(count).toBe(40_320);
  }, 20_000);

  it('exhausts 40,320 deeper interleavings of capability failure, settlement and mount replay', () => {
    let count = 0;
    for (const sequence of permutations<Step>(['Fusion A', 'fresh failed', 'ready', 'settled', 'unmount', 'mount', 'frame', 'success'])) {
      try { verify(sequence); } catch (error) { throw new Error(`${sequence.join(' → ')}: ${String(error)}`); }
      count++;
    }
    expect(count).toBe(40_320);
  }, 20_000);

  it.each([
    { A: 'failed', B: 'ready' }, { A: 'ready', B: 'failed' },
    { A: 'unavailable', B: 'ready' }, { A: 'ready', B: 'preparing' },
  ])('exhausts independent fetch starts/responses with %j', responses => {
    let count = 0;
    // Responses may arrive in either order and on either side of acceptance,
    // preparation completion, queuing, cancellation and the POST boundary.
    const scenarios: Step[][] = [
      ['Fusion A', 'Fusion B', 'frame', 'stop'],
      ['prepare', 'prepared', 'Fusion A', 'frame'],
      ['Fusion A', 'frame', 'unmount', 'mount'],
      ['Fusion A', 'frame', 'settled', 'stop'],
    ];
    for (const scenario of scenarios) {
      for (const sequence of permutations<Step>(['fetch-start A', 'fetch-response A', 'fetch-start B', 'fetch-response B', ...scenario])) {
        if (sequence.indexOf('fetch-start A') > sequence.indexOf('fetch-response A')
          || sequence.indexOf('fetch-start B') > sequence.indexOf('fetch-response B')) continue;
        try { verify(sequence, responses); } catch (error) { throw new Error(`${sequence.join(' → ')}: ${String(error)}`); }
        count++;
      }
    }
    expect(count).toBe(40_320);
  }, 20_000);

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
    const retry = reduceRequestedSolve(state, event);
    expect(retry.state.active!.id).toBeGreaterThan(preparing.state.active!.id);
    expect(retry.state.active!.preparation).toBe('none');
    expect(reduceRequestedSolve(retry.state, event).state.active!.id).toBe(retry.state.active!.id);
  });

  it.each(['waiting-readiness', 'continuing', 'submitting'] as const)('preparation failure cancels pre-POST work while preserving posted work: %s', phase => {
    let state = reduceRequestedSolve({ ...initialRequestedSolve<Input>(), plan: cold }, { type: 'press', inputs }).state;
    if (phase !== 'waiting-readiness') state = reduceRequestedSolve(state, { type: 'plan-ready', plan: ready }).state;
    const id = state.active!.id;
    if (phase === 'submitting') state = reduceRequestedSolve(state, { type: 'frame-resolved', id }).state;
    const failed = reduceRequestedSolve(state, { type: 'plan-ready', plan: { ...cold, capabilityGeneration: 3, engines: [{ name: 'engine', available: false, runtime_state: 'failed' }] } });
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
    expect(reduceRequestedSolve(state, { type: 'plan-ready', plan: { ...failedPlan, capabilityGeneration: 3 } }).state.phase).toBe('cancelled');

    const automatic = reduceRequestedSolve({ ...initialRequestedSolve<Input>(), plan: failedPlan },
      { type: 'fusion-request', inputs, jobId: 'late-Fusion', engine: 'engine' });
    expect(automatic.state.active?.jobId).toBe('late-Fusion');
    expect(reduceRequestedSolve(automatic.state, { type: 'plan-ready', plan: failedPlan }).state.phase).toBe('waiting-readiness');
    expect(reduceRequestedSolve(automatic.state, { type: 'plan-ready', plan: ready }).state.phase).toBe('continuing');
  });

  it('rejects stale ready snapshots after a fresh failure, then accepts a later request for the same job', () => {
    const event = { type: 'fusion-request' as const, inputs, jobId: 'Fusion', engine: 'engine' };
    let state = reduceRequestedSolve({ ...initialRequestedSolve<Input>(), plan: cold }, event).state;
    const failed = { ...cold, capabilityGeneration: 3, engines: [{ name: 'engine', available: false, runtime_state: 'failed' }] };
    state = reduceRequestedSolve(state, { type: 'plan-ready', plan: failed }).state;
    expect(state.active).toBeNull();
    state = reduceRequestedSolve(state, event).state;
    const id = state.active!.id;
    const stale = reduceRequestedSolve(state, { type: 'plan-ready', plan: ready });
    expect(stale.state.plan).toBe(failed);
    expect(stale.state.phase).toBe('waiting-readiness');
    expect(stale.effects).toEqual([]);
    const fresh = reduceRequestedSolve(stale.state, { type: 'plan-ready', plan: { ...ready, capabilityGeneration: 4 } });
    expect(fresh.state.active!.id).toBe(id);
    expect(fresh.effects.filter(effect => effect.type === 'continue')).toHaveLength(1);
  });

  it('retains queued Fusion ownership when a POST settles between unmount and remount', () => {
    let state = reduceRequestedSolve({ ...initialRequestedSolve<Input>(), plan: ready }, { type: 'press', inputs }).state;
    const id = state.active!.id;
    state = reduceRequestedSolve(state, { type: 'frame-resolved', id }).state;
    state = reduceRequestedSolve(state, { type: 'fusion-request', inputs, jobId: 'Fusion', engine: 'engine' }).state;
    const fusionId = state.queue[0].id;
    state = reduceRequestedSolve(state, { type: 'unmount' }).state;
    state = reduceRequestedSolve(state, { type: 'post-succeeded', id }).state;
    expect(state.active).toBeNull();
    expect(state.queue.map(request => request.id)).toEqual([fusionId]);
    const remount = reduceRequestedSolve(state, { type: 'mount' });
    expect(remount.state.active!.id).toBe(fusionId);
    expect(remount.effects.filter(effect => effect.type === 'continue')).toHaveLength(1);
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
