import test from 'node:test';
import assert from 'node:assert/strict';

import { buildWaveguidePayload } from '../src/solver/waveguidePayload.js';
import { prepareOccSimulationParams } from '../src/modules/design/index.js';

test('buildWaveguidePayload maps adaptive mesh resolution fields', () => {
  const payload = buildWaveguidePayload(
    prepareOccSimulationParams({
      type: 'OSSE',
      throatResolution: 4,
      mouthResolution: 9,
      rearResolution: 12,
      encFrontResolution: '6,7,8,9',
      encBackResolution: '11,12,13,14',
      quadrants: '1234'
    }),
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
  // quadrants is not forwarded — OCC always builds full-domain meshes.
  assert.equal(payload.quadrants, undefined);
  assert.equal(payload.msh_version, '2.2');
});

test('buildWaveguidePayload uses DesignModule OCC simulation defaults when fields are omitted', () => {
  const payload = buildWaveguidePayload(
    prepareOccSimulationParams({
      type: 'OSSE'
    }),
    '2.2'
  );

  assert.equal(payload.n_angular, 100);
  assert.equal(payload.n_length, 20);
  // quadrants is not forwarded — OCC always builds full-domain meshes.
  assert.equal(payload.quadrants, undefined);
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
    prepareOccSimulationParams({
      type: 'R-OSSE',
      R: '140',
      a: '45',
      b: bExpr
    }),
    '2.2'
  );

  assert.equal(payload.b, '0.2+0.1*sin(p)');
});

test('buildWaveguidePayload rejects unprepared OCC payload fields', () => {
  assert.throws(
    () => buildWaveguidePayload(
      {
        type: 'OSSE',
        angularSegments: 20
      },
      '2.2'
    ),
    /requires finite "lengthSegments"/
  );
});

test('buildWaveguidePayload receives rounded OCC segments from DesignModule normalization', () => {
  const payload = buildWaveguidePayload(
    prepareOccSimulationParams({
      type: 'OSSE',
      angularSegments: 21.2,
      lengthSegments: 9.7
    }),
    '2.2'
  );

  assert.equal(payload.n_angular, 21);
  assert.equal(payload.n_length, 10);
});

test('buildWaveguidePayload does not forward quadrants — OCC is always full-domain', () => {
  // quadrants is no longer included in the OCC payload regardless of input value.
  assert.equal(
    buildWaveguidePayload(
      prepareOccSimulationParams({ type: 'OSSE', quadrants: '14' }),
      '2.2'
    ).quadrants,
    undefined
  );
  assert.equal(
    buildWaveguidePayload(
      prepareOccSimulationParams({ type: 'OSSE', quadrants: '12' }),
      '2.2'
    ).quadrants,
    undefined
  );
  assert.equal(
    buildWaveguidePayload(
      prepareOccSimulationParams({ type: 'OSSE', quadrants: 'not-a-quadrant' }),
      '2.2'
    ).quadrants,
    undefined
  );
});

test('buildWaveguidePayload includes source definition fields', () => {
  const payload = buildWaveguidePayload(
    prepareOccSimulationParams({
      type: 'R-OSSE',
      sourceShape: 1,
      sourceRadius: 14.5,
      sourceCurv: -1,
      sourceVelocity: 2,
      sourceContours: 'custom-contours',
      verticalOffset: 3.5
    }),
    '2.2'
  );

  assert.equal(payload.source_shape, 1);
  assert.equal(payload.source_radius, 14.5);
  assert.equal(payload.source_curv, -1);
  assert.equal(payload.source_velocity, 2);
  assert.equal(payload.source_contours, 'custom-contours');
  assert.equal(payload.vertical_offset, 3.5);
});

test('buildWaveguidePayload uses defaults for source definition fields when omitted', () => {
  const payload = buildWaveguidePayload(
    prepareOccSimulationParams({ type: 'R-OSSE' }),
    '2.2'
  );

  assert.equal(payload.source_shape, 2);
  assert.equal(payload.source_radius, -1);
  assert.equal(payload.source_curv, 0);
  assert.equal(payload.source_velocity, 1);
  assert.equal(payload.source_contours, undefined);
  assert.equal(payload.vertical_offset, 0);
});

test('buildWaveguidePayload stringifies enclosure resolution lists', () => {
  const payload = buildWaveguidePayload(
    prepareOccSimulationParams({
      type: 'OSSE',
      encFrontResolution: [7, 8, 9, 10],
      encBackResolution: [11, 12, 13, 14]
    }),
    '2.2'
  );

  assert.equal(payload.enc_front_resolution, '7,8,9,10');
  assert.equal(payload.enc_back_resolution, '11,12,13,14');
});
