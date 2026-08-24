import { beforeEach, describe, expect, it } from 'vitest';
import { documentSettingsSignature } from './designWire';
import { resetSolveOptionsStore, useSolveOptionsStore } from './solveOptions';

/**
 * `documentSettingsSignature` runs as a Zustand selector on the render path
 * (the unsaved-changes hook and the CAD freshness check), so it must be total.
 * It used to throw for any directivity grid `polarConfigFromUi` refuses --
 * a 0..0 sweep typed into the panel unmounted the entire app.
 */
describe('documentSettingsSignature', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  it('does not throw for a degenerate sweep the solve path refuses', () => {
    useSolveOptionsStore.getState().updatePolar({ angleStart: 0, angleEnd: 0 });
    expect(() => documentSettingsSignature()).not.toThrow();
    expect(typeof documentSettingsSignature()).toBe('string');
  });

  it('still tracks polar edits while the grid is invalid', () => {
    useSolveOptionsStore.getState().updatePolar({ angleStart: 0, angleEnd: 0 });
    const degenerate = documentSettingsSignature();
    // An edit to another rig field is still a document change while the pair
    // is broken: the unsaved indicator must keep following the user's typing.
    useSolveOptionsStore.getState().updatePolar({ distance: 3.5 });
    expect(documentSettingsSignature()).not.toBe(degenerate);
    // And repairing the sweep changes it again, back onto the resolved form.
    useSolveOptionsStore.getState().updatePolar({ angleEnd: 180 });
    expect(documentSettingsSignature()).not.toBe(degenerate);
  });

  it('is stable for an unchanged state, valid or not', () => {
    expect(documentSettingsSignature()).toBe(documentSettingsSignature());
    useSolveOptionsStore.getState().updatePolar({ angleStart: 90, angleEnd: 30 });
    expect(documentSettingsSignature()).toBe(documentSettingsSignature());
  });
});
