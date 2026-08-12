import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { projectSubmittedDesign, submittedProjectionsEqual } from './submittedProjection';

function options() {
  resetSolveOptionsStore();
  return useSolveOptionsStore.getState().options();
}

describe('canonical submitted design projection', () => {
  it('is equal for cloned submitted state and ignores expression sidecars', () => {
    const design = designForFamily('R-OSSE');
    const first = projectSubmittedDesign(design, options());
    const clone = structuredClone(design);
    clone._expressions = { R: { value: design.R!, raw: '70 * 2' } };
    expect(submittedProjectionsEqual(first, projectSubmittedDesign(clone, options()))).toBe(true);
  });

  it('detects family scalar, solve-option, and simulation-block differences', () => {
    const design = designForFamily('R-OSSE');
    const solveOptions = options();
    const baseline = projectSubmittedDesign(design, solveOptions);
    const scalar = structuredClone(design); scalar.R! += 1;
    const simulation = structuredClone(design); simulation.simulation.f2 += 1_000;
    expect(submittedProjectionsEqual(baseline, projectSubmittedDesign(scalar, solveOptions))).toBe(false);
    expect(submittedProjectionsEqual(baseline, projectSubmittedDesign(simulation, solveOptions))).toBe(false);
    expect(submittedProjectionsEqual(baseline, projectSubmittedDesign(design, { ...solveOptions, verbose: true }))).toBe(false);
  });

  it('counts configured morph differences even when the target is None', () => {
    const design = designForFamily('R-OSSE');
    expect(design.morph.target_shape).toBe(0);
    const changed = structuredClone(design);
    changed.morph.target_width = 300;
    expect(submittedProjectionsEqual(
      projectSubmittedDesign(design, options()),
      projectSubmittedDesign(changed, options()),
    )).toBe(false);
  });
});
