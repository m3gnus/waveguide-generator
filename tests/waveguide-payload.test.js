import test from 'node:test';
import assert from 'node:assert/strict';

import { buildWaveguidePayload } from '../src/solver/waveguidePayload.js';

test('buildWaveguidePayload maps adaptive mesh resolution fields', () => {
  const payload = buildWaveguidePayload(
    {
      type: 'OSSE',
      throatResolution: 4,
      mouthResolution: 9,
      rearResolution: 12,
      encFrontResolution: '6,7,8,9',
      encBackResolution: '11,12,13,14',
      quadrants: '1234'
    },
    '2.2'
  );

  assert.equal(payload.formula_type, 'OSSE');
  assert.equal(payload.throat_res, 4);
  assert.equal(payload.mouth_res, 9);
  assert.equal(payload.rear_res, 12);
  assert.equal(payload.enc_front_resolution, '6,7,8,9');
  assert.equal(payload.enc_back_resolution, '11,12,13,14');
  assert.equal(payload.subdomain_slices, undefined);
  assert.equal(payload.interface_offset, undefined);
  assert.equal(payload.interface_draw, undefined);
  assert.equal(payload.interface_resolution, undefined);
  assert.equal(payload.quadrants, 1234);
  assert.equal(payload.msh_version, '2.2');
});

test('buildWaveguidePayload applies requested mesh defaults when fields are omitted', () => {
  const payload = buildWaveguidePayload(
    {
      type: 'OSSE',
      quadrants: '1234'
    },
    '2.2'
  );

  assert.equal(payload.throat_res, 6);
  assert.equal(payload.mouth_res, 15);
  assert.equal(payload.rear_res, 40);
  assert.equal(payload.enc_front_resolution, '25,25,25,25');
  assert.equal(payload.enc_back_resolution, '40,40,40,40');
});

test('buildWaveguidePayload preserves R-OSSE b expression strings', () => {
  const bExpr = () => 0;
  bExpr._rawExpr = '0.2+0.1*sin(p)';

  const payload = buildWaveguidePayload(
    {
      type: 'R-OSSE',
      R: '140',
      a: '45',
      b: bExpr
    },
    '2.2'
  );

  assert.equal(payload.b, '0.2+0.1*sin(p)');
});

test('buildWaveguidePayload coerces non-finite numeric fields to finite defaults', () => {
  const payload = buildWaveguidePayload(
    {
      type: 'OSSE',
      throatExtAngle: 'sin(p)',
      gcurveDist: 'bad-value',
      morphRate: 'nan-value',
      angularSegments: 'oops',
      wallThickness: 'invalid',
      encDepth: 'none',
      encEdgeType: 'bad'
    },
    '2.2'
  );

  assert.equal(payload.throat_ext_angle, 0);
  assert.equal(payload.gcurve_dist, 0.5);
  assert.equal(payload.morph_rate, 3.0);
  assert.equal(payload.n_angular, 100);
  assert.equal(payload.wall_thickness, 6.0);
  assert.equal(payload.enc_depth, 0);
  assert.equal(payload.enc_edge_type, 1);
});
