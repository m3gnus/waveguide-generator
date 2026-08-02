import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import {
  convertToFreeform,
  formatConversionReport,
} from '../src/modules/design/convertToFreeform.js';
import { prepareBackendViewportMesh } from '../src/modules/geometry/useCases.js';
import { getViewportStateCacheKey } from '../src/app/viewportCacheKey.js';
import { buildFreeformDisplayCurve } from '../src/geometry/freeformCurve.js';

function syntheticGrid(rows, { scale = 1, verticalOffset = 0, quadrants = 1234 } = {}) {
  const angles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
  const nLength = rows.H.length - 1;
  const inner = [];
  for (let i = 0; i < angles.length; i += 1) {
    const source = i === 1 ? rows.V : rows.H;
    for (const [z, r] of source) {
      inner.push(
        r * Math.cos(angles[i]) * scale,
        r * Math.sin(angles[i]) * scale + verticalOffset,
        z * scale
      );
    }
  }
  return {
    angle_list: angles,
    grid_n_phi: angles.length,
    grid_n_length: nLength,
    inner_points: inner,
    full_circle: true,
    quadrants,
    vertical_offset_mm: verticalOffset,
    slice_map: Array.from({ length: nLength + 1 }, (_, index) => index),
  };
}

function reusableViewportMesh(state, grid, preparedParams = {}) {
  const sourceState = { ...state, params: { ...state.params, quadrants: 1234 } };
  return {
    grid,
    preparedParams: { scale: 1, ...preparedParams },
    stateKey: getViewportStateCacheKey(sourceState),
  };
}

function sourceState(params = {}, type = 'OSSE') {
  return {
    type,
    params: { ...getDefaults(type), ...params },
  };
}

function segmentDistance(point, start, end) {
  const dz = end[0] - start[0];
  const dr = end[1] - start[1];
  const lengthSquared = dz * dz + dr * dr;
  const projection =
    lengthSquared > 0
      ? Math.max(
          0,
          Math.min(1, ((point[0] - start[0]) * dz + (point[1] - start[1]) * dr) / lengthSquared)
        )
      : 0;
  return Math.hypot(
    point[0] - (start[0] + projection * dz),
    point[1] - (start[1] + projection * dr)
  );
}

function maximumDistanceToPolyline(dense, polyline) {
  return Math.max(
    ...dense.map((point) =>
      Math.min(
        ...polyline
          .slice(0, -1)
          .map((start, index) => segmentDistance(point, start, polyline[index + 1]))
      )
    )
  );
}

function candidateCurve(params, plane, denseCount) {
  return buildFreeformDisplayCurve({
    points: [
      [0, params.throatRadius],
      ...params[`interior${plane}`].map((point) => [point.z, point.r]),
      [params.length, params[`mouthRadius${plane}`]],
    ],
    throatAngleDeg: params.throatAngle,
    mouthAngleDeg: params[`mouthAngle${plane}`],
    throatTangentScale: params[`throatTangentScale${plane}`],
    mouthTangentScale: params[`mouthTangentScale${plane}`],
    sampleCount: Math.max(192, denseCount * 4),
  });
}

test('convertToFreeform un-scales monotone meridians and clamps rectangle morph corners', async () => {
  const grid = syntheticGrid(
    {
      H: [
        [0, 10],
        [10, 12],
        [20, 18],
        [30, 28],
        [40, 40],
      ],
      V: [
        [0, 10],
        [10, 11],
        [20, 15],
        [30, 22],
        [40, 30],
      ],
    },
    { scale: 2 }
  );
  const state = sourceState({ scale: 2, morphTarget: 1, morphCorner: 99, quadrants: 12 });
  const result = await convertToFreeform(state, {
    viewportMesh: reusableViewportMesh(state, grid, { scale: 2 }),
    validateCandidate: false,
  });

  assert.equal(result.params.scale, 2);
  assert.equal(result.params.length, 40);
  assert.equal(result.params.throatRadius, 10);
  assert.equal(result.params.mouthRadiusH, 40);
  assert.equal(result.params.mouthRadiusV, 30);
  assert.ok(Math.abs(result.params.throatAngle - 11.309932) < 1e-5);
  assert.deepEqual(result.params.crossSections, [
    { t: 0, shape: 'circle' },
    { t: 1, shape: 'rounded_rectangle', cornerRadiusMm: 10 },
  ]);
  assert.equal(result.params.morphTarget, 0);
  assert.equal(result.params.quadrants, 12);
  assert.equal(result.report.truncatedMm, 0);
  assert.match(
    formatConversionReport(result.report),
    /^max deviation H .* mm, V .* mm; H\/V meridians only; off-axis cross-sections may differ$/
  );
});

