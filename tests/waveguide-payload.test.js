import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { buildWaveguidePayload } from '../src/solver/waveguidePayload.js';
import { prepareBackendMeshSimulationParams } from '../src/modules/design/index.js';
import { prepareGeometryParams } from '../src/geometry/params.js';

test('buildWaveguidePayload maps adaptive mesh resolution fields', () => {
  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'OSSE',
      throatResolution: 4,
      mouthResolution: 9,
      rearResolution: 12,
      apertureResolutionScale: 2.25,
      maxTriangles: 24000,
      allowLargeMesh: true,
      encFrontResolution: '6,7,8,9',
      encBackResolution: '11,12,13,14',
      quadrants: '1234',
    }),
    '2.2'
  );

  assert.equal(payload.formula_type, 'OSSE');
  assert.equal(payload.throat_res, 4);
  assert.equal(payload.mouth_res, 9);
  assert.equal(payload.rear_res, 12);
  assert.equal(payload.aperture_resolution_scale, 2.25);
  assert.equal(payload.max_triangles, 24000);
  assert.equal(payload.allow_large_mesh, true);
  assert.equal(payload.enc_front_resolution, '6,7,8,9');
  assert.equal(payload.enc_back_resolution, '11,12,13,14');
  assert.equal(payload.subdomain_slices, undefined);
  assert.equal(payload.interface_offset, undefined);
  assert.equal(payload.interface_draw, undefined);
  assert.equal(payload.interface_resolution, undefined);
  assert.equal(payload.quadrants, 1234);
  assert.equal(payload.msh_version, '2.2');
});

test('buildWaveguidePayload uses DesignModule backend mesh defaults when fields are omitted', () => {
  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'OSSE',
    }),
    '2.2'
  );

  assert.equal(payload.n_angular, 100);
  assert.equal(payload.n_length, 20);
  assert.equal(payload.quadrants, 1234);
  assert.equal(payload.throat_res, 6);
  assert.equal(payload.mouth_res, 15);
  assert.equal(payload.rear_res, 40);
  assert.equal(payload.aperture_resolution_scale, 1.5);
  assert.equal(payload.max_triangles, 50000);
  assert.equal(payload.allow_large_mesh, false);
  assert.equal(payload.enc_front_resolution, '25,25,25,25');
  assert.equal(payload.enc_back_resolution, '40,40,40,40');
});

test('buildWaveguidePayload preserves R-OSSE b expression strings', () => {
  const bExpr = () => 0;
  bExpr._rawExpr = '0.2+0.1*sin(p)';

  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'R-OSSE',
      R: '140',
      a: '45',
      b: bExpr,
    }),
    '2.2'
  );

  assert.equal(payload.b, '0.2+0.1*sin(p)');
});

test('buildWaveguidePayload preserves angular slot-length expression strings', () => {
  const slotLength = () => 0;
  slotLength._rawExpr = '45 - 42*sin(2*p)^4';

  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'OSSE',
      slotLength,
    }),
    '2.2'
  );

  assert.equal(payload.slot_length, '45 - 42*sin(2*p)^4');
});

test('buildWaveguidePayload preserves ATH total-length mode', () => {
  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'OSSE',
      _athLengthMode: 'total',
    }),
    '2.2'
  );

  assert.equal(payload.length_mode, 'total');
});

test('buildWaveguidePayload emits ICW coverage controls when enabled', () => {
  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      ...getDefaults('ICW'),
      type: 'ICW',
      coverage_angle: 50,
      hold_start: 0.3,
      hold_end: 0.7,
    }),
    '2.2'
  );

  assert.equal(payload.formula_type, 'ICW');
  assert.equal(payload.coverage_angle, 50);
  assert.equal(payload.hold_start, 0.3);
  assert.equal(payload.hold_end, 0.7);
});

test('buildWaveguidePayload keeps ICW coverage off by default and OSSE/R-OSSE JSON unchanged', () => {
  const icwPayload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      ...getDefaults('ICW'),
      type: 'ICW',
    }),
    '2.2'
  );

  assert.equal(icwPayload.formula_type, 'ICW');
  assert.equal(icwPayload.coverage_angle, 0);
  assert.equal(icwPayload.hold_start, 0.3);
  assert.equal(icwPayload.hold_end, 0.7);

  const ossePayload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'OSSE',
    }),
    '2.2'
  );
  const osseJson = JSON.stringify(ossePayload);
  assert.equal(osseJson.includes('coverage_angle'), false);
  assert.equal(osseJson.includes('hold_start'), false);
  assert.equal(osseJson.includes('hold_end'), false);

  const rossePayload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'R-OSSE',
    }),
    '2.2'
  );
  const rosseJson = JSON.stringify(rossePayload);
  assert.equal(rosseJson.includes('coverage_angle'), false);
  assert.equal(rosseJson.includes('hold_start'), false);
  assert.equal(rosseJson.includes('hold_end'), false);
});

