import { describe, expect, it } from 'vitest';
import { selectCadModelState } from './cadModelState';
import { cadModelStatusLine } from './cadModelStatusLine';

/** The status line of a model, for the request it was given. */
const say = (model: ReturnType<typeof selectCadModelState>, request: Parameters<typeof cadModelStatusLine>[1] = null) => cadModelStatusLine(model, request);
import type { MeshCeilingChoice } from './meshCeilingChoice';
import { bundle, record } from '../shell/cadlink/selection.fixtures';
import type { JobItem } from '../api/jobsSocket';

const selected = { selectedBundle: bundle, ingestRecord: null, needsIngest: true, projectLineageId: null };
const idle = { ingesting: false, ingestError: null };

describe('selected CAD model state', () => {
  it.each([
    ['none', { ...selected, selectedBundle: null }, idle],
    ['selected-unprepared', selected, idle],
    ['preparing', selected, { ...idle, ingesting: true }],
    ['prepare-failed', selected, { ...idle, ingestError: 'Preparation failed' }],
    ['prepared', { ...selected, ingestRecord: record, needsIngest: false }, idle],
    ['stale', { ...selected, ingestRecord: record }, idle],
  ] as const)('owns the %s state', (phase, state, preparation) => {
    expect(selectCadModelState(state, preparation).phase).toBe(phase);
  });

  it('recognizes a CAD-authored selection without a preparation or document identity', () => {
    const model = selectCadModelState(selected, idle);
    expect(model).toMatchObject({ selected: true, label: 'Speaker', authoredInCad: true, lineageId: null, canPrepare: true });
    expect(say(model).short).toBe('not prepared');
    // A bundle naming the open WG design is that design's own return (linked).
    expect(selectCadModelState({ ...selected, selectedBundle: { ...bundle, designIds: ['design-a'] } }, idle, null, 'design-a').authoredInCad).toBe(false);
    // Naming a different WG design, or no open design, never brings parametric Open/Send back.
    expect(selectCadModelState({ ...selected, selectedBundle: { ...bundle, designIds: ['design-a'] } }, idle, null, 'design-b').authoredInCad).toBe(true);
    expect(selectCadModelState({ ...selected, selectedBundle: { ...bundle, designIds: ['design-a'] } }, idle).authoredInCad).toBe(true);
    expect(selectCadModelState({ ...selected, selectedBundle: { ...bundle, bundleOrigin: 'native' } }, idle).canPrepare).toBe(false);
  });

  it('treats a restored run record without bundle or freshness as a CAD model, not from Fusion', () => {
    const restored = selectCadModelState({ ...selected, selectedBundle: null, ingestRecord: { ...record, freshness: undefined as never }, needsIngest: false }, idle, null, 'design-a');
    expect(restored.authoredInCad).toBe(true);
    expect(restored.application).not.toBe('Fusion');
    const onshape = selectCadModelState({ ...selected, selectedBundle: { ...bundle, bundleOrigin: 'onshape' } }, idle);
    expect(onshape.application).toBe('Onshape');
  });

  it('takes the server job of the selected Fusion request into account without a mesh', () => {
    const pointer = { ...selected, selectedBundle: { ...bundle, readable: false, requestId: 'job-1' } };
    const job = { id: 'job-1', status: 'preparing', cad_state: { state: 'processing', reason: null, message: null } } as unknown as JobItem;
    const preparing = selectCadModelState(pointer, idle, { kind: 'preparing', job });
    expect(preparing.phase).toBe('preparing');
    expect(say(preparing, { kind: 'preparing', job }).short).toBe('preparing…');
    expect(preparing.canPrepare).toBe(false);
    const failedJob = { ...job, status: 'error', cad_state: { state: 'needs_user_input', reason: 'preparation_failed', prepare_failed: true,
      message: 'Waveguide Generator could not prepare Speaker. This has been recorded for the developers.', next_step: null } } as unknown as JobItem;
    const failed = selectCadModelState(pointer, idle, { kind: 'prepare-failed', job: failedJob });
    expect(failed).toMatchObject({ phase: 'prepare-failed', failureMessage: 'Waveguide Generator could not prepare Speaker. This has been recorded for the developers.' });
    // The status bar and the model card say the same.
    expect(say(preparing, { kind: 'preparing', job }).text).toBe('Preparing Speaker…');
    expect(say(failed, { kind: 'prepare-failed', job: failedJob }).short).toBe('could not prepare');
    expect(say(failed, { kind: 'prepare-failed', job: failedJob }).text).toBe('Waveguide Generator could not prepare Speaker. This has been recorded for the developers.');
    const heldJob = { ...job, status: 'held', cad_state: { state: 'needs_user_input', reason: 'mesh_too_large', held: true, attention: 'question' } } as unknown as JobItem;
    const waiting = selectCadModelState(pointer, idle, { kind: 'waiting', job: heldJob });
    expect(say(waiting, { kind: 'waiting', job: heldJob }).short).toBe('needs your choice');
    expect(say(waiting, { kind: 'waiting', job: heldJob }).text).toContain('needs your choice');
    // A prepared model on screen is its own state; the request's job does not override it.
    expect(selectCadModelState({ ...pointer, ingestRecord: record, needsIngest: false }, idle, { kind: 'preparing', job }).phase).toBe('prepared');
  });

  it('follows a refused or failed request of a pointer, so every surface says the same', () => {
    const pointer = { ...selected, selectedBundle: { ...bundle, readable: false, requestId: 'job-1' } };
    const job = { id: 'job-1', status: 'error', cad_state: { state: 'rejected', reason: 'model_cut_unsolvable' } } as unknown as JobItem;
    const refused = selectCadModelState(pointer, idle, { kind: 'refused', job, message: 'Speaker arrived cut open.' });
    expect(refused).toMatchObject({ phase: 'stopped', failureMessage: 'Speaker arrived cut open.', canPrepare: false });
    expect(say(refused, { kind: 'refused', job, message: 'Speaker arrived cut open.' })).toMatchObject({ short: 'not solved', text: 'Speaker arrived cut open.', action: 'none' });
    const failed = selectCadModelState(pointer, idle, { kind: 'failed', job, message: 'The solve failed.' });
    expect(failed.phase).toBe('stopped');
    // A readable bundle can be prepared again; a prepared one is its own state.
    expect(selectCadModelState(selected, idle, { kind: 'refused', job, message: 'x' }).needsPreparation).toBe(true);
    expect(selectCadModelState({ ...pointer, ingestRecord: record, needsIngest: false }, idle,
      { kind: 'refused', job, message: 'x' }).phase).toBe('prepared');
  });

  it('offers Try again only where preparing again can work', () => {
    const pointer = { ...selected, selectedBundle: { ...bundle, readable: false } };
    const failing = { ingesting: false, ingestError: 'Role resolution refused.' };
    expect(say(selectCadModelState(pointer, failing))).toMatchObject({ text: 'WG could not prepare Speaker.', action: 'none' });
    expect(say(selectCadModelState(selected, failing))).toMatchObject({ text: 'WG could not prepare Speaker.', action: 'try-again' });
  });

  it('waits for a mesh-size choice asked for this model only', () => {
    const ceiling: MeshCeilingChoice = { name: 'Speaker', nextStep: 'x', bundlePath: bundle.bundlePath, then: 'prepare', deferred: true };
    const waiting = selectCadModelState(selected, idle, null, null, ceiling);
    expect(waiting).toMatchObject({ phase: 'waiting', meshChoice: ceiling, heldRequest: null });
    expect(say(waiting).text).toBe('Speaker needs your choice: it is larger than WG\'s limit.');
    expect(selectCadModelState(selected, idle, null, null, { ...ceiling, bundlePath: 'another' }).phase).toBe('selected-unprepared');
    expect(selectCadModelState({ ...selected, ingestRecord: record, needsIngest: false }, idle, null, null, ceiling).phase).toBe('prepared');
  });

  it('keeps a retained legacy preparation usable without a workspace bundle', () => {
    expect(selectCadModelState({ ...selected, selectedBundle: null, ingestRecord: record, needsIngest: false }, idle))
      .toMatchObject({ phase: 'prepared', selected: true, record });
  });

  it('scopes preparing and failed feedback to the preparation intent', () => {
    const restored = { ...selected, ingestRecord: record, needsIngest: false, captureIngestIntent: () => 2 };
    expect(selectCadModelState(restored, { ingesting: false, ingestError: 'Older failure', preparationIntent: 1 }).phase).toBe('prepared');
    expect(selectCadModelState(restored, { ingesting: true, ingestError: null, preparationIntent: 1 }).phase).toBe('prepared');
    expect(selectCadModelState({ ...selected, captureIngestIntent: () => 2 },
      { ingesting: false, ingestError: 'Current failure', preparationIntent: 2 }).phase).toBe('prepare-failed');
  });

  it('does not present a stale mesh count as prepared geometry', () => {
    const state = { ...selected, ingestRecord: { ...record, mesh: { stats: { triangle_count: 123 } } } as typeof record };
    expect(say(selectCadModelState({ ...state, needsIngest: false }, idle)).short).toBe('ready · 123 triangles');
    expect(selectCadModelState(state, idle).triangles).toBeNull();
  });

  it('owns a request held for the user as the waiting state', () => {
    const job = { id: 'held-1', status: 'held', cad_state: { state: 'needs_user_input', reason: 'mesh_too_large', held: true, attention: 'question' } } as unknown as JobItem;
    const waiting = { kind: 'waiting' as const, job };
    const model = selectCadModelState(selected, idle, waiting);
    expect(model).toMatchObject({ phase: 'waiting', preparation: 'selected-unprepared', heldRequest: job, needsPreparation: false });
    expect(say(model, waiting).short).toBe('needs your choice');
    const prepared = selectCadModelState({ ...selected, ingestRecord: record, needsIngest: false }, idle, waiting);
    expect(prepared).toMatchObject({ phase: 'waiting', preparation: 'prepared' });
    // The engine gate stays with solve readiness; an active preparation outranks the hold.
    const engine = { ...job, cad_state: { ...job.cad_state, reason: 'engine_unavailable', attention: 'action' } } as JobItem;
    expect(selectCadModelState(selected, idle, { kind: 'waiting', job: engine }).phase).toBe('selected-unprepared');
    expect(selectCadModelState(selected, { ...idle, ingesting: true }, waiting).phase).toBe('preparing');
    expect(selectCadModelState({ ...selected, selectedBundle: null }, idle, waiting).phase).toBe('none');
  });
});
