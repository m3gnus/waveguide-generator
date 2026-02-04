import fs from 'fs';
import path from 'path';
import { MWGConfigParser } from '../../src/config/parser.js';
import { PARAM_SCHEMA } from '../../src/config/schema.js';
import { parseExpression } from '../../src/geometry/expression.js';
import { buildHornMesh } from '../../src/geometry/meshBuilder.js';

const ATH_ZMAP_20 = [
    0.0,
    0.01319,
    0.03269,
    0.05965,
    0.094787,
    0.139633,
    0.195959,
    0.263047,
    0.340509,
    0.427298,
    0.518751,
    0.610911,
    0.695737,
    0.770223,
    0.833534,
    0.88547,
    0.925641,
    0.955904,
    0.977809,
    0.992192,
    1.0
];

const rawExpressionKeys = new Set([
    'zMapPoints',
    'subdomainSlices',
    'interfaceOffset',
    'interfaceDraw',
    'gcurveSf',
    'encFrontResolution',
    'encBackResolution',
    'outputSubDir',
    'outputDestDir',
    'sourceContours'
]);

const isNumericString = (value) => /^-?\d+(\.\d+)?$/.test(value);

const resampleZMap = (map, lengthSteps) => {
    const maxIndex = map.length - 1;
    if (maxIndex === lengthSteps) return map.slice();
    const out = new Array(lengthSteps + 1);
    for (let j = 0; j <= lengthSteps; j++) {
        const t = (j / lengthSteps) * maxIndex;
        const idx = Math.floor(t);
        const frac = t - idx;
        const v0 = map[idx];
        const v1 = map[Math.min(idx + 1, maxIndex)];
        out[j] = v0 + (v1 - v0) * frac;
    }
    return out;
};

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

const prepareParams = (content, {
    applyVerticalOffset = false,
    forceFullQuadrants = true,
    applyAthDefaults = true
} = {}) => {
    const parsed = MWGConfigParser.parse(content);
    const preparedParams = { ...parsed.params };

    applySchema(preparedParams, PARAM_SCHEMA[parsed.type] || {});
    ['GEOMETRY', 'MORPH', 'MESH', 'ROLLBACK', 'ENCLOSURE', 'SOURCE', 'ABEC', 'OUTPUT'].forEach((group) => {
        applySchema(preparedParams, PARAM_SCHEMA[group] || {});
    });

    preparedParams.type = parsed.type;

    if (applyAthDefaults) {
        const isOSSE = parsed.type === 'OSSE';
        if (preparedParams.quadrants === undefined || preparedParams.quadrants === null || preparedParams.quadrants === '') {
            preparedParams.quadrants = isOSSE ? '14' : '1';
        }
        if (isOSSE) {
            if (preparedParams.k === undefined) preparedParams.k = 1;
            if (preparedParams.h === undefined) preparedParams.h = 0;
            const hasMeshEnclosure = parsed.blocks && parsed.blocks['Mesh.Enclosure'];
            if (!hasMeshEnclosure && preparedParams.encDepth === undefined) {
                preparedParams.encDepth = 0;
            }
        }
    }

    const rawScale = preparedParams.scale ?? preparedParams.Scale ?? 1;
    const scaleNum = typeof rawScale === 'number' ? rawScale : Number(rawScale);
    const scale = Number.isFinite(scaleNum) ? scaleNum : 1;
    preparedParams.scale = scale;
    preparedParams.useAthZMap = scale !== 1;

    if (scale !== 1) {
        const lengthKeys = [
            'L',
            'r0',
            'throatExtLength',
            'slotLength',
            'circArcRadius',
            'morphCorner',
            'morphWidth',
            'morphHeight',
            'throatResolution',
            'mouthResolution',
            'verticalOffset',
            'encDepth',
            'encEdge',
            'encSpaceL',
            'encSpaceT',
            'encSpaceR',
            'encSpaceB',
            'wallThickness',
            'rearResolution',
            'interfaceOffset',
            'interfaceDraw',
            'encEdge'
        ];

        lengthKeys.forEach((key) => {
            const value = preparedParams[key];
            if (value === undefined || value === null || value === '') return;
            if (typeof value === 'function') {
                preparedParams[key] = (p) => scale * value(p);
            } else if (typeof value === 'number' && Number.isFinite(value)) {
                preparedParams[key] = value * scale;
            }
        });
    }

    if (!applyVerticalOffset) {
        preparedParams.verticalOffset = 0;
    }

    if (forceFullQuadrants) {
        preparedParams.quadrants = '1234';
    }

    return preparedParams;
};