test('buildWaveguidePayload emits isolated FREEFORM blocks with snake_case fields', () => {
  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      ...getDefaults('FREEFORM'),
      type: 'FREEFORM',
      length: 120,
      throatRadius: 12.7,
      throatAngle: 16,
      mouthRadiusH: 160,
      mouthRadiusV: 110,
      interiorH: [[130, 999], { z: 60, r: 80, angleDeg: 25, strength: 1.4 }, [-2, 999]],
      interiorV: [{ z: 90, r: 85, angleDeg: null, strength: 2 }, [60, 60, -10]],
      mouthAngleH: 70,
      throatTangentScaleH: 1.1,
      mouthTangentScaleH: 0.9,
      mouthAngleV: 60,
      throatTangentScaleV: 1.2,
      mouthTangentScaleV: 0.8,
      crossSections: [
        { t: 0, shape: 'circle' },
        { t: 0.4, shape: 'superellipse', exponent: 4 },
        { t: 1, shape: 'rounded_rectangle', cornerRadiusMm: 10 },
      ],
      overshootPolicy: 'allow',
      inflectionPolicy: 'reject',
    }),
    '2.2'
  );

  assert.equal(payload.formula_type, 'FREEFORM');
  assert.deepEqual(payload.profile_h, {
    points: [
      [0, 12.7],
      [60, 80, 25, 1.4],
      [120, 160],
    ],
    throat_angle_deg: 16,
    mouth_angle_deg: 70,
    throat_tangent_scale: 1.1,
    mouth_tangent_scale: 0.9,
  });
  assert.deepEqual(payload.profile_v, {
    points: [
      [0, 12.7],
      [60, 60, -10],
      [90, 85],
      [120, 110],
    ],
    throat_angle_deg: 16,
    mouth_angle_deg: 60,
    throat_tangent_scale: 1.2,
    mouth_tangent_scale: 0.8,
  });
  assert.deepEqual(payload.cross_sections, [
    { t: 0, shape: 'circle' },
    { t: 0.4, shape: 'superellipse', exponent: 4 },
    { t: 1, shape: 'rounded_rectangle', corner_radius_mm: 10 },
  ]);
  assert.equal(payload.overshoot_policy, 'allow');
  assert.equal(payload.inflection_policy, 'reject');
  for (const key of ['R', 'r', 'b', 'm', 'tmax', 'L', 's', 'n', 'h', 'a', 'a0', 'r0', 'k', 'q']) {
    assert.equal(Object.hasOwn(payload, key), false, `${key} must not leak into FREEFORM`);
  }
  assert.equal(payload.source_radius, getDefaults('FREEFORM').sourceRadius);
  assert.equal(payload.enc_depth, getDefaults('FREEFORM').encDepth);
});

test('legacy FREEFORM profiles migrate to the new model without changing their wire payload', () => {
  const prepared = prepareGeometryParams({
    type: 'FREEFORM',
    profileH: [
      [0, 12.7],
      [40, 55],
      [120, 160],
    ],
    profileV: [
      [0, 12.7],
      [70, 68],
      [120, 110],
    ],
    throatAngleH: 16,
    throatAngleV: 16,
    mouthAngleH: 70,
    mouthAngleV: 60,
    throatTangentScaleH: 1.1,
    mouthTangentScaleH: 0.9,
    throatTangentScaleV: 1.2,
    mouthTangentScaleV: 0.8,
  });
  const payload = buildWaveguidePayload(prepareBackendMeshSimulationParams(prepared), '2.2');

  assert.equal(prepared.length, 120);
  assert.equal(prepared.throatRadius, 12.7);
  assert.equal(prepared.throatAngle, 16);
  assert.equal(prepared.mouthRadiusH, 160);
  assert.equal(prepared.mouthRadiusV, 110);
  assert.deepEqual(prepared.interiorH, [{ z: 40, r: 55, angleDeg: null, strength: null }]);
  assert.deepEqual(prepared.interiorV, [{ z: 70, r: 68, angleDeg: null, strength: null }]);
  assert.equal(Object.hasOwn(prepared, 'profileH'), false);
  assert.equal(Object.hasOwn(prepared, 'profileV'), false);
  assert.equal(Object.hasOwn(prepared, 'throatAngleH'), false);
  assert.equal(Object.hasOwn(prepared, 'throatAngleV'), false);
  assert.deepEqual(payload.profile_h, {
    points: [
      [0, 12.7],
      [40, 55],
      [120, 160],
    ],
    throat_angle_deg: 16,
    mouth_angle_deg: 70,
    throat_tangent_scale: 1.1,
    mouth_tangent_scale: 0.9,
  });
  assert.deepEqual(payload.profile_v, {
    points: [
      [0, 12.7],
      [70, 68],
      [120, 110],
    ],
    throat_angle_deg: 16,
    mouth_angle_deg: 60,
    throat_tangent_scale: 1.2,
    mouth_tangent_scale: 0.8,
  });
});

