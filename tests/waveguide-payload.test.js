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
