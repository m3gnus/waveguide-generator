import test from 'node:test';
import assert from 'node:assert/strict';

import { MWGConfigParser } from '../src/config/index.js';
import { getDefaults } from '../src/config/defaults.js';
import { generateMWGConfigContent } from '../src/export/mwgConfig.js';
import { importMWGConfig } from '../src/modules/design/useCases.js';

test('legacy ABEC frequency keys round-trip to simulation frequency keys and unknown blocks are preserved', () => {
  const source = [
    'Coverage.Angle = 45',
    'Length = 120',
    'Term.n = 4',
    'Term.q = 1',
    'Term.s = 0.6',
    'Throat.Angle = 15.5',
    'Throat.Diameter = 25.4',
    'OS.k = 7',
    'ABEC.SimType = 2',
    'ABEC.f1 = 300',
    'ABEC.f2 = 12000',
    'ABEC.NumFrequencies = 55',
    'Unknown.Block = {',
    'Foo = Bar',
    '}'
  ].join('\n');

  const parsed = MWGConfigParser.parse(source);
  assert.equal(parsed.type, 'OSSE');
  assert.equal(parsed.params.freqStart, '300');
  assert.equal(parsed.params.freqEnd, '12000');
  assert.equal(parsed.params.numFreqs, '55');
  assert.ok(parsed.blocks['Unknown.Block']);

  const params = {
    ...getDefaults('OSSE'),
    ...parsed.params,
    _blocks: parsed.blocks,
    type: 'OSSE'
  };
  const regenerated = generateMWGConfigContent(params);
  const reparsed = MWGConfigParser.parse(regenerated);

  assert.equal(String(reparsed.params.freqStart), '300');
  assert.equal(String(reparsed.params.freqEnd), '12000');
  assert.equal(String(reparsed.params.numFreqs), '55');
  assert.ok(reparsed.blocks['Unknown.Block']);
});

test('legacy Mesh.RearShape config input is tolerated but not exported', () => {
  const source = [
    'Coverage.Angle = 45',
    'Length = 120',
    'Term.n = 4',
    'Term.q = 1',
    'Term.s = 0.6',
    'Throat.Angle = 15.5',
    'Throat.Diameter = 25.4',
    'OS.k = 7',
    'Mesh.WallThickness = 6',
    'Mesh.RearShape = 2'
  ].join('\n');

  const parsed = MWGConfigParser.parse(source);
  assert.equal(parsed.type, 'OSSE');

  const params = {
    ...getDefaults('OSSE'),
    ...parsed.params,
    type: 'OSSE'
  };
  const regenerated = generateMWGConfigContent(params);

  assert.equal(regenerated.includes('Mesh.RearShape'), false);
  assert.equal(regenerated.includes('Mesh.WallThickness = 6'), true);
});

test('config export rounds fractional corner sample counts to an integer', () => {
  const content = generateMWGConfigContent({
    ...getDefaults('OSSE'),
    morphTarget: 1,
    cornerSegments: 4.5,
  });

  assert.match(content, /^Mesh\.CornerSegments = 5$/m);
});

test('mixed OSSE flat and internal keys normalize independently', () => {
  const source = [
    'a = 51',
    'Coverage.Angle = 45',
    'Length = 120',
    'Throat.Angle = 15.5',
    'Throat.Diameter = 25.4',
    'Term.n = 4',
    'Term.q = 1',
    'Term.s = 0.6',
    'OS.k = 7',
    'OS.h = 0.2'
  ].join('\n');

  const parsed = MWGConfigParser.parse(source);

  assert.equal(parsed.type, 'OSSE');
  assert.equal(parsed.params.a, '51');
  assert.equal(parsed.params.L, '120');
  assert.equal(parsed.params.a0, '15.5');
  assert.equal(parsed.params.r0, '12.7');
  assert.equal(parsed.params.n, '4');
  assert.equal(parsed.params.q, '1');
  assert.equal(parsed.params.s, '0.6');
  assert.equal(parsed.params.k, '7');
  assert.equal(parsed.params.h, '0.2');
});

test('zero-angle Slot.Length imports as throat extension length', () => {
  const source = [
    'Coverage.Angle = 45',
    'Length = 120',
    'Throat.Angle = 15.5',
    'Throat.Diameter = 25.4',
    'Throat.Ext.Angle = 0',
    'Throat.Ext.Length = 4',
    'Slot.Length = 6',
    'Term.n = 4',
    'Term.q = 1',
    'Term.s = 0.6',
    'OS.k = 7'
  ].join('\n');

  const parsed = MWGConfigParser.parse(source);

  assert.equal(parsed.type, 'OSSE');
  assert.equal(parsed.params.throatExtAngle, '0');
  assert.equal(parsed.params.throatExtLength, '10');
  assert.equal(parsed.params.slotLength, '0');
});