test('buildWaveguidePayload rejects unprepared backend mesh payload fields', () => {
  assert.throws(
    () =>
      buildWaveguidePayload(
        {
          type: 'OSSE',
          angularSegments: 20,
        },
        '2.2'
      ),
    /requires finite "lengthSegments"/
  );
});

test('buildWaveguidePayload receives rounded backend mesh segments from DesignModule normalization', () => {
  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'OSSE',
      angularSegments: 21.2,
      lengthSegments: 9.7,
      cornerSegments: 4.5,
    }),
    '2.2'
  );

  assert.equal(payload.n_angular, 21);
  assert.equal(payload.n_length, 10);
  assert.equal(payload.corner_segments, 5);
});

test('buildWaveguidePayload rejects fractional corner segments on the direct path', () => {
  const prepared = prepareBackendMeshSimulationParams({ type: 'OSSE' });

  assert.throws(
    () => buildWaveguidePayload({ ...prepared, cornerSegments: 4.5 }, '2.2'),
    /requires integer "cornerSegments"/
  );
});

test('buildWaveguidePayload preserves valid reduced-domain quadrants from DesignModule normalization', () => {
  assert.equal(
    buildWaveguidePayload(
      prepareBackendMeshSimulationParams({ type: 'OSSE', quadrants: '14' }),
      '2.2'
    ).quadrants,
    14
  );
  assert.equal(
    buildWaveguidePayload(
      prepareBackendMeshSimulationParams({ type: 'OSSE', quadrants: '12' }),
      '2.2'
    ).quadrants,
    12
  );
  assert.equal(
    buildWaveguidePayload(
      prepareBackendMeshSimulationParams({ type: 'OSSE', quadrants: '1' }),
      '2.2'
    ).quadrants,
    1
  );
  assert.equal(
    buildWaveguidePayload(
      prepareBackendMeshSimulationParams({ type: 'OSSE', quadrants: 'not-a-quadrant' }),
      '2.2'
    ).quadrants,
    1
  );
  assert.equal(
    buildWaveguidePayload(
      prepareBackendMeshSimulationParams({ type: 'OSSE', quadrants: '1234x' }),
      '2.2'
    ).quadrants,
    1234
  );
  assert.equal(
    buildWaveguidePayload(
      prepareBackendMeshSimulationParams({ type: 'OSSE', quadrants: '1,2' }),
      '2.2'
    ).quadrants,
    1
  );
});

test('buildWaveguidePayload includes source definition fields', () => {
  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'R-OSSE',
      sourceShape: 1,
      sourceRadius: 14.5,
      sourceCurv: -1,
      sourceVelocity: 2,
      sourceContours: 'custom-contours',
      verticalOffset: 3.5,
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
    prepareBackendMeshSimulationParams({ type: 'R-OSSE' }),
    '2.2'
  );

  assert.equal(payload.source_shape, 2);
  assert.equal(payload.source_radius, -1);
  assert.equal(payload.source_curv, 0);
  assert.equal(payload.source_velocity, 1);
  assert.equal(payload.source_contours, undefined);
  assert.equal(payload.vertical_offset, 0);
});

test('buildWaveguidePayload includes solver mode', () => {
  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'OSSE',
      solverMode: 'circsym',
    }),
    '2.2'
  );

  assert.equal(payload.solver_mode, 'circsym');
});

test('buildWaveguidePayload stringifies enclosure resolution lists', () => {
  const payload = buildWaveguidePayload(
    prepareBackendMeshSimulationParams({
      type: 'OSSE',
      encFrontResolution: [7, 8, 9, 10],
      encBackResolution: [11, 12, 13, 14],
    }),
    '2.2'
  );

  assert.equal(payload.enc_front_resolution, '7,8,9,10');
  assert.equal(payload.enc_back_resolution, '11,12,13,14');
});
