import { afterEach, describe, expect, it } from 'vitest';
import { resetSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { subscribeSolveSettingsEdits } from './solveSettingsEdits';

describe('solve settings edits', () => {
  afterEach(() => resetSolveOptionsStore());

  it('signals a plane toggled, and nothing for a toggle that changes nothing', () => {
    resetSolveOptionsStore();
    useSolveOptionsStore.setState((state) => ({ polar: { ...state.polar, enabledAxes: ['horizontal'] } }));
    let edits = 0;
    const stop = subscribeSolveSettingsEdits(() => { edits += 1; });
    // The last plane stays on, so nothing changed and nothing was chosen.
    useSolveOptionsStore.getState().toggleAxis('horizontal');
    expect(useSolveOptionsStore.getState().polar.enabledAxes).toEqual(['horizontal']);
    expect(edits).toBe(0);
    useSolveOptionsStore.getState().toggleAxis('vertical');
    expect(useSolveOptionsStore.getState().polar.enabledAxes).toEqual(['horizontal', 'vertical']);
    expect(edits).toBe(1);
    useSolveOptionsStore.getState().setFrequencySpacing('linear');
    expect(edits).toBe(2);
    stop();
  });
});
