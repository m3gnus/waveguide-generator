import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { incrementTrailingDigits, nextRunName, runNameFromFilename } from './runNaming';
import { projectSubmittedDesign } from './submittedProjection';

function projection() {
  resetSolveOptionsStore();
  return projectSubmittedDesign(designForFamily('R-OSSE'), useSolveOptionsStore.getState().options());
}

describe('design-tracking run names', () => {
  it('increments trailing digits in place and preserves padding', () => {
    expect(incrementTrailingDigits('asro68')).toBe('asro69');
    expect(incrementTrailingDigits('horn_007')).toBe('horn_008');
    expect(incrementTrailingDigits('horn_999')).toBe('horn_1000');
  });

  it('appends 2 when no trailing digits exist', () => {
    expect(incrementTrailingDigits('winner')).toBe('winner2');
  });

  it('keeps the same name for an unchanged submitted design', () => {
    const baseline = projection();
    expect(nextRunName({ outputName: 'asro68', nameSourceProjection: baseline }, structuredClone(baseline)))
      .toBe('asro68');
  });

  it('increments only after the submitted design changes', () => {
    const baseline = projection();
    const changed = structuredClone(baseline);
    changed.design.scale = 2;
    expect(nextRunName({ outputName: 'asro68', nameSourceProjection: baseline }, changed)).toBe('asro69');
  });

  it('uses a file stem, then horn for an untitled document, before a baseline exists', () => {
    const current = projection();
    expect(nextRunName({ outputName: 'stale', nameSourceProjection: null }, current, '/tmp/My horn.cfg')).toBe('My horn');
    expect(nextRunName({ outputName: 'stale', nameSourceProjection: null }, current, '')).toBe('horn');
    expect(runNameFromFilename('horn_007.mwg')).toBe('horn_007');
  });

  it('lets a manual edit reset the baseline for later increments', () => {
    const baseline = projection();
    const manual = { outputName: 'winner', nameSourceProjection: baseline };
    expect(nextRunName(manual, baseline)).toBe('winner');
    const changed = structuredClone(baseline);
    changed.design.scale = 1.1;
    expect(nextRunName(manual, changed)).toBe('winner2');
  });
});