const parseMeshGeoMouthExtents = (filePath) => {
    const text = fs.readFileSync(filePath, 'utf8');
    const pointRegex = /Point\(\d+\)=\{([^,]+),([^,]+),([^,]+),/g;
    let match;
    let maxZ = -Infinity;
    const points = [];
    while ((match = pointRegex.exec(text))) {
        const x = Number(match[1]);
        const y = Number(match[2]);
        const z = Number(match[3]);
        if (z > maxZ) maxZ = z;
        points.push({ x, y, z });
    }
    const mouthPoints = points.filter((p) => Math.abs(p.z - maxZ) < 1e-6);
    const maxX = Math.max(...mouthPoints.map((p) => Math.abs(p.x)));
    const maxY = Math.max(...mouthPoints.map((p) => Math.abs(p.y)));
    return { maxX, maxY, maxZ };
};

const parseStlBounds = (filePath) => {
    const buffer = fs.readFileSync(filePath);
    const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
    const triangleCount = view.getUint32(80, true);
    let offset = 84;
    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;

    for (let i = 0; i < triangleCount; i++) {
        offset += 12; // normal
        for (let v = 0; v < 3; v++) {
            const x = view.getFloat32(offset, true); offset += 4;
            const y = view.getFloat32(offset, true); offset += 4;
            const z = view.getFloat32(offset, true); offset += 4;
            minX = Math.min(minX, x); maxX = Math.max(maxX, x);
            minY = Math.min(minY, y); maxY = Math.max(maxY, y);
            minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z);
        }
        offset += 2; // attribute
    }

    return { minX, maxX, minY, maxY, minZ, maxZ };
};

const parseMshBounds = (filePath) => {
    const text = fs.readFileSync(filePath, 'utf8');
    const lines = text.split(/\r?\n/);
    let nodes = [];
    for (let i = 0; i < lines.length; i++) {
        if (lines[i].trim() === '$Nodes') {
            const count = parseInt(lines[i + 1].trim().split(/\s+/)[0]);
            let idx = i + 2;
            for (let n = 0; n < count; n++) {
                const parts = lines[idx + n].trim().split(/\s+/);
                if (parts.length >= 4) {
                    nodes.push([
                        parseFloat(parts[1]),
                        parseFloat(parts[2]),
                        parseFloat(parts[3])
                    ]);
                }
            }
            break;
        }
    }

    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;
    for (const node of nodes) {
        const x = node[0];
        const y = node[1];
        const z = node[2];
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
        minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z);
    }
    return { minX, maxX, minY, maxY, minZ, maxZ };
};

const rotateVerticesForAthSTL = (vertices) => {
    const out = new Array(vertices.length);
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1];
        const z = vertices[i + 2];
        out[i] = x;
        out[i + 1] = -z;
        out[i + 2] = y;
    }
    return out;
};

const computeMeshBounds = (vertices) => {
    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1];
        const z = vertices[i + 2];
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
        minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z);
    }
    return { minX, maxX, minY, maxY, minZ, maxZ };
};

