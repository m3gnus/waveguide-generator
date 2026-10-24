import { describe, expect, it } from 'vitest';
import { selectCadModelState, cadModelStatusLabel } from './cadModelState';
import { bundle, record } from '../shell/cadlink/selection.fixtures';

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
    expect(selectCadModelState({ ...selected, selectedBundle: { ...bundle, designIds: ['design-a'] } }, idle).authoredInCad).toBe(false);
    expect(selectCadModelState({ ...selected, selectedBundle: { ...bundle, bundleOrigin: 'native' } }, idle).canPrepare).toBe(false);
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
});
