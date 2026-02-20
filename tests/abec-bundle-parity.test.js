import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';

import { MWGConfigParser } from '../src/config/index.js';
import { getDefaults } from '../src/config/defaults.js';
import {
    buildGeometryArtifacts,
    prepareGeometryParams,
    coerceConfigParams,
    applyAthImportDefaults,
    isMWGConfig
} from '../src/geometry/index.js';
import {
    generateAbecProjectFile,
    generateAbecSolvingFile,
    generateAbecObservationFile
} from '../src/export/abecProject.js';
import { validateAbecBundle } from '../src/export/abecBundleValidator.js';

const WORKSPACE_ROOT = process.cwd();
const REFERENCE_CONFIG_PATH = path.join(WORKSPACE_ROOT, '_references', 'testconfigs', '260112aolo1', 'config.txt');
const REFERENCE_BUNDLE_PATH = path.join(
    WORKSPACE_ROOT,
    '_references',
    'testconfigs',
    '260112aolo1',
    'ABEC_FreeStanding'
);
const REFERENCE_MSH_PATH = path.join(REFERENCE_BUNDLE_PATH, '260112aolo1.msh');
const GOLDEN_ROOT = path.join(WORKSPACE_ROOT, 'tests', 'fixtures', 'abec');

function readText(filePath) {
    return fs.readFileSync(filePath, 'utf8').replace(/\r\n/g, '\n').trimEnd();
}

function loadPreparedParams(configPath, overrides = {}) {
    const content = fs.readFileSync(configPath, 'utf8');
    const parsed = MWGConfigParser.parse(content);
    const typedParams = coerceConfigParams(parsed.params);
    if (parsed.blocks && Object.keys(parsed.blocks).length > 0) {
        typedParams._blocks = parsed.blocks;
    }
    if (!isMWGConfig(content)) {
        applyAthImportDefaults(parsed, typedParams);
    }
    const merged = { ...getDefaults(parsed.type), ...typedParams, ...overrides };
    return prepareGeometryParams(merged, { type: parsed.type, applyVerticalOffset: true });
}

function getAxialMax(vertices) {
    let maxY = -Infinity;
    for (let i = 1; i < vertices.length; i += 3) {
        if (vertices[i] > maxY) maxY = vertices[i];
    }
    return Number.isFinite(maxY) ? maxY : 0;
}

function loadBundleEntriesFromDir(rootDir) {
    const entries = {};
    const walk = (dirPath, relPrefix = '') => {
        fs.readdirSync(dirPath, { withFileTypes: true }).forEach((entry) => {
            const absPath = path.join(dirPath, entry.name);
            const relPath = relPrefix ? `${relPrefix}/${entry.name}` : entry.name;
            if (entry.isDirectory()) {
                walk(absPath, relPath);
                return;
            }
            entries[relPath] = fs.readFileSync(absPath, 'utf8');
        });
    };
    walk(rootDir);
    return entries;
}

function buildGeneratedBundleEntries(simType) {
    const prepared = loadPreparedParams(REFERENCE_CONFIG_PATH, { abecSimType: simType });
    const artifacts = buildGeometryArtifacts(prepared, {
        includeEnclosure: Number(prepared.encDepth || 0) > 0
    });
    const payload = artifacts.simulation;
    const meshFileName = '260112aolo1.msh';

    return {
        'Project.abec': generateAbecProjectFile({
            solvingFileName: 'solving.txt',
            observationFileName: 'observation.txt',
            meshFileName
        }),
        'solving.txt': generateAbecSolvingFile(prepared, {
            interfaceEnabled: Boolean(payload.metadata?.interfaceEnabled),
            infiniteBaffleOffset: getAxialMax(artifacts.mesh.vertices)
        }),
        'observation.txt': generateAbecObservationFile({
            angleRange: '0,180,37',
            distance: 2,
            normAngle: 5,
            inclination: 0,
            polarBlocks: prepared._blocks,
            allowDefaultPolars: !(prepared._blocks && Number(prepared.abecSimType || 2) === 1)
        }),
        '260112aolo1.msh': fs.readFileSync(REFERENCE_MSH_PATH, 'utf8')
    };
}

function assertGoldenMatch(entries, goldenFolder) {
    ['Project.abec', 'solving.txt', 'observation.txt'].forEach((name) => {
        const expected = readText(path.join(goldenFolder, name));
        const actual = String(entries[name] || '').replace(/\r\n/g, '\n').trimEnd();
        assert.equal(actual, expected, `${name} does not match golden fixture`);
    });
}

test('ATH ABEC_FreeStanding reference bundle satisfies parity contract', (t) => {
    if (!fs.existsSync(REFERENCE_BUNDLE_PATH)) {
        t.skip('reference ABEC_FreeStanding bundle not available');
        return;
    }

    const entries = loadBundleEntriesFromDir(REFERENCE_BUNDLE_PATH);
    const result = validateAbecBundle(entries, {
        mode: 'ABEC_FreeStanding',
        requireBemMeshGeo: true
    });
    assert.equal(result.ok, true, result.errors.join('\n'));
});

test('generated ABEC_FreeStanding bundle matches golden files and parity contract', (t) => {
    if (!fs.existsSync(REFERENCE_CONFIG_PATH) || !fs.existsSync(REFERENCE_MSH_PATH)) {
        t.skip('reference config/mesh is not available');
        return;
    }

    const entries = buildGeneratedBundleEntries(2);
    assertGoldenMatch(entries, path.join(GOLDEN_ROOT, 'ABEC_FreeStanding'));

    const result = validateAbecBundle(entries, {
        mode: 'ABEC_FreeStanding',
        requireBemMeshGeo: false
    });
    assert.equal(result.ok, true, result.errors.join('\n'));
});

test('generated ABEC_InfiniteBaffle bundle matches golden files and parity contract', (t) => {
    if (!fs.existsSync(REFERENCE_CONFIG_PATH) || !fs.existsSync(REFERENCE_MSH_PATH)) {
        t.skip('reference config/mesh is not available');
        return;
    }

    const entries = buildGeneratedBundleEntries(1);
    assertGoldenMatch(entries, path.join(GOLDEN_ROOT, 'ABEC_InfiniteBaffle'));

    const result = validateAbecBundle(entries, {
        mode: 'ABEC_InfiniteBaffle',
        requireBemMeshGeo: false
    });
    assert.equal(result.ok, true, result.errors.join('\n'));
});

