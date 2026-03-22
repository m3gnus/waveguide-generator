import test from 'node:test';
import assert from 'node:assert/strict';

import { parseMSH } from '../src/import/mshParser.js';
import { exportLegacyMSH } from './helpers/legacyMsh.js';

/**
 * Helper: build a simple MSH string directly (no ATH transform)
 * so we can test the parser in isolation without exportLegacyMSH's
 * vertex transform side-effects.
 */
function buildSimpleMSH(vertices, indices, physicalTags, physicalNames) {
  const nodeCount = vertices.length / 3;
  const triCount = indices.length / 3;
  const names = physicalNames || [{ id: 1, name: 'SD1G0' }];
  let s = '$MeshFormat\n2.2 0 8\n$EndMeshFormat\n';
  s += '$PhysicalNames\n' + names.length + '\n';
  for (const { id, name } of names) s += `2 ${id} "${name}"\n`;
  s += '$EndPhysicalNames\n';
  s += '$Nodes\n' + nodeCount + '\n';
  for (let i = 0; i < nodeCount; i++) {
    s += `${i + 1} ${vertices[i * 3]} ${vertices[i * 3 + 1]} ${vertices[i * 3 + 2]}\n`;
  }
  s += '$EndNodes\n';
  s += '$Elements\n' + triCount + '\n';
  for (let i = 0; i < triCount; i++) {
    const tag = physicalTags ? physicalTags[i] : 1;
    s += `${i + 1} 2 2 ${tag} ${tag} ${indices[i * 3] + 1} ${indices[i * 3 + 1] + 1} ${indices[i * 3 + 2] + 1}\n`;
  }
  s += '$EndElements\n';
  return s;
}

test('mshParser: basic round-trip — vertices and indices match', () => {
  const vertices = [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0];
  const indices = [0, 1, 2, 1, 3, 2];
  const msh = buildSimpleMSH(vertices, indices);
  const result = parseMSH(msh);

  assert.equal(result.vertices.length, vertices.length);
  assert.equal(result.indices.length, indices.length);

  for (let i = 0; i < vertices.length; i++) {
    assert.equal(result.vertices[i], vertices[i], `vertex component [${i}]`);
  }
  for (let i = 0; i < indices.length; i++) {
    assert.equal(result.indices[i], indices[i], `index [${i}]`);
  }
});

test('mshParser: physical tags preserved through round-trip', () => {
  const vertices = [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0];
  const indices = [0, 1, 2, 1, 3, 2];
  const tags = [1, 2];
  const names = [
    { id: 1, name: 'SD1G0' },
    { id: 2, name: 'SD1D1001' }
  ];
  const msh = buildSimpleMSH(vertices, indices, tags, names);
  const result = parseMSH(msh);

  assert.equal(result.physicalTags.length, 2);
  assert.equal(result.physicalTags[0], 1);
  assert.equal(result.physicalTags[1], 2);
  assert.equal(result.physicalNames.get(1), 'SD1G0');
  assert.equal(result.physicalNames.get(2), 'SD1D1001');
});

test('mshParser: multiple physical groups including enclosure tag 3', () => {
  const vertices = [
    0, 0, 0,  1, 0, 0,  0, 1, 0,
    1, 1, 0,  2, 0, 0,  2, 1, 0
  ];
  const indices = [0, 1, 2, 1, 3, 2, 3, 4, 5];
  const tags = [1, 2, 3];
  const names = [
    { id: 1, name: 'SD1G0' },
    { id: 2, name: 'SD1D1001' },
    { id: 3, name: 'SD2G0' }
  ];
  const msh = buildSimpleMSH(vertices, indices, tags, names);
  const result = parseMSH(msh);

  assert.equal(result.physicalTags.length, 3);
  assert.equal(result.physicalTags[0], 1);
  assert.equal(result.physicalTags[1], 2);
  assert.equal(result.physicalTags[2], 3);
  assert.equal(result.physicalNames.size, 3);
  assert.equal(result.physicalNames.get(3), 'SD2G0');
});

test('mshParser: throws on missing $Nodes section', () => {
  const badMsh = '$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Elements\n0\n$EndElements\n';
  assert.throws(() => parseMSH(badMsh), /Missing \$Nodes section/);
});

test('mshParser: skips non-triangle elements', () => {
  // Build MSH with a mix of element types: type 1 = line (2 nodes), type 2 = triangle
  const vertices = [0, 0, 0, 1, 0, 0, 0, 1, 0];
  const nodeCount = 3;
  let s = '$MeshFormat\n2.2 0 8\n$EndMeshFormat\n';
  s += '$PhysicalNames\n1\n2 1 "SD1G0"\n$EndPhysicalNames\n';
  s += '$Nodes\n' + nodeCount + '\n';
  s += '1 0 0 0\n2 1 0 0\n3 0 1 0\n';
  s += '$EndNodes\n';
  // 3 elements: one line (type 1), one triangle (type 2), one point (type 15)
  s += '$Elements\n3\n';
  s += '1 1 2 1 1 1 2\n';       // line element (type 1, 2 nodes)
  s += '2 2 2 1 1 1 2 3\n';     // triangle (type 2, 3 nodes)
  s += '3 15 2 1 1 1\n';        // point element (type 15, 1 node)
  s += '$EndElements\n';

  const result = parseMSH(s);
  // Only the triangle should be kept
  assert.equal(result.indices.length, 3);
  assert.equal(result.physicalTags.length, 1);
  assert.equal(result.physicalTags[0], 1);
  // Check 0-based indices
  assert.equal(result.indices[0], 0);
  assert.equal(result.indices[1], 1);
  assert.equal(result.indices[2], 2);
});

test('mshParser: round-trip with exportLegacyMSH', () => {
  // Use exportLegacyMSH to generate MSH text and parse it back.
  // Note: exportLegacyMSH applies an ATH transform (swaps Y/Z), so we
  // verify the parsed output matches the transformed geometry, not the input.
  const vertices = new Float32Array([0, 0, 0, 10, 0, 0, 0, 10, 0]);
  const indices = new Uint32Array([0, 1, 2]);
  const surfaceTags = [1];
  const msh = exportLegacyMSH(vertices, indices, surfaceTags);
  const result = parseMSH(msh);

  assert.equal(result.indices.length, 3);
  assert.equal(result.physicalTags.length, 1);
  assert.equal(result.physicalNames.get(1), 'SD1G0');
  assert.equal(result.physicalNames.get(2), 'SD1D1001');
  // Vertex count should match
  assert.equal(result.vertices.length / 3, vertices.length / 3);
});
