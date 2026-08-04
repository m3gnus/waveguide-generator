import test from 'node:test';
import assert from 'node:assert/strict';

import { resolvePhaseTimeConvention } from '../src/results/conventions.js';

test('resolvePhaseTimeConvention preserves explicit, engine, and backend mappings', () => {
  const cases = [
    {
      label: 'explicit negative convention takes precedence',
      metadata: { phase_time_convention: 'exp(-ikr)', solver_backend: 'metal' },
      expected: 'exp(-ikr)',
    },
    {
      label: 'positive spatial convention accepts underscore spelling',
      metadata: { phase_time_convention: 'positive_spatial' },
      expected: 'exp(+ikr)',
    },
    {
      label: 'explicit Bempp backend alias reflects its positive outgoing kernel',
      metadata: { phase_time_convention: 'bempp-cl-opencl' },
      expected: 'exp(+ikr)',
    },
    {
      label: 'legacy marker remains explicitly negative',
      metadata: { phase_time_convention: 'legacy' },
      expected: 'exp(-ikr)',
    },
    {
      label: 'engine mapping',
      metadata: { engine: 'hornlab-bempp-bem' },
      expected: 'exp(+ikr)',
    },
    {
      label: 'selected device mapping',
      metadata: { device_interface: { selected: 'bempp_cl_numba' } },
      expected: 'exp(+ikr)',
    },
    {
      label: 'metal backend mapping',
      metadata: { solver_backend: 'hornlab-metal-bem' },
      expected: 'exp(+ikr)',
    },
    {
      label: 'bempp backend mapping',
      metadata: { solver_backend: 'bempp-cl' },
      expected: 'exp(+ikr)',
    },
    {
      label: 'metal metadata fallback',
      metadata: { metal: {} },
      expected: 'exp(+ikr)',
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