test('FREEFORM .mwg export/import round-trips canonical params and shared design fields', () => {
  const source = {
    ...getDefaults('FREEFORM'),
    type: 'FREEFORM',
    scale: 1.5,
    length: 137.25,
    throatRadius: 13.1,
    throatAngle: 17.25,
    mouthRadiusH: 166.5,
    mouthAngleH: 71.25,
    interiorH: [
      { z: 36.25, r: 52.5, angleDeg: 19.5, strength: 1.35 },
      { z: 91.75, r: 119.25, angleDeg: null, strength: null },
    ],
    throatTangentScaleH: 1.15,
    mouthTangentScaleH: 0.85,
    mouthRadiusV: 112.75,
    mouthAngleV: 57.5,
    interiorV: [{ z: 62.125, r: 57.75, angleDeg: -6.5, strength: 0.9 }],
    throatTangentScaleV: 1.25,
    mouthTangentScaleV: 0.95,
    crossSections: [
      { t: 0, shape: 'circle' },
      { t: 0.4, shape: 'superellipse', exponent: 3.5 },
      { t: 1, shape: 'rounded_rectangle', cornerRadiusMm: 13.75 },
    ],
    overshootPolicy: 'allow',
    inflectionPolicy: 'reject',
    angularSegments: 128,
    lengthSegments: 72,
    cornerSegments: 7,
    throatSegments: 4,
    throatResolution: 4.25,
    mouthResolution: 11.5,
    rearResolution: 31.5,
    apertureResolutionScale: 1.75,
    maxTriangles: 88000,
    allowLargeMesh: 1,
    quadrants: 12,
    encDepth: 245.5,
    encEdge: 21.25,
    encEdgeType: 2,
    encSpaceL: 31,
    encSpaceT: 32,
    encSpaceR: 33,
    encSpaceB: 34,
    encFrontResolution: '21,22,23,24',
    encBackResolution: '35,36,37,38',
    sourceShape: 2,
    sourceRadius: 11.75,
    sourceCurv: -1,
    sourceVelocity: 2,
    sourceContours: 'driver-contour.txt',
    freqStart: 315,
    freqEnd: 14750,
    numFreqs: 63,
    simType: 1,
    solverMode: 'full_3d',
  };
  const content = generateMWGConfigContent(source);
  const imported = importMWGConfig(content, 'freeform-roundtrip.mwg');
  assert.equal(imported.success, true, imported.error);
  assert.equal(imported.type, 'FREEFORM');
  const restored = { ...getDefaults('FREEFORM'), ...imported.params };

  const freeformKeys = [
    'length',
    'throatRadius',
    'throatAngle',
    'mouthRadiusH',
    'mouthAngleH',
    'interiorH',
    'throatTangentScaleH',
    'mouthTangentScaleH',
    'mouthRadiusV',
    'mouthAngleV',
    'interiorV',
    'throatTangentScaleV',
    'mouthTangentScaleV',
    'crossSections',
    'overshootPolicy',
    'inflectionPolicy',
  ];
  for (const key of freeformKeys) assert.deepEqual(restored[key], source[key], key);

  const sharedKeys = [
    'scale',
    'angularSegments',
    'lengthSegments',
    'cornerSegments',
    'throatSegments',
    'throatResolution',
    'mouthResolution',
    'rearResolution',
    'apertureResolutionScale',
    'maxTriangles',
    'allowLargeMesh',
    'quadrants',
    'encDepth',
    'encEdge',
    'encEdgeType',
    'encSpaceL',
    'encSpaceT',
    'encSpaceR',
    'encSpaceB',
    'encFrontResolution',
    'encBackResolution',
    'sourceShape',
    'sourceRadius',
    'sourceCurv',
    'sourceVelocity',
    'sourceContours',
    'freqStart',
    'freqEnd',
    'numFreqs',
    'simType',
    'solverMode',
  ];
  for (const key of sharedKeys) assert.deepEqual(restored[key], source[key], key);
});

test('FREEFORM parser consumes its blocks so re-export emits every section once', () => {
  const source = {
    ...getDefaults('FREEFORM'),
    type: 'FREEFORM',
    interiorH: [[40, 55, 20, 1.2]],
    interiorV: [[60, 62]],
  };
  const first = generateMWGConfigContent(source);
  const imported = importMWGConfig(first, 'single-emission.mwg');
  assert.equal(imported.success, true, imported.error);
  assert.deepEqual(imported.params._blocks, undefined);
  const second = generateMWGConfigContent({
    ...getDefaults('FREEFORM'),
    ...imported.params,
    type: 'FREEFORM',
  });

  for (const section of [
    'Freeform.H = {',
    'Freeform.V = {',
    'Freeform.H.Points = {',
    'Freeform.V.Points = {',
    'Freeform.CrossSections = {',
  ]) {
    assert.equal(second.split(section).length - 1, 1, section);
  }
});
