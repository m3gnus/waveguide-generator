import { beforeEach, describe, expect, it } from 'vitest';
import type { ResultData } from '../api/results';
import { rigSpecFromSolveOptions } from './ObservationOverlay';
import type { ObservationFrameBasis } from './observationRig';
import { useObservationStore } from './observationStore';

const basis: ObservationFrameBasis = {
  axis: [0, 0, 1],
  u: [1, 0, 0],
  v: [0, 1, 0],
  origin_m: [0, 0, 0.19],
  mouth_center_m: [0, 0, 0.19],
  source_center_m: [0, 0, 0],
};

const withBasis = (frame: unknown = basis): ResultData =>
  ({ frequencies: [], metadata: { observation_frame_basis: frame } } as unknown as ResultData);

describe('useObservationStore', () => {
  beforeEach(() => {
    useObservationStore.setState({ visible: false, basis: null, sourceLabel: null, hovered: null });
  });

  it('adopts the frame a run published, with the run it came from', () => {
    useObservationStore.getState().adopt(withBasis(), 'horn_v03');
    expect(useObservationStore.getState().basis).toEqual(basis);
    expect(useObservationStore.getState().sourceLabel).toBe('horn_v03');
  });

  /**
   * The dock re-resolves its primary payload on every socket event, so a fresh
   * object identity here would rebuild the rig geometry -- and drop a hover in
   * progress -- several times a second while a solve streams.
   */
  it('holds its identity when an equal frame is adopted again', () => {
    const store = useObservationStore.getState();
    store.adopt(withBasis(), 'horn_v03');
    const first = useObservationStore.getState().basis;
    store.setHovered({ plane: 'horizontal', angleDeg: 30 });
    store.adopt(withBasis({ ...basis }), 'horn_v03');
    expect(useObservationStore.getState().basis).toBe(first);
    expect(useObservationStore.getState().hovered).toEqual({ plane: 'horizontal', angleDeg: 30 });
  });

  it('clears rather than keeps a stale frame when the run has none', () => {
    const store = useObservationStore.getState();
    store.adopt(withBasis(), 'horn_v03');
    store.adopt(undefined, null);
    expect(useObservationStore.getState().basis).toBeNull();
  });

  it('drops the hover when hidden', () => {
    const store = useObservationStore.getState();
    store.adopt(withBasis(), 'horn_v03');
    store.setVisible(true);
    store.setHovered({ plane: 'vertical', angleDeg: 45 });
    store.toggle();
    expect(useObservationStore.getState().visible).toBe(false);
    expect(useObservationStore.getState().hovered).toBeNull();
  });
});

describe('rigSpecFromSolveOptions', () => {
  const polar = {
    distance: 2,
    angleStart: 0,
    angleEnd: 90,
    angleStep: 5,
    enabledAxes: ['horizontal', 'vertical'],
    diagonalAngle: 45,
    observationOrigin: 'mouth',
  };

  it('resolves the sample count the way the solve does', () => {
    expect(rigSpecFromSolveOptions(polar).angleCount).toBe(19);
    expect(rigSpecFromSolveOptions({ ...polar, angleStep: 10 }).angleCount).toBe(10);
  });

  it('keeps only the planes the solve knows', () => {
    expect(rigSpecFromSolveOptions({ ...polar, enabledAxes: ['horizontal', 'nonsense'] }).planes)
      .toEqual(['horizontal']);
  });

  it('carries the pivot choice through', () => {
    expect(rigSpecFromSolveOptions({ ...polar, observationOrigin: 'throat' }).origin).toBe('throat');
    expect(rigSpecFromSolveOptions({ ...polar, observationOrigin: 'anything else' }).origin).toBe('mouth');
  });

  it('survives a degenerate sweep rather than dividing by zero', () => {
    expect(rigSpecFromSolveOptions({ ...polar, angleStep: 0 }).angleCount).toBe(2);
    expect(rigSpecFromSolveOptions({ ...polar, angleEnd: 0 }).angleCount).toBe(1);
  });
});
