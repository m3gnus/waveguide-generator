import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';

function makeParams(type, overrides = {}) {
  return {
    ...getDefaults(type),
    type,
    angularSegments: 64,
    lengthSegments: 16,
    quadrants: '1234',
    ...overrides
  };
}

function buildViewportArtifacts(rawParams) {
  const prepared = prepareGeometryParams(rawParams, {
    type: rawParams.type,
    applyVerticalOffset: true
  });

  return buildGeometryArtifacts(prepared, {
    adaptivePhi: false
  });
}

function buildMesh(rawParams, options = {}) {
  const prepared = prepareGeometryParams(rawParams, {
    type: rawParams.type,
    applyVerticalOffset: true
  });

  return buildGeometryArtifacts(prepared, options).mesh;
}

function hornTriangles(mesh) {
  const horn = mesh.groups?.horn;
  assert.ok(horn, 'Expected horn group to be present');
  return horn.end - horn.start;
}

test('OSSE viewport-equivalent tessellation keeps horn topology consistent with and without enclosure', () => {
  const base = makeParams('OSSE', {
    L: '120',
    a: '45',
    a0: '15.5',
    r0: '12.7',
    s: '0.58',
    n: 4.158,
    q: 0.991,
    k: 7,
    h: 0,
    encDepth: 0,
    wallThickness: 0
  });

  const noEnclosure = buildViewportArtifacts(base).mesh;
  const withEnclosure = buildViewportArtifacts({ ...base, encDepth: 240 }).mesh;

  assert.equal(hornTriangles(noEnclosure), hornTriangles(withEnclosure));
});

test('R-OSSE viewport-equivalent tessellation keeps horn topology consistent with and without enclosure', () => {
  const base = makeParams('R-OSSE', {
    R: '140 * (abs(cos(p)/1.6)^3 + abs(sin(p)/1)^4)^(-1/4.5)',
    a: '25 * (abs(cos(p)/1.2)^4 + abs(sin(p)/1)^3)^(-1/2.5)',
    a0: 15.5,
    r0: 12.7,
    k: 2.0,
    m: 0.85,
    b: '0.2',
    r: 0.4,
    q: 3.4,
    tmax: 1.0,
    encDepth: 0,
    wallThickness: 0
  });

  const noEnclosure = buildViewportArtifacts(base).mesh;
  const withEnclosure = buildViewportArtifacts({ ...base, encDepth: 240 }).mesh;

  assert.equal(hornTriangles(noEnclosure), hornTriangles(withEnclosure));
});

test('enclosure-present mesh ignores adaptivePhi flag for horn tessellation', () => {
  const base = makeParams('OSSE', {
    L: '120',
    a: '45',
    a0: '15.5',
    r0: '12.7',
    s: '0.58',
    n: 4.158,
    q: 0.991,
    k: 7,
    h: 0,
    encDepth: 240,
    wallThickness: 0
  });

  const adaptiveOff = buildMesh(base, { adaptivePhi: false });
  const adaptiveOn = buildMesh(base, { adaptivePhi: true });

  assert.equal(hornTriangles(adaptiveOff), hornTriangles(adaptiveOn));
  assert.equal(adaptiveOff.indices.length, adaptiveOn.indices.length);
  assert.equal(adaptiveOff.vertices.length, adaptiveOn.vertices.length);
});

test('no-enclosure wall meshes ignore adaptivePhi flag', () => {
  const base = makeParams('OSSE', {
    L: '120',
    a: '45',
    a0: '15.5',
    r0: '12.7',
    s: '0.58',
    n: 4.158,
    q: 0.991,
    k: 7,
    h: 0,
    encDepth: 0,
    wallThickness: 8
  });

  const adaptiveOff = buildMesh(base, { includeEnclosure: false, adaptivePhi: false });
  const adaptiveOn = buildMesh(base, { includeEnclosure: false, adaptivePhi: true });

  assert.equal(hornTriangles(adaptiveOff), hornTriangles(adaptiveOn));
  assert.equal(adaptiveOff.indices.length, adaptiveOn.indices.length);
  assert.equal(adaptiveOff.vertices.length, adaptiveOn.vertices.length);
});

test('no-enclosure no-wall full-circle mesh differs when adaptivePhi is enabled (control)', () => {
  const base = makeParams('OSSE', {
    L: '120',
    a: '45',
    a0: '15.5',
    r0: '12.7',
    s: '0.58',
    n: 4.158,
    q: 0.991,
    k: 7,
    h: 0,
    encDepth: 0,
    wallThickness: 0
  });

  const adaptiveOff = buildMesh(base, { includeEnclosure: false, adaptivePhi: false });
  const adaptiveOn = buildMesh(base, { includeEnclosure: false, adaptivePhi: true });

  assert.notEqual(adaptiveOff.indices.length, adaptiveOn.indices.length);
  assert.notEqual(hornTriangles(adaptiveOff), hornTriangles(adaptiveOn));
});
