import fs from 'fs';
import { MWGConfigParser } from '../src/config/parser.js';
import { PARAM_SCHEMA } from '../src/config/schema.js';
import { parseExpression } from '../src/geometry/expression.js';
import { buildHornMesh } from '../src/geometry/meshBuilder.js';

const rawExpressionKeys = new Set([
    'zMapPoints', 'subdomainSlices', 'interfaceOffset', 'interfaceDraw',
    'gcurveSf', 'encFrontResolution', 'encBackResolution', 'outputSubDir',
    'outputDestDir', 'sourceContours'
]);

const isNumericString = (value) => /^-?\d+(\.\d+)?$/.test(value);

const applySchema = (params, schema) => {
    if (!schema) return;
    for (const [key, def] of Object.entries(schema)) {
        const val = params[key];
        if (val === undefined || val === null) continue;
        if (def.type === 'expression') {
            if (rawExpressionKeys.has(key)) continue;
            if (typeof val !== 'string') continue;
            const trimmed = val.trim();
            if (!trimmed) continue;
            if (isNumericString(trimmed)) {
                params[key] = Number(trimmed);
            } else {
                params[key] = parseExpression(trimmed);
            }
        } else if ((def.type === 'number' || def.type === 'range') && typeof val === 'string') {
            const trimmed = val.trim();
            if (!trimmed) continue;
            if (isNumericString(trimmed)) {
                params[key] = Number(trimmed);
            }
        }
    }
};

const prepareParams = (content) => {
    const parsed = MWGConfigParser.parse(content);
    const preparedParams = { ...parsed.params };
    applySchema(preparedParams, PARAM_SCHEMA[parsed.type] || {});
    ['GEOMETRY', 'MORPH', 'MESH', 'ROLLBACK', 'ENCLOSURE', 'SOURCE', 'ABEC', 'OUTPUT'].forEach((group) => {
        applySchema(preparedParams, PARAM_SCHEMA[group] || {});
    });
    preparedParams.type = parsed.type;
    preparedParams.quadrants = '1234';
    preparedParams.verticalOffset = 0;
    return preparedParams;
};

// Use a real horn config to test
const testConfig = `
OSSE
GEOMETRY
L=200
r0=30
a=1.4
k=1
h=0
MORPH
TargetWidth=150
TargetHeight=120
Rate=0.8
MESH
LengthSteps=20
AngularSteps=24
Enclosure
ENCLOSURE
Depth=100
Edge=5
SpaceL=25
SpaceT=25
SpaceR=25
SpaceB=25
`;

const params = prepareParams(testConfig);
params.encDepth = 100;
params.encEdge = 5;
const result = buildHornMesh(params, { collectGroups: true });
const { vertices, indices } = result;
const groupInfo = result.groups;

console.log('\n=== Enclosure Normal Analysis ===\n');
console.log(`Total vertices: ${vertices.length / 3}`);
console.log(`Total triangles: ${indices.length / 3}`);

if (!groupInfo || !groupInfo.enclosure) {
    console.log('\nNo enclosure group info found!');
    process.exit(1);
}

const encStart = groupInfo.enclosure.start;
const encEnd = groupInfo.enclosure.end;

console.log(`\nEnclosure triangles: ${encStart} to ${encEnd} (${encEnd - encStart} triangles)`);
console.log(`\nExpected horn vertices: ${(params.lengthSegments + 1) * params.angularSegments}`);
console.log(`Actual vertex count: ${vertices.length / 3}`);
console.log(`First few indices: [${indices.slice(0, 15).join(', ')}...]`);

// Sample a few triangles from different sections
const sampleSections = [
    { name: 'Front roundover', idx: encStart },
    { name: 'Side walls', idx: encStart + Math.floor((encEnd - encStart) * 0.5) },
    { name: 'Back roundover', idx: encStart + Math.floor((encEnd - encStart) * 0.75) },
    { name: 'Back cap', idx: Math.max(encStart, encEnd - 2) }
];

console.log('\n--- Sample Triangles ---');
for (const section of sampleSections) {
    const triIdx = section.idx * 3;
    const i0 = indices[triIdx];
    const i1 = indices[triIdx + 1];
    const i2 = indices[triIdx + 2];

    const v0x = vertices[i0 * 3], v0y = vertices[i0 * 3 + 1], v0z = vertices[i0 * 3 + 2];
    const v1x = vertices[i1 * 3], v1y = vertices[i1 * 3 + 1], v1z = vertices[i1 * 3 + 2];
    const v2x = vertices[i2 * 3], v2y = vertices[i2 * 3 + 1], v2z = vertices[i2 * 3 + 2];

    // Edge vectors
    const e1x = v1x - v0x, e1y = v1y - v0y, e1z = v1z - v0z;
    const e2x = v2x - v0x, e2y = v2y - v0y, e2z = v2z - v0z;

    // Cross product (normal, using right-hand rule)
    const nx = e1y * e2z - e1z * e2y;
    const ny = e1z * e2x - e1x * e2z;
    const nz = e1x * e2y - e1y * e2x;

    const len = Math.sqrt(nx * nx + ny * ny + nz * nz);

    console.log(`\n${section.name} (triangle ${section.idx}):`);
    console.log(`  Indices: [${i0}, ${i1}, ${i2}]`);
    console.log(`  V0: (${v0x.toFixed(2)}, ${v0y.toFixed(2)}, ${v0z.toFixed(2)})`);
    console.log(`  V1: (${v1x.toFixed(2)}, ${v1y.toFixed(2)}, ${v1z.toFixed(2)})`);
    console.log(`  V2: (${v2x.toFixed(2)}, ${v2y.toFixed(2)}, ${v2z.toFixed(2)})`);
    console.log(`  Normal: (${(nx / len).toFixed(3)}, ${(ny / len).toFixed(3)}, ${(nz / len).toFixed(3)})`);
    console.log(`  Length: ${len.toFixed(3)} ${len < 0.001 ? '⚠️ DEGENERATE' : '✓'}`);

    // Compute centroid
    const cx = (v0x + v1x + v2x) / 3;
    const cy = (v0y + v1y + v2y) / 3;
    const cz = (v0z + v1z + v2z) / 3;

    // For enclosure that's a closed box, we expect the normal to point away from the centroid of the box
    // A simple heuristic: if it's a front face (larger Y), normal Y component should be positive
    // If it's a back face (smaller Y), normal Y component should be negative
    // For side faces, the radial component should point outward

    console.log(`  Centroid: (${cx.toFixed(2)}, ${cy.toFixed(2)}, ${cz.toFixed(2)})`);
}

console.log('\n✅ Normal directions sampled - review output above');
console.log('For enclosure geometry, check that:');
console.log('  - Front faces (high Y) should have positive NY');
console.log('  - Back faces (low Y) should have negative NY');
console.log('  - Radial faces should have normals pointing outward from center');