test('convertToFreeform truncates non-monotone rollback and resamples the other mouth', async () => {
  const horizontal = Array.from({ length: 21 }, (_, index) => {
    const z = index * 2;
    return [z, 10 + 0.22 * z + 0.012 * z * z];
  });
  horizontal.push([34.9, 39]);
  const vertical = Array.from({ length: 22 }, (_, index) => {
    const z = index * 2;
    return [z, 10 + 0.16 * z + 0.008 * z * z];
  });
  const grid = syntheticGrid({
    H: horizontal,
    V: vertical,
  });
  const state = sourceState({ morphTarget: 2 });
  const result = await convertToFreeform(state, {
    viewportMesh: reusableViewportMesh(state, grid),
    validateCandidate: false,
  });

  assert.equal(result.params.length, 40);
  assert.equal(result.params.mouthRadiusH, 38);
  assert.equal(result.params.mouthRadiusV, 29.2);
  assert.deepEqual(result.params.crossSections, [
    { t: 0, shape: 'circle' },
    { t: 1, shape: 'ellipse' },
  ]);
  assert.equal(result.report.truncatedMm, 34.9);
  assert.match(formatConversionReport(result.report), /rollback lip 34\.9 mm dropped$/);
});

test('convertToFreeform enforces the 62-interior-anchor decimation cap', async () => {
  const dense = Array.from({ length: 101 }, (_, index) => [
    index,
    20 + index * 0.5 + (index % 2 === 0 ? 1 : -1),
  ]);
  const grid = syntheticGrid({ H: dense, V: dense });
  const state = sourceState();
  const result = await convertToFreeform(state, {
    viewportMesh: reusableViewportMesh(state, grid),
    validateCandidate: false,
    toleranceMm: 0.01,
  });

  assert.equal(result.report.anchorCountH, 62);
  assert.equal(result.report.anchorCountV, 62);
  assert.equal(result.params.interiorH.length, 62);
  assert.ok(result.report.maxDeviationMmH > 0.01);
  assert.ok(
    result.params.interiorH.every((point) => point.angleDeg === null && point.strength === null)
  );
});

test('R-OSSE conversion reports spline deviation instead of the smaller chord residual', async () => {
  const coefficients = [0.77, -0.803, 0.617, 0.632, 0.486];
  const horizontal = Array.from({ length: 121 }, (_, z) => {
    const t = z / 120;
    const radius = coefficients.reduce(
      (value, coefficient, index) => value + coefficient * 10 * Math.sin((index + 1) * Math.PI * t),
      12 + 100 * t
    );
    return [z, radius];
  });
  const vertical = Array.from({ length: 121 }, (_, z) => {
    const t = z / 120;
    return [z, 12 + 75 * t + 6 * Math.sin(2 * Math.PI * t)];
  });
  const state = sourceState({}, 'R-OSSE');
  const result = await convertToFreeform(state, {
    viewportMesh: reusableViewportMesh(state, syntheticGrid({ H: horizontal, V: vertical })),
    validateCandidate: false,
  });
  const anchorsH = [
    [0, result.params.throatRadius],
    ...result.params.interiorH.map((point) => [point.z, point.r]),
    [result.params.length, result.params.mouthRadiusH],
  ];
  const curveH = candidateCurve(result.params, 'H', horizontal.length);
  const curveV = candidateCurve(result.params, 'V', vertical.length);
  const expectedH = maximumDistanceToPolyline(horizontal, curveH);
  const expectedV = maximumDistanceToPolyline(vertical, curveV);
  const chordH = maximumDistanceToPolyline(horizontal, anchorsH);

  assert.ok(Math.abs(result.report.maxDeviationMmH - expectedH) < 1e-9);
  assert.ok(Math.abs(result.report.maxDeviationMmV - expectedV) < 1e-9);
  assert.ok(result.report.maxDeviationMmH > chordH * 1.5);
  assert.ok(Math.abs(result.report.maxDeviationMmH - expectedV) > 0.05);
  assert.ok(Math.abs(result.report.maxDeviationMmV - expectedH) > 0.05);
});

test('OSSE conversion pins the opposite spline-vs-chord deviation direction', async () => {
  const dense = Array.from({ length: 121 }, (_, z) => [
    z,
    10 + 100 * (1 - Math.cos((Math.PI * z) / 240)),
  ]);
  const state = sourceState({}, 'OSSE');
  const result = await convertToFreeform(state, {
    viewportMesh: reusableViewportMesh(state, syntheticGrid({ H: dense, V: dense })),
    validateCandidate: false,
  });
  const anchors = [
    [0, result.params.throatRadius],
    ...result.params.interiorH.map((point) => [point.z, point.r]),
    [result.params.length, result.params.mouthRadiusH],
  ];
  const chordDeviation = maximumDistanceToPolyline(dense, anchors);

  assert.ok(result.report.maxDeviationMmH < chordDeviation * 0.5);
  assert.ok(chordDeviation > 0.1);
});

test('convertToFreeform requests a full-domain smooth grid when no cached grid is available', async () => {
  const grid = syntheticGrid({
    H: [
      [0, 10],
      [20, 20],
    ],
    V: [
      [0, 10],
      [20, 18],
    ],
  });
  let requestedState;
  let requestedOptions;
  await convertToFreeform(sourceState({ quadrants: 1 }), {
    prepareViewportMesh: async (state, options) => {
      requestedState = state;
      requestedOptions = options;
      return { grid, preparedParams: { scale: 1 } };
    },
    validateCandidate: false,
  });

  assert.equal(requestedState.params.quadrants, 1234);
  assert.equal(requestedOptions.variant, 'smooth');
});

