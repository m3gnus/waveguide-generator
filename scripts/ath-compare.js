import fs from 'fs';
import path from 'path';
import { MWGConfigParser } from '../src/config/index.js';
import { getDefaults } from '../src/config/defaults.js';
import { PARAM_SCHEMA } from '../src/config/schema.js';
import { parseExpression, buildHornMesh } from '../src/geometry/index.js';
import { exportHornToMSHWithBoundaries, exportFullGeo } from '../src/export/msh.js';

const root = process.argv[2] || '_references/testconfigs';
const outRoot = process.argv[3] || '_references/testconfigs/_generated';

const NUMERIC_PATTERN = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

function isNumericString(value) {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim();
  if (!trimmed) return false;
  return NUMERIC_PATTERN.test(trimmed);
}

function applyAthImportDefaults(parsed, typedParams) {
  if (!parsed || !parsed.type) return;

  const isOSSE = parsed.type === 'OSSE';
  typedParams.useAthZMap = true;
  if (typedParams.morphTarget === undefined) {
    typedParams.morphTarget = 0;
  }
  const hasQuadrants =
    typedParams.quadrants !== undefined &&
    typedParams.quadrants !== null &&
    typedParams.quadrants !== '';
  if (!hasQuadrants) {
    typedParams.quadrants = isOSSE ? '14' : '1';
  }

  const hasMeshEnclosure = parsed.blocks && parsed.blocks['Mesh.Enclosure'];
  if (!hasMeshEnclosure && typedParams.encDepth === undefined) {
    typedParams.encDepth = 0;
  }

  if (isOSSE) {
    if (typedParams.k === undefined) {
      typedParams.k = 1;
    }
    if (typedParams.h === undefined) {
      typedParams.h = 0;
    }
  }
}

