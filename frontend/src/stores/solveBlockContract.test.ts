import { beforeEach, describe, expect, it } from 'vitest';
import defaultsOnly from '../../../server/tests/fixtures/solve_blocks/defaults-only.json';
import engineSymmetrySpacing from '../../../server/tests/fixtures/solve_blocks/engine-symmetry-spacing.json';
import explicitFrequencyList from '../../../server/tests/fixtures/solve_blocks/explicit-frequency-list.json';
import fieldPlaneOff from '../../../server/tests/fixtures/solve_blocks/field-plane-off.json';
import malformedFrequencyList from '../../../server/tests/fixtures/solve_blocks/malformed-frequency-list.json';
import polarOverridesAxes from '../../../server/tests/fixtures/solve_blocks/polar-overrides-axes.json';
import polarSampleCap from '../../../server/tests/fixtures/solve_blocks/polar-sample-cap.json';
import {
  resetSolveOptionsStore,
  restoreSolveSettingsFromBlocks,
  useSolveOptionsStore,
} from './solveOptions';

const fixtures = [
  defaultsOnly,
  engineSymmetrySpacing,
  explicitFrequencyList,
  malformedFrequencyList,
  polarOverridesAxes,
  fieldPlaneOff,
  polarSampleCap,
];

describe('solve block reader contract', () => {
  beforeEach(() => {
    localStorage.clear();
    resetSolveOptionsStore();
  });

  for (const [index, fixture] of fixtures.entries()) {
    it(`matches shared fixture ${index + 1}`, () => {
      restoreSolveSettingsFromBlocks(fixture.blocks);
      expect(useSolveOptionsStore.getState().options()).toEqual(fixture.expected);
    });
  }
});
