import test from 'node:test';
import assert from 'node:assert/strict';

import { resolvePhaseTimeConvention } from '../src/results/conventions.js';

test('resolvePhaseTimeConvention preserves explicit, engine, and backend mappings', () => {
  const cases = [
    {
      label: 'explicit negative convention takes precedence',
      metadata: { phase_time_convention: 'exp(-ikr)', solver_backend: 'metal' },
      expected: 'bempp',
    },
    {
      label: 'positive spatial convention accepts underscore spelling',
      metadata: { phase_time_convention: 'positive_spatial' },
      expected: 'metal',
    },
    {
      label: 'engine mapping',
      metadata: { engine: 'hornlab-bempp-bem' },
      expected: 'metal',
    },
    {
      label: 'selected device mapping',
      metadata: { device_interface: { selected: 'bempp_cl_numba' } },
      expected: 'metal',
    },
    {
      label: 'metal backend mapping',
      metadata: { solver_backend: 'hornlab-metal-bem' },
      expected: 'metal',
    },
    {
      label: 'bempp backend mapping',
      metadata: { solver_backend: 'bempp-cl' },
      expected: 'bempp',
    },
    {
      label: 'metal metadata fallback',
      metadata: { metal: {} },
      expected: 'metal',
    },
    {
      label: 'unknown metadata',
      metadata: {},
      expected: null,
    },
  ];

  for (const { label, metadata, expected } of cases) {
    assert.equal(resolvePhaseTimeConvention({ metadata }), expected, label);
  }
});