test('convertToFreeform removes rigid vertical offset after scale without dropping the placement', async () => {
  const rows = {
    H: [
      [0, 20],
      [40, 20],
    ],
    V: [
      [0, 20],
      [40, 20],
    ],
  };
  for (const verticalOffset of [0, 10]) {
    const state = sourceState({ scale: 2, verticalOffset });
    const grid = syntheticGrid(rows, { scale: 2, verticalOffset });
    const result = await convertToFreeform(state, {
      viewportMesh: reusableViewportMesh(state, grid, { scale: 2, verticalOffset }),
      validateCandidate: false,
    });
    assert.ok(Math.abs(result.params.throatRadius - 20) < 1e-9);
    assert.ok(Math.abs(result.params.mouthRadiusH - 20) < 1e-9);
    assert.ok(Math.abs(result.params.mouthRadiusV - 20) < 1e-9);
    assert.equal(result.params.verticalOffset, verticalOffset);
  }
});

test('convertToFreeform rejects stale, malformed, and symmetry-reduced cached grids', async () => {
  const state = sourceState({ L: 80, quadrants: 1 });
  const freshGrid = syntheticGrid({
    H: [
      [0, 10],
      [80, 50],
    ],
    V: [
      [0, 10],
      [80, 40],
    ],
  });
  const staleGrid = syntheticGrid({
    H: [
      [0, 10],
      [40, 20],
    ],
    V: [
      [0, 10],
      [40, 20],
    ],
  });
  const cases = [
    { ...reusableViewportMesh(state, staleGrid), stateKey: 'stale' },
    reusableViewportMesh(state, { ...staleGrid, inner_points: staleGrid.inner_points.slice(3) }),
    reusableViewportMesh(state, { ...staleGrid, full_circle: false, quadrants: 1 }),
  ];

  for (const viewportMesh of cases) {
    let calls = 0;
    const result = await convertToFreeform(state, {
      viewportMesh,
      prepareViewportMesh: async () => {
        calls += 1;
        return { grid: freshGrid, preparedParams: { scale: 1 } };
      },
      validateCandidate: false,
    });
    assert.equal(calls, 1);
    assert.equal(result.params.length, 80);
    assert.equal(result.params.mouthRadiusH, 50);
  }
});

test('convertToFreeform drops unsupported analytic geometry and validates the candidate', async () => {
  const state = sourceState({
    gcurveType: 1,
    gcurveWidth: 40,
    throatExtLength: 12,
    rot: 5,
  });
  const grid = syntheticGrid({
    H: [
      [0, 10],
      [120, 40],
    ],
    V: [
      [0, 10],
      [120, 30],
    ],
  });
  let validatedState;
  const result = await convertToFreeform(state, {
    viewportMesh: reusableViewportMesh(state, grid),
    validateCandidate: async (candidate) => {
      validatedState = candidate;
    },
  });

  assert.equal(validatedState.type, 'FREEFORM');
  assert.equal(result.params.gcurveType, 0);
  assert.equal(result.params.gcurveWidth, '0');
  assert.equal(result.params.throatExtLength, '0');
  assert.equal(result.params.rot, '0');
  assert.deepEqual(result.report.droppedParams, [
    'throatExtLength',
    'rot',
    'gcurveType',
    'gcurveWidth',
  ]);
  assert.match(formatConversionReport(result.report), /dropped .*gcurveType/);
});

test('convertToFreeform surfaces backend candidate validation failures', async () => {
  const state = sourceState({ morphTarget: 1, morphCorner: 99 });
  const grid = syntheticGrid({
    H: [
      [0, 10],
      [120, 40],
    ],
    V: [
      [0, 10],
      [120, 30],
    ],
  });
  await assert.rejects(
    convertToFreeform(state, {
      viewportMesh: reusableViewportMesh(state, grid),
      validateCandidate: async () => {
        throw new Error('maximum feasible cornerRadiusMm for this station is 23.7682 mm');
      },
    }),
    /maximum feasible cornerRadiusMm/
  );
});

test('prepareBackendViewportMesh passes the raw response grid through unchanged', async () => {
  const grid = syntheticGrid({
    H: [
      [0, 10],
      [20, 20],
    ],
    V: [
      [0, 10],
      [20, 18],
    ],
  });
  const payload = {
    formula: 'OSSE',
    mode: 'bare',
    params: { type: 'OSSE' },
    grid,
    enclosure: null,
    metadata: { marker: 'raw-grid' },
  };
  const mesh = await prepareBackendViewportMesh(sourceState(), {
    fetchImpl: async () => ({
      ok: true,
      json: async () => payload,
    }),
  });

  assert.equal(mesh.grid, grid);
  assert.deepEqual(mesh.metadata, { marker: 'raw-grid' });
  assert.ok(mesh.vertices.length > 0);
  assert.ok(mesh.indices.length > 0);
});
