import { beforeEach, describe, expect, it } from 'vitest';
import type { SolverFrameAxisOption, SolverFramePreselection, SolverFramePreview, SolverFrameState } from '../api/solverFrame';
import type { SolverFrameAxis } from '../viewport/solverFrame';
import {
  chosenFrameAxis,
  resetCadSolverFrameStore,
  useCadSolverFrameStore,
} from './cadSolverFrame';

const IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
const ALL: SolverFrameAxis[] = ['+z', '-z', '+x', '-x', '+y', '-y'];

function frame(
  preselected: SolverFrameAxis | null,
  confirmed: SolverFrameAxis | null = null,
  source: SolverFramePreselection['source'] = confirmed ? 'confirmed' : 'suggested',
): SolverFrameState {
  const axes: SolverFrameAxisOption[] = ALL.map((axis) => ({
    axis, allowed: true, reason: null, solverFromAssembly: IDENTITY, previewFromRecord: IDENTITY,
  }));
  return {
    linked: false,
    ingestId: 'wgi_1',
    contract: 'solver-frame-v2',
    requirement: null,
    recordAxis: '+z',
    recordStatesFrame: true,
    confirmed: confirmed ? { axis: confirmed, confirmedAt: '2026-09-29T10:00:00Z' } : null,
    axes,
    suggestion: null,
    preselected: preselected ? { axis: preselected, source } : null,
    differs: null,
  };
}

function show(preview: SolverFramePreview): void {
  useCadSolverFrameStore.getState().apply('wgi_1', preview);
}

const shown = () => useCadSolverFrameStore.getState().frames.wgi_1;

describe('chosenFrameAxis', () => {
  beforeEach(() => resetCadSolverFrameStore());

  it("names no axis for WG's own decision, confident or fallback: the backend decides", () => {
    show(frame('+y'));
    expect(shown().axis).toBe('+y');
    expect(chosenFrameAxis(shown())).toBeNull();
    show(frame('+z', null, 'fallback'));
    expect(shown().axis).toBe('+z');
    expect(chosenFrameAxis(shown())).toBeNull();
  });

  it("names the user's pick (Change) rather than the preselection", () => {
    show(frame('+z', null, 'fallback'));
    useCadSolverFrameStore.getState().pick('wgi_1', '-x');
    expect(chosenFrameAxis(shown())).toBe('-x');
  });

  it("names the project's own frame, confirmed or carried from v1", () => {
    show(frame('+y', '+y'));
    expect(chosenFrameAxis(shown())).toBe('+y');
    show(frame('-x', null, 'carried'));
    expect(chosenFrameAxis(shown())).toBe('-x');
  });

  it('names nothing when the card never showed a frame, failed, shows none, or shows a linked model', () => {
    expect(chosenFrameAxis(undefined)).toBeNull();
    show(frame(null));
    expect(shown().axis).toBeNull();
    expect(chosenFrameAxis(shown())).toBeNull();
    show({ linked: true });
    expect(chosenFrameAxis(shown())).toBeNull();
    useCadSolverFrameStore.setState({ frames: { wgi_1: {
      ingestId: 'wgi_1', status: 'error', frame: frame('+z', '+z'), linked: false, axis: '+z', picked: true,
      changedFrom: null, error: 'unreachable',
    } } });
    expect(chosenFrameAxis(shown())).toBeNull();
  });
});
