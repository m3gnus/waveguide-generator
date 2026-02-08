import fs from 'fs';
import path from 'path';
import { MWGConfigParser } from '../src/config/index.js';
import { getDefaults } from '../src/config/defaults.js';
import {
  buildGeometryArtifacts,
  prepareGeometryParams,
  coerceConfigParams,
  applyAthImportDefaults,
  isMWGConfig
} from '../src/geometry/index.js';
import { exportFullGeo, exportMSH } from '../src/export/msh.js';

const root = process.argv[2] || '_references/testconfigs';
const outRoot = process.argv[3] || '_references/testconfigs/_generated';

const THRESHOLDS = {
  geoPointCoord: 0.005,
  geoPointMesh: 0.05,
  stlBBox: 0.5,
  stlCentroid: 0.5
};


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

function parseGeo(filePath) {
  const text = fs.readFileSync(filePath, 'utf8');
  const lines = text.split(/\r?\n/);
  const points = new Map();
  let lineCount = 0;
  let splineCount = 0;
  let curveLoopCount = 0;
  let surfaceCount = 0;
  const physicalSurfaceDefs = [];
  let saveTarget = null;

  for (const line of lines) {
    const pointMatch = line.match(/^Point\((\d+)\)=\{([^}]+)\};$/);
    if (pointMatch) {
      const id = Number(pointMatch[1]);
      const vals = pointMatch[2].split(',').map((v) => Number(v.trim()));
      points.set(id, {
        x: vals[0] || 0,
        y: vals[1] || 0,
        z: vals[2] || 0,
        mesh: vals[3] || 0
      });
      continue;
    }
    if (/^Line\(/.test(line)) lineCount += 1;
    if (/^Spline\(/.test(line)) splineCount += 1;
    if (/^Curve Loop\(/.test(line)) curveLoopCount += 1;
    if (/^(Plane Surface|Surface)\(/.test(line)) surfaceCount += 1;
    if (/^Physical Surface\(/.test(line)) physicalSurfaceDefs.push(line.trim());

    const saveMatch = line.match(/^Save\s+"([^"]+)";/);
    if (saveMatch) saveTarget = saveMatch[1];
  }

  return {
    points,
    lineCount,
    splineCount,
    curveLoopCount,
    surfaceCount,
    physicalSurfaceDefs,
    saveTarget
  };
}

function parseMsh(filePath) {
  const text = fs.readFileSync(filePath, 'utf8');
  const lines = text.split(/\r?\n/);

  const physicalNames = new Map();
  let nodeCount = null;
  let elementCount = null;
  const tagCounts = new Map();

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (line === '$PhysicalNames') {
      const count = Number(lines[i + 1] || 0);
      for (let j = 0; j < count; j += 1) {
        const entry = lines[i + 2 + j];
        const match = entry && entry.match(/^2\s+(\d+)\s+"([^"]+)"$/);
        if (match) {
          physicalNames.set(Number(match[1]), match[2]);
        }
      }
      continue;
    }
    if (line === '$Nodes') {
      nodeCount = Number(lines[i + 1] || 0);
      continue;
    }
    if (line === '$Elements') {
      elementCount = Number(lines[i + 1] || 0);
      const start = i + 2;
      for (let j = 0; j < elementCount; j += 1) {
        const elLine = lines[start + j];
        if (!elLine) continue;
        const parts = elLine.trim().split(/\s+/);
        if (parts.length < 6) continue;
        const type = Number(parts[1]);
        const numTags = Number(parts[2]);
        if (type !== 2 || numTags < 1) continue;
        const physical = Number(parts[3]);
        tagCounts.set(physical, (tagCounts.get(physical) || 0) + 1);
      }
      continue;
    }
  }

  return {
    physicalNames,
    nodeCount: Number.isFinite(nodeCount) ? nodeCount : 0,
    elementCount: Number.isFinite(elementCount) ? elementCount : 0,
    tagCounts
  };
}

function parseBinaryStl(filePath) {
  const buf = fs.readFileSync(filePath);
  if (buf.length < 84) {
    throw new Error('Invalid STL: too short');
  }
  const triCount = buf.readUInt32LE(80);

  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  let sumX = 0, sumY = 0, sumZ = 0;
  let pointCount = 0;

  let offset = 84;
  for (let t = 0; t < triCount; t += 1) {
    offset += 12; // normal
    for (let k = 0; k < 3; k += 1) {
      const x = buf.readFloatLE(offset); offset += 4;
      const y = buf.readFloatLE(offset); offset += 4;
      const z = buf.readFloatLE(offset); offset += 4;

      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (z < minZ) minZ = z;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      if (z > maxZ) maxZ = z;
      sumX += x;
      sumY += y;
      sumZ += z;
      pointCount += 1;
    }
    offset += 2; // attr
  }

  const centroid = pointCount > 0
    ? [sumX / pointCount, sumY / pointCount, sumZ / pointCount]
    : [0, 0, 0];

  return {
    triangleCount: triCount,
    bbox: {
      min: [minX, minY, minZ],
      max: [maxX, maxY, maxZ]
    },
    centroid
  };
}

function compareGeoSemantic(oursPath, refPath) {
  if (!fs.existsSync(refPath)) {
    return { status: 'incomplete-reference', reason: 'missing reference GEO' };
  }

  const ours = parseGeo(oursPath);
  const ref = parseGeo(refPath);

  if (ours.points.size !== ref.points.size) {
    return {
      status: 'fail',
      reason: `point count mismatch ${ours.points.size} != ${ref.points.size}`
    };
  }

  let maxCoordDelta = 0;
  let maxMeshDelta = 0;
  const ids = [...ref.points.keys()].sort((a, b) => a - b);
  for (const id of ids) {
    const a = ours.points.get(id);
    const b = ref.points.get(id);
    if (!a || !b) {
      return { status: 'fail', reason: `point id mismatch at ${id}` };
    }
    maxCoordDelta = Math.max(
      maxCoordDelta,
      Math.abs(a.x - b.x),
      Math.abs(a.y - b.y),
      Math.abs(a.z - b.z)
    );
    maxMeshDelta = Math.max(maxMeshDelta, Math.abs(a.mesh - b.mesh));
  }

  if (maxCoordDelta > THRESHOLDS.geoPointCoord) {
    return {
      status: 'fail',
      reason: `max point delta ${maxCoordDelta.toFixed(6)} > ${THRESHOLDS.geoPointCoord}`
    };
  }
  if (maxMeshDelta > THRESHOLDS.geoPointMesh) {
    return {
      status: 'fail',
      reason: `max mesh-size delta ${maxMeshDelta.toFixed(6)} > ${THRESHOLDS.geoPointMesh}`
    };
  }

  if (ours.lineCount !== ref.lineCount ||
      ours.splineCount !== ref.splineCount ||
      ours.curveLoopCount !== ref.curveLoopCount ||
      ours.surfaceCount !== ref.surfaceCount) {
    return {
      status: 'fail',
      reason: `curve/surface count mismatch lines:${ours.lineCount}/${ref.lineCount} splines:${ours.splineCount}/${ref.splineCount} loops:${ours.curveLoopCount}/${ref.curveLoopCount} surfaces:${ours.surfaceCount}/${ref.surfaceCount}`
    };
  }

  if (ours.physicalSurfaceDefs.length !== ref.physicalSurfaceDefs.length) {
    return {
      status: 'fail',
      reason: `physical surface definition count mismatch ${ours.physicalSurfaceDefs.length} != ${ref.physicalSurfaceDefs.length}`
    };
  }

  const refSave = ref.saveTarget ? path.basename(ref.saveTarget) : null;
  const oursSave = ours.saveTarget ? path.basename(ours.saveTarget) : null;
  if (refSave && oursSave && refSave !== oursSave) {
    return { status: 'fail', reason: `save target mismatch ${oursSave} != ${refSave}` };
  }

  return {
    status: 'ok',
    reason: `max point delta ${maxCoordDelta.toFixed(6)}, max mesh delta ${maxMeshDelta.toFixed(6)}`
  };
}

function compareMshSemantic(oursPath, refPath) {
  if (!fs.existsSync(refPath)) {
    return { status: 'incomplete-reference', reason: 'missing reference MSH' };
  }

  const ours = parseMsh(oursPath);
  const ref = parseMsh(refPath);

  if (ours.nodeCount !== ref.nodeCount) {
    return { status: 'fail', reason: `node count mismatch ${ours.nodeCount} != ${ref.nodeCount}` };
  }
  if (ours.elementCount !== ref.elementCount) {
    return { status: 'fail', reason: `element count mismatch ${ours.elementCount} != ${ref.elementCount}` };
  }

  if (ours.physicalNames.size !== ref.physicalNames.size) {
    return {
      status: 'fail',
      reason: `physical name count mismatch ${ours.physicalNames.size} != ${ref.physicalNames.size}`
    };
  }

  for (const [id, name] of ref.physicalNames.entries()) {
    if (ours.physicalNames.get(id) !== name) {
      return {
        status: 'fail',
        reason: `physical name mismatch id ${id}: ${ours.physicalNames.get(id)} != ${name}`
      };
    }
  }

  const tags = new Set([...ref.tagCounts.keys(), ...ours.tagCounts.keys()]);
  for (const tag of tags) {
    const a = ours.tagCounts.get(tag) || 0;
    const b = ref.tagCounts.get(tag) || 0;
    if (a !== b) {
      return { status: 'fail', reason: `tag ${tag} triangle count mismatch ${a} != ${b}` };
    }
  }

  return { status: 'ok', reason: 'physical names and counts match' };
}

function compareStlSemantic(oursPath, refPath) {
  if (!fs.existsSync(refPath)) {
    return { status: 'incomplete-reference', reason: 'missing reference STL' };
  }

  const ours = parseBinaryStl(oursPath);
  const ref = parseBinaryStl(refPath);

  if (ours.triangleCount !== ref.triangleCount) {
    return { status: 'fail', reason: `triangle count mismatch ${ours.triangleCount} != ${ref.triangleCount}` };
  }

  const bboxDelta = Math.max(
    ...ours.bbox.min.map((v, i) => Math.abs(v - ref.bbox.min[i])),
    ...ours.bbox.max.map((v, i) => Math.abs(v - ref.bbox.max[i]))
  );
  if (bboxDelta > THRESHOLDS.stlBBox) {
    return {
      status: 'fail',
      reason: `bbox delta ${bboxDelta.toFixed(6)} > ${THRESHOLDS.stlBBox}`
    };
  }

  const centroidDelta = Math.max(
    ...ours.centroid.map((v, i) => Math.abs(v - ref.centroid[i]))
  );
  if (centroidDelta > THRESHOLDS.stlCentroid) {
    return {
      status: 'fail',
      reason: `centroid delta ${centroidDelta.toFixed(6)} > ${THRESHOLDS.stlCentroid}`
    };
  }

  return {
    status: 'ok',
    reason: `bbox delta ${bboxDelta.toFixed(6)}, centroid delta ${centroidDelta.toFixed(6)}`
  };
}

function loadConfig(configPath) {
  const content = fs.readFileSync(configPath, 'utf8');
  const parsed = MWGConfigParser.parse(content);
  if (!parsed.type) {
    throw new Error(`No type detected for ${configPath}`);
  }
  const typedParams = coerceConfigParams(parsed.params);
  if (parsed.blocks && Object.keys(parsed.blocks).length > 0) {
    typedParams._blocks = parsed.blocks;
  }
  if (!isMWGConfig(content)) {
    applyAthImportDefaults(parsed, typedParams);
  }

  const defaults = getDefaults(parsed.type);
  const params = { ...defaults, ...typedParams };
  return { type: parsed.type, params };
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function findReferenceGeo(folderPath) {
  const bemGeoRoot = path.join(folderPath, 'bem_mesh.geo');
  const bemGeoAbecFs = path.join(folderPath, 'ABEC_FreeStanding', 'bem_mesh.geo');
  const bemGeoAbecIb = path.join(folderPath, 'ABEC_InfiniteBaffle', 'bem_mesh.geo');
  if (fs.existsSync(bemGeoRoot)) return { path: bemGeoRoot, spline: true };
  if (fs.existsSync(bemGeoAbecFs)) return { path: bemGeoAbecFs, spline: true };
  if (fs.existsSync(bemGeoAbecIb)) return { path: bemGeoAbecIb, spline: true };

  const meshGeo = path.join(folderPath, 'mesh.geo');
  if (fs.existsSync(meshGeo)) return { path: meshGeo, spline: false };

  return { path: null, spline: true };
}

function run() {
  ensureDir(outRoot);
  const entries = fs.readdirSync(root, { withFileTypes: true });
  const configFiles = entries.filter((e) => e.isFile() && e.name.toLowerCase().endsWith('.txt')).sort((a, b) => a.name.localeCompare(b.name));

  const results = [];
  let blockingFailures = 0;

  for (const file of configFiles) {
    const configPath = path.join(root, file.name);
    const baseName = path.basename(file.name, path.extname(file.name));
    const folderName = findFolderForConfig(root, baseName, entries);
    if (!folderName) {
      results.push({ name: baseName, error: 'No matching folder' });
      blockingFailures += 1;
      continue;
    }

    const folderPath = path.join(root, folderName);
    const referenceStl = findFirstFileWithExt(folderPath, '.stl');
    const referenceMsh = findFirstFileWithExt(folderPath, '.msh');
    const refGeoInfo = findReferenceGeo(folderPath);

    const outDir = path.join(outRoot, baseName);
    ensureDir(outDir);

    let type, params;
    try {
      ({ type, params } = loadConfig(configPath));
    } catch (err) {
      results.push({ name: baseName, error: err.message });
      blockingFailures += 1;
      continue;
    }

    const geoParams = prepareGeometryParams(params, {
      type,
      forceFullQuadrants: false,
      applyVerticalOffset: true
    });
    const geoArtifacts = buildGeometryArtifacts(geoParams, {
      includeEnclosure: false,
      includeRearShape: false
    });
    const geoMesh = geoArtifacts.mesh;

    const geoOut = path.join(outDir, 'mesh.geo');
    const geoContent = exportFullGeo(geoMesh.vertices, geoParams, {
      outputName: baseName,
      useSplines: refGeoInfo.spline,
      ringCount: geoMesh.ringCount,
      fullCircle: geoMesh.fullCircle
    });
    fs.writeFileSync(geoOut, geoContent);

    const stlOut = path.join(outDir, `${baseName}.stl`);
    writeBinaryStl(stlOut, geoMesh.vertices, geoMesh.indices);

    const mshParams = prepareGeometryParams(params, {
      type,
      forceFullQuadrants: false,
      applyVerticalOffset: true
    });
    const mshOut = path.join(outDir, `${baseName}.msh`);
    const mshArtifacts = buildGeometryArtifacts(mshParams, {
      includeEnclosure: Number(mshParams.encDepth || 0) > 0
    });
    const mshPayload = mshArtifacts.simulation;
    fs.writeFileSync(
      mshOut,
      exportMSH(mshPayload.vertices, mshPayload.indices, mshPayload.surfaceTags, {
        verticalOffset: mshPayload.metadata?.verticalOffset || 0
      })
    );

    const record = { name: baseName };

    record.geo = refGeoInfo.path
      ? compareGeoSemantic(geoOut, refGeoInfo.path)
      : { status: 'incomplete-reference', reason: 'missing reference GEO' };

    record.stl = referenceStl
      ? compareStlSemantic(stlOut, referenceStl)
      : { status: 'incomplete-reference', reason: 'missing reference STL' };

    record.msh = referenceMsh
      ? compareMshSemantic(mshOut, referenceMsh)
      : { status: 'incomplete-reference', reason: 'missing reference MSH' };

    for (const key of ['geo', 'stl', 'msh']) {
      if (record[key]?.status === 'fail') {
        blockingFailures += 1;
      }
    }

    results.push(record);
  }

  for (const result of results) {
    if (result.error) {
      console.log(`${result.name}: ERROR`);
      console.log(`  ${result.error}`);
      continue;
    }

    const render = (r) => {
      if (!r) return 'UNKNOWN';
      if (r.status === 'ok') return 'OK';
      if (r.status === 'incomplete-reference') return 'INCOMPLETE';
      return 'FAIL';
    };

    console.log(`${result.name}: GEO ${render(result.geo)}, STL ${render(result.stl)}, MSH ${render(result.msh)}`);

    if (result.geo && result.geo.status !== 'ok') {
      console.log(`  GEO: ${result.geo.reason}`);
    }
    if (result.stl && result.stl.status !== 'ok') {
      console.log(`  STL: ${result.stl.reason}`);
    }
    if (result.msh && result.msh.status !== 'ok') {
      console.log(`  MSH: ${result.msh.reason}`);
    }
  }

  if (blockingFailures > 0) {
    console.log(`\nBlocking parity failures: ${blockingFailures}`);
    process.exitCode = 1;
  } else {
    console.log('\nParity gates passed (excluding incomplete references).');
  }
}

run();