function prepareParamsForMesh(params, type, { forceFullQuadrants = false, applyVerticalOffset = true } = {}) {
  const preparedParams = { ...params };

  const rawExpressionKeys = new Set([
    'zMapPoints',
    'subdomainSlices',
    'interfaceOffset',
    'interfaceDraw',
    'gcurveSf',
    'encFrontResolution',
    'encBackResolution',
    'sourceContours'
  ]);

  const applySchema = (schema) => {
    if (!schema) return;
    for (const [key, def] of Object.entries(schema)) {
      const val = preparedParams[key];
      if (val === undefined || val === null) continue;

      if (def.type === 'expression') {
        if (rawExpressionKeys.has(key)) continue;
        if (typeof val !== 'string') continue;
        const trimmed = val.trim();
        if (!trimmed) continue;
        if (isNumericString(trimmed)) {
          preparedParams[key] = Number(trimmed);
        } else {
          preparedParams[key] = parseExpression(trimmed);
        }
      } else if ((def.type === 'number' || def.type === 'range') && typeof val === 'string') {
        const trimmed = val.trim();
        if (!trimmed) continue;
        if (isNumericString(trimmed)) {
          preparedParams[key] = Number(trimmed);
        } else if (/[a-zA-Z]/.test(trimmed)) {
          preparedParams[key] = parseExpression(trimmed);
        }
      }
    }
  };

  applySchema(PARAM_SCHEMA[type] || {});
  ['GEOMETRY', 'MORPH', 'MESH', 'ROLLBACK', 'ENCLOSURE', 'SOURCE', 'ABEC'].forEach(
    (group) => {
      applySchema(PARAM_SCHEMA[group] || {});
    }
  );

  preparedParams.type = type;

  const rawScale = preparedParams.scale ?? preparedParams.Scale ?? 1;
  const scaleNum = typeof rawScale === 'number' ? rawScale : Number(rawScale);
  const scale = Number.isFinite(scaleNum) ? scaleNum : 1;
  preparedParams.scale = scale;
  const useAthZMap = preparedParams.useAthZMap ?? scale !== 1;
  preparedParams.useAthZMap = Boolean(useAthZMap);

  if (scale !== 1) {
    const lengthKeys = [
      'L',
      'R',
      'r0',
      'throatExtLength',
      'slotLength',
      'circArcRadius',
      'morphCorner',
      'morphWidth',
      'morphHeight',
      'gcurveWidth',
      'sourceRadius'
    ];

    lengthKeys.forEach((key) => {
      const value = preparedParams[key];
      if (value === undefined || value === null || value === '') return;
      if (typeof value === 'function') {
        preparedParams[key] = (p) => scale * value(p);
      } else if (typeof value === 'number' && Number.isFinite(value)) {
        preparedParams[key] = value * scale;
      } else if (typeof value === 'string' && isNumericString(value)) {
        preparedParams[key] = Number(value) * scale;
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
}

function findFolderForConfig(rootDir, baseName, dirEntries) {
  const lower = baseName.toLowerCase();
  for (const entry of dirEntries) {
    if (!entry.isDirectory()) continue;
    if (entry.name.toLowerCase() === lower) return entry.name;
  }
  return null;
}

function findFirstFileWithExt(dir, ext) {
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(full);
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith(ext)) {
        return full;
      }
    }
  }
  return null;
}

function writeBinaryStl(outPath, vertices, indices, { rotateX = Math.PI / 2 } = {}) {
  const triCount = indices.length / 3;
  const header = Buffer.alloc(80);
  header.write('Created by Gmsh', 0, 'ascii');
  const buffer = Buffer.alloc(84 + triCount * 50);
  header.copy(buffer, 0);
  buffer.writeUInt32LE(triCount, 80);

  const sinX = Math.sin(rotateX);
  const cosX = Math.cos(rotateX);

  const getVertex = (idx) => {
    const x = vertices[idx * 3];
    const y = vertices[idx * 3 + 1];
    const z = vertices[idx * 3 + 2];
    const ry = y * cosX - z * sinX;
    const rz = y * sinX + z * cosX;
    return [x, ry, rz];
  };

  let offset = 84;
  for (let i = 0; i < indices.length; i += 3) {
    const i0 = indices[i];
    const i1 = indices[i + 1];
    const i2 = indices[i + 2];
    const v0 = getVertex(i0);
    const v1 = getVertex(i1);
    const v2 = getVertex(i2);

    const ux = v1[0] - v0[0];
    const uy = v1[1] - v0[1];
    const uz = v1[2] - v0[2];
    const vx = v2[0] - v0[0];
    const vy = v2[1] - v0[1];
    const vz = v2[2] - v0[2];

    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
    nx /= len;
    ny /= len;
    nz /= len;

    buffer.writeFloatLE(nx, offset); offset += 4;
    buffer.writeFloatLE(ny, offset); offset += 4;
    buffer.writeFloatLE(nz, offset); offset += 4;

    buffer.writeFloatLE(v0[0], offset); offset += 4;
    buffer.writeFloatLE(v0[1], offset); offset += 4;
    buffer.writeFloatLE(v0[2], offset); offset += 4;

    buffer.writeFloatLE(v1[0], offset); offset += 4;
    buffer.writeFloatLE(v1[1], offset); offset += 4;
    buffer.writeFloatLE(v1[2], offset); offset += 4;

    buffer.writeFloatLE(v2[0], offset); offset += 4;
    buffer.writeFloatLE(v2[1], offset); offset += 4;
    buffer.writeFloatLE(v2[2], offset); offset += 4;

    buffer.writeUInt16LE(0, offset); offset += 2;
  }

  fs.writeFileSync(outPath, buffer);
}

function compareBuffers(aBuf, bBuf) {
  if (aBuf.length !== bBuf.length) {
    return { equal: false, reason: `size ${aBuf.length} != ${bBuf.length}` };
  }
  for (let i = 0; i < aBuf.length; i++) {
    if (aBuf[i] !== bBuf[i]) {
      return { equal: false, reason: `byte mismatch at ${i}: ${aBuf[i]} != ${bBuf[i]}` };
    }
  }
  return { equal: true };
}

function compareTextFiles(aPath, bPath) {
  const aText = fs.readFileSync(aPath, 'utf8');
  const bText = fs.readFileSync(bPath, 'utf8');
  if (aText === bText) return { equal: true };
  const aLines = aText.split(/\r?\n/);
  const bLines = bText.split(/\r?\n/);
  const max = Math.max(aLines.length, bLines.length);
  for (let i = 0; i < max; i++) {
    if (aLines[i] !== bLines[i]) {
      return { equal: false, reason: `line ${i + 1} differs`, a: aLines[i], b: bLines[i] };
    }
  }
  return { equal: false, reason: 'text mismatch' };
}

function loadConfig(configPath) {
  const content = fs.readFileSync(configPath, 'utf8');
  const parsed = MWGConfigParser.parse(content);
  if (!parsed.type) {
    throw new Error(`No type detected for ${configPath}`);
  }
  const typedParams = {};
  for (const [key, value] of Object.entries(parsed.params)) {
    if (value === undefined || value === null) continue;
    const stringValue = String(value).trim();
    if (isNumericString(stringValue)) {
      typedParams[key] = Number(stringValue);
    } else {
      typedParams[key] = stringValue;
    }
  }
  if (parsed.blocks && Object.keys(parsed.blocks).length > 0) {
    typedParams._blocks = parsed.blocks;
  }
  const isMWG = /;\s*MWG config/i.test(content);
  if (!isMWG) {
    applyAthImportDefaults(parsed, typedParams);
  }

  const defaults = getDefaults(parsed.type);
  const params = { ...defaults, ...typedParams };
  return { type: parsed.type, params };
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function run() {
  ensureDir(outRoot);
  const entries = fs.readdirSync(root, { withFileTypes: true });
  const configFiles = entries.filter((e) => e.isFile() && e.name.toLowerCase().endsWith('.txt'));

  const results = [];

  for (const file of configFiles) {
    const configPath = path.join(root, file.name);
    const baseName = path.basename(file.name, path.extname(file.name));
    const folderName = findFolderForConfig(root, baseName, entries);
    if (!folderName) {
      results.push({ name: baseName, error: 'No matching folder' });
      continue;
    }

    const folderPath = path.join(root, folderName);
    const referenceStl = findFirstFileWithExt(folderPath, '.stl');
    const referenceMsh = findFirstFileWithExt(folderPath, '.msh');
    const referenceGeo = path.join(folderPath, 'mesh.geo');

    const outDir = path.join(outRoot, baseName);
    ensureDir(outDir);

    let type, params;
    try {
      ({ type, params } = loadConfig(configPath));
    } catch (err) {
      results.push({ name: baseName, error: err.message });
      continue;
    }

    // GEO (horn only, full quadrants, no vertical offset - for Gmsh processing)
    const geoParams = prepareParamsForMesh(params, type, {
      forceFullQuadrants: true,
      applyVerticalOffset: false
    });
    const geoMesh = buildHornMesh(geoParams, {
      includeEnclosure: false,
      includeRearShape: false
    });
    const geoOut = path.join(outDir, 'mesh.geo');
    const geoContent = exportFullGeo(geoMesh.vertices, geoParams, { outputName: baseName });
    fs.writeFileSync(geoOut, geoContent);

    // STL (horn only, full quadrants, no vertical offset)
    const stlParams = geoParams;  // Same as geo params
    const stlMesh = geoMesh;  // Same mesh
    const stlOut = path.join(outDir, `${baseName}.stl`);
    writeBinaryStl(stlOut, stlMesh.vertices, stlMesh.indices);

    // MSH (with enclosure)
    const mshParams = prepareParamsForMesh(params, type, {
      forceFullQuadrants: false,
      applyVerticalOffset: true
    });
    const mshMesh = buildHornMesh(mshParams, {
      includeEnclosure: true,
      includeRearShape: true,
      collectGroups: true
    });
    const mshOut = path.join(outDir, `${baseName}.msh`);
    const mshContent = exportHornToMSHWithBoundaries(
      mshMesh.vertices,
      mshMesh.indices,
      mshParams,
      mshMesh.groups,
      { ringCount: mshMesh.ringCount }
    );
    fs.writeFileSync(mshOut, mshContent);

    const record = { name: baseName };

    // Compare GEO files
    if (fs.existsSync(referenceGeo)) {
      record.geo = compareTextFiles(geoOut, referenceGeo);
    } else {
      record.geo = { equal: false, reason: 'missing reference GEO' };
    }

    if (referenceStl) {
      const refBuf = fs.readFileSync(referenceStl);
      const outBuf = fs.readFileSync(stlOut);
      record.stl = compareBuffers(outBuf, refBuf);
    } else {
      record.stl = { equal: false, reason: 'missing reference STL' };
    }

    if (referenceMsh) {
      record.msh = compareTextFiles(mshOut, referenceMsh);
    } else {
      record.msh = { equal: false, reason: 'missing reference MSH' };
    }

    results.push(record);
  }

  for (const result of results) {
    const geoStatus = result.geo?.equal ? 'GEO OK' : `GEO FAIL`;
    const stlStatus = result.stl?.equal ? 'STL OK' : `STL FAIL`;
    const mshStatus = result.msh?.equal ? 'MSH OK' : `MSH FAIL`;
    console.log(`${result.name}: ${geoStatus}, ${stlStatus}, ${mshStatus}`);
    if (result.geo && !result.geo.equal) {
      console.log(`  GEO diff: ${result.geo.reason}`);
      if (result.geo.a !== undefined || result.geo.b !== undefined) {
        console.log(`  GEO ours: ${result.geo.a}`);
        console.log(`  GEO ref : ${result.geo.b}`);
      }
    }
    if (result.stl && !result.stl.equal) {
      console.log(`  STL diff: ${result.stl.reason}`);
    }
    if (result.msh && !result.msh.equal) {
      console.log(`  MSH diff: ${result.msh.reason}`);
      if (result.msh.a !== undefined || result.msh.b !== undefined) {
        console.log(`  MSH ours: ${result.msh.a}`);
        console.log(`  MSH ref : ${result.msh.b}`);
      }
    }
    if (result.error) {
      console.log(`  ERROR: ${result.error}`);
    }
  }
}

run();
