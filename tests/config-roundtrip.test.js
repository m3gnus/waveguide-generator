import test from 'node:test';
import assert from 'node:assert/strict';

import { MWGConfigParser } from '../src/config/index.js';
import { getDefaults } from '../src/config/defaults.js';
import { generateMWGConfigContent } from '../src/export/mwgConfig.js';

test('ABEC.SimProfile=0 round-trips and unknown blocks are preserved', () => {
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
    'ABEC.SimProfile = 0',
    'Unknown.Block = {',
    'Foo = Bar',
    '}'
  ].join('\n');

  const parsed = MWGConfigParser.parse(source);
  assert.equal(parsed.type, 'OSSE');
  assert.equal(parsed.params.abecSimProfile, '0');
  assert.ok(parsed.blocks['Unknown.Block']);

  const params = {
    ...getDefaults('OSSE'),
    ...parsed.params,
    _blocks: parsed.blocks,
    type: 'OSSE'
  };
  const regenerated = generateMWGConfigContent(params);
  const reparsed = MWGConfigParser.parse(regenerated);

  assert.equal(String(reparsed.params.abecSimProfile), '0');
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
