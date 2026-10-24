import { describe, expect, it } from 'vitest';
import { selectCadModelState, cadModelHeaderLine, cadModelStatusLabel } from './cadModelState';
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
    expect(cadModelStatusLabel(model)).toBe('CAD model · not prepared');
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
    expect(cadModelStatusLabel(preparing)).toBe('CAD model · preparing…');
    expect(preparing.canPrepare).toBe(false);
    const failedJob = { ...job, status: 'error', cad_state: { state: 'needs_user_input', reason: 'preparation_failed', prepare_failed: true,
      message: 'WG could not prepare Speaker. This has been recorded for the developers.', next_step: null } } as unknown as JobItem;
    const failed = selectCadModelState(pointer, idle, { kind: 'prepare-failed', job: failedJob });
    expect(failed).toMatchObject({ phase: 'prepare-failed', failureMessage: 'WG could not prepare Speaker. This has been recorded for the developers.' });
    // The status bar and the CAD project header say the same.
    expect(cadModelHeaderLine(preparing)).toBe('Preparing Speaker…');
    expect(cadModelStatusLabel(failed)).toBe('CAD model · preparation failed');
    expect(cadModelHeaderLine(failed)).toBe('WG could not prepare Speaker. This has been recorded for the developers.');
    const heldJob = { ...job, status: 'held', cad_state: { state: 'needs_user_input', reason: 'mesh_too_large', held: true } } as unknown as JobItem;
    const waiting = selectCadModelState(pointer, idle, { kind: 'waiting', job: heldJob });
    expect(cadModelStatusLabel(waiting)).toBe('CAD model · waiting for you');
    expect(cadModelHeaderLine(waiting)).toContain('Waiting for you');
    // A prepared model on screen is its own state; the request's job does not override it.
    expect(selectCadModelState({ ...pointer, ingestRecord: record, needsIngest: false }, idle, { kind: 'preparing', job }).phase).toBe('prepared');
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
    expect(cadModelStatusLabel(selectCadModelState({ ...state, needsIngest: false }, idle))).toBe('CAD model · 123 triangles');
    expect(selectCadModelState(state, idle).triangles).toBeNull();
  });

  it('owns a request held for the user as the waiting state', () => {
    const job = { id: 'held-1', status: 'held', cad_state: { state: 'needs_user_input', reason: 'mesh_too_large', held: true } } as unknown as JobItem;
    const waiting = { kind: 'waiting' as const, job };
    const model = selectCadModelState(selected, idle, waiting);
    expect(model).toMatchObject({ phase: 'waiting', preparation: 'selected-unprepared', heldRequest: job, needsPreparation: false });
    expect(cadModelStatusLabel(model)).toBe('CAD model · waiting for you');
    const prepared = selectCadModelState({ ...selected, ingestRecord: record, needsIngest: false }, idle, waiting);
    expect(prepared).toMatchObject({ phase: 'waiting', preparation: 'prepared' });
    // The engine gate stays with solve readiness; an active preparation outranks the hold.
    const engine = { ...job, cad_state: { ...job.cad_state, reason: 'engine_unavailable' } } as JobItem;
    expect(selectCadModelState(selected, idle, { kind: 'waiting', job: engine }).phase).toBe('selected-unprepared');
    expect(selectCadModelState(selected, { ...idle, ingesting: true }, waiting).phase).toBe('preparing');
    expect(selectCadModelState({ ...selected, selectedBundle: null }, idle, waiting).phase).toBe('none');
  });
});