describe('ATH Tritonia compatibility', () => {
    it('matches reference mesh extents and axial spacing', () => {
        const refRoot = path.join(process.cwd(), '_references', 'testconfigs');
        const scriptPath = path.join(refRoot, 'tritonia.txt');
        const meshGeoPath = path.join(refRoot, 'tritonia', 'mesh.geo');
        const stlPath = path.join(refRoot, 'tritonia', 'tritonia.stl');

        const content = fs.readFileSync(scriptPath, 'utf8');
        const params = prepareParams(content, { applyVerticalOffset: false, forceFullQuadrants: true });
        const { vertices } = buildHornMesh(params);

        const ringSize = params.angularSegments;
        const lastRingStart = params.lengthSegments * ringSize * 3;
        const ringPoints = [];
        for (let i = 0; i < ringSize; i++) {
            const idx = lastRingStart + i * 3;
            ringPoints.push([vertices[idx], vertices[idx + 1], vertices[idx + 2]]);
        }
        const maxX = Math.max(...ringPoints.map((p) => Math.abs(p[0])));
        const maxZ = Math.max(...ringPoints.map((p) => Math.abs(p[2])));

        const meshGeoExtents = parseMeshGeoMouthExtents(meshGeoPath);
        console.log('Mesh extents:', { maxX, maxZ, meshGeoExtents });
        expect(Math.abs(maxX - meshGeoExtents.maxX)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(maxZ - meshGeoExtents.maxY)).toBeLessThanOrEqual(1.0);

        const totalLength = (typeof params.L === 'function' ? params.L(0) : params.L)
            + (typeof params.throatExtLength === 'function' ? params.throatExtLength(0) : (params.throatExtLength || 0))
            + (typeof params.slotLength === 'function' ? params.slotLength(0) : (params.slotLength || 0));
        const expectedMap = resampleZMap(ATH_ZMAP_20, params.lengthSegments);

        for (let j = 0; j <= params.lengthSegments; j++) {
            const idx = j * ringSize * 3 + 1;
            const y = vertices[idx];
            const expected = expectedMap[j] * totalLength;
            expect(Math.abs(y - expected)).toBeLessThanOrEqual(0.1);
        }

        const refBounds = parseStlBounds(stlPath);
        const rotated = rotateVerticesForAthSTL(vertices);
        const ourBounds = computeMeshBounds(rotated);
        expect(Math.abs(ourBounds.minX - refBounds.minX)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.maxX - refBounds.maxX)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.minY - refBounds.minY)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.maxY - refBounds.maxY)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.minZ - refBounds.minZ)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.maxZ - refBounds.maxZ)).toBeLessThanOrEqual(1.0);
    });
});

describe('ATH Aolo compatibility', () => {
    it('matches reference STL bounds', () => {
        const refRoot = path.join(process.cwd(), '_references', 'testconfigs');
        const scriptPath = path.join(refRoot, 'aolo.txt');
        const stlPath = path.join(refRoot, 'aolo', '260112aolo1.stl');

        const content = fs.readFileSync(scriptPath, 'utf8');
        const params = prepareParams(content, { applyVerticalOffset: false, forceFullQuadrants: true });
        const { vertices } = buildHornMesh(params);

        const refBounds = parseStlBounds(stlPath);
        const rotated = rotateVerticesForAthSTL(vertices);
        const ourBounds = computeMeshBounds(rotated);
        expect(Math.abs(ourBounds.minX - refBounds.minX)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.maxX - refBounds.maxX)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.minY - refBounds.minY)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.maxY - refBounds.maxY)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.minZ - refBounds.minZ)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourBounds.maxZ - refBounds.maxZ)).toBeLessThanOrEqual(1.0);
    });

    it('matches reference MSH extents (symmetry mesh)', () => {
        const refRoot = path.join(process.cwd(), '_references', 'testconfigs');
        const scriptPath = path.join(refRoot, 'aolo.txt');
        const mshPath = path.join(refRoot, 'aolo', 'ABEC_FreeStanding', '260112aolo1.msh');

        const content = fs.readFileSync(scriptPath, 'utf8');
        const params = prepareParams(content, { applyVerticalOffset: true, forceFullQuadrants: false });
        const { vertices } = buildHornMesh(params);

        const refBounds = parseMshBounds(mshPath);
        const rotated = rotateVerticesForAthSTL(vertices);
        const ourBounds = computeMeshBounds(rotated);

        const refAbsX = Math.max(Math.abs(refBounds.minX), Math.abs(refBounds.maxX));
        const refAbsY = Math.max(Math.abs(refBounds.minY), Math.abs(refBounds.maxY));
        const refAbsZ = Math.max(Math.abs(refBounds.minZ), Math.abs(refBounds.maxZ));
        const ourAbsX = Math.max(Math.abs(ourBounds.minX), Math.abs(ourBounds.maxX));
        const ourAbsY = Math.max(Math.abs(ourBounds.minY), Math.abs(ourBounds.maxY));
        const ourAbsZ = Math.max(Math.abs(ourBounds.minZ), Math.abs(ourBounds.maxZ));

        expect(Math.abs(ourAbsX - refAbsX)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourAbsY - refAbsY)).toBeLessThanOrEqual(1.0);
        expect(Math.abs(ourAbsZ - refAbsZ)).toBeLessThanOrEqual(1.0);
    });
});
