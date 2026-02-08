#!/usr/bin/env node

import fs from 'fs';
import os from 'os';
import path from 'path';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';
import { MWGConfigParser } from '../src/config/index.js';
import { getDefaults } from '../src/config/defaults.js';
import { buildGmshGeo } from '../src/export/gmshGeoBuilder.js';
import {
  buildGeometryArtifacts,
  prepareGeometryParams,
  coerceConfigParams,
  applyAthImportDefaults,
  isMWGConfig
} from '../src/geometry/index.js';
import { generateMeshFromGeo } from '../src/solver/client.js';

const DEFAULT_ROOT = '_references/testconfigs';
const DEFAULT_OUT = '.tmp-ath-parity';
const DEFAULT_BACKEND_URL = 'http://localhost:8000';
const GMSH_CLI_TIMEOUT_MS = 180000;
const VERTEX_CLOUD_MAX = 0.35;

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function generateMshViaCli(geoText, { mshVersion = '2.2' } = {}) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ath-parity-gmsh-'));
  const geoPath = path.join(tempDir, 'input.geo');
  const mshPath = path.join(tempDir, 'output.msh');

  try {
    fs.writeFileSync(geoPath, geoText, 'utf8');
    const format = mshVersion === '4.1' ? 'msh4' : 'msh2';
    const args = [
      geoPath,
      '-2',
      '-format',
      format,
      '-save_all',
      '0',
      '-o',
      mshPath
    ];

    try {
      execFileSync('gmsh', args, { stdio: 'pipe', timeout: GMSH_CLI_TIMEOUT_MS });
    } catch (err) {
      const stderr = String(err?.stderr || '');
      if (!stderr.includes('env: python: No such file or directory')) {
        throw err;
      }
      const gmshPath = execFileSync('which', ['gmsh'], { encoding: 'utf8' }).trim();
      execFileSync('python3', [gmshPath, ...args], { stdio: 'pipe', timeout: GMSH_CLI_TIMEOUT_MS });
    }
    return fs.readFileSync(mshPath, 'utf8');
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

function generateMshViaPythonApi(geoText, { mshVersion = '2.2' } = {}) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ath-parity-gmsh-py-'));
  const geoPath = path.join(tempDir, 'input.geo');
  const mshPath = path.join(tempDir, 'output.msh');

  try {
    fs.writeFileSync(geoPath, geoText, 'utf8');
    const script = [
      'import gmsh, sys',
      'geo_path, msh_path, msh_version = sys.argv[1], sys.argv[2], sys.argv[3]',
      'gmsh.initialize()',
      'try:',
      "  gmsh.option.setNumber('General.Terminal', 0)",
      '  gmsh.clear()',
      '  gmsh.open(geo_path)',
      "  gmsh.option.setNumber('Mesh.MshFileVersion', float(msh_version))",
      "  gmsh.option.setNumber('Mesh.Binary', 0)",
      "  gmsh.option.setNumber('Mesh.SaveAll', 0)",
      '  gmsh.model.mesh.generate(2)',
      '  gmsh.write(msh_path)',
      'finally:',
      '  if gmsh.isInitialized():',
      '    gmsh.finalize()'
    ].join('\n');

    execFileSync(
      'python3',
      ['-c', script, geoPath, mshPath, mshVersion],
      { stdio: 'pipe', timeout: GMSH_CLI_TIMEOUT_MS }
    );
    return fs.readFileSync(mshPath, 'utf8');
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

async function generateMshWithGmsh(
  geoText,
  {
    mshVersion = '2.2',
    backendUrl = DEFAULT_BACKEND_URL,
    mode = 'auto'
  } = {}
) {
  const normalizedMode = String(mode || 'auto').trim().toLowerCase();
  const errors = [];

  if (normalizedMode !== 'cli') {
    try {
      const result = await generateMeshFromGeo(
        { geoText, mshVersion, binary: false },
        backendUrl
      );
      if (result?.generatedBy === 'gmsh' && typeof result?.msh === 'string') {
        return result.msh;
      }
      errors.push('backend returned an invalid Gmsh response');
    } catch (err) {
      errors.push(`backend (${backendUrl}): ${err.message}`);
    }
  }

  if (normalizedMode === 'auto' || normalizedMode === 'python') {
    try {
      return generateMshViaPythonApi(geoText, { mshVersion });
    } catch (err) {
      errors.push(`python: ${err.message}`);
    }
  }

  if (normalizedMode === 'auto' || normalizedMode === 'cli') {
    try {
      return generateMshViaCli(geoText, { mshVersion });
    } catch (err) {
      errors.push(`cli: ${err.message}`);
    }
  }

  throw new Error(
    `unable to generate .msh via Gmsh (${normalizedMode} mode). ${errors.join(' | ')}`
  );
}

function parseMsh(text) {
  const lines = text.split(/\r?\n/);
  const physicalNames = new Map();
  const nodes = [];
  const triangles = [];
  const tagCounts = new Map();

  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();

    if (line === '$PhysicalNames') {
      const count = Number(lines[i + 1] || 0);
      for (let j = 0; j < count; j += 1) {
        const entry = lines[i + 2 + j] || '';
        const match = entry.match(/^2\s+(\d+)\s+"([^"]+)"$/);
        if (match) {
          physicalNames.set(Number(match[1]), match[2]);
        }
      }
      i += count + 3;
      continue;
    }

    if (line === '$Nodes') {
      const count = Number(lines[i + 1] || 0);
      for (let j = 0; j < count; j += 1) {
        const entry = (lines[i + 2 + j] || '').trim().split(/\s+/);
        if (entry.length >= 4) {
          nodes.push([Number(entry[1]), Number(entry[2]), Number(entry[3])]);
        }
      }
      i += count + 3;
      continue;
    }

    if (line === '$Elements') {
      const count = Number(lines[i + 1] || 0);
      for (let j = 0; j < count; j += 1) {
        const entry = (lines[i + 2 + j] || '').trim().split(/\s+/).map(Number);
        if (entry.length < 6) continue;
        const type = entry[1];
        const tagCount = entry[2];
        if (type !== 2 || tagCount < 1) continue;
        const physicalTag = entry[3];
        const n1 = entry[3 + tagCount];
        const n2 = entry[4 + tagCount];
        const n3 = entry[5 + tagCount];
        triangles.push([n1, n2, n3, physicalTag]);
        tagCounts.set(physicalTag, (tagCounts.get(physicalTag) || 0) + 1);
      }
      i += count + 3;
      continue;
    }

    i += 1;
  }

  return {
    physicalNames,
    nodes,
    triangles,
    nodeCount: nodes.length,
    elementCount: triangles.length,
    tagCounts
  };
}

function nearestDistance(point, cloud) {
  let best = Infinity;
  for (let i = 0; i < cloud.length; i += 1) {
    const dx = point[0] - cloud[i][0];
    const dy = point[1] - cloud[i][1];
    const dz = point[2] - cloud[i][2];
    const d = Math.hypot(dx, dy, dz);
    if (d < best) best = d;
    if (best === 0) break;
  }
  return best;
}

function compareVertexCloud(oursNodes, refNodes) {
  if (oursNodes.length === 0 || refNodes.length === 0) {
    return { maxForward: Infinity, maxBackward: Infinity, rmsForward: Infinity, rmsBackward: Infinity };
  }

  let maxForward = 0;
  let maxBackward = 0;
  let sumForward = 0;
  let sumBackward = 0;

  for (const p of oursNodes) {
    const d = nearestDistance(p, refNodes);
    maxForward = Math.max(maxForward, d);
    sumForward += d * d;
  }

  for (const p of refNodes) {
    const d = nearestDistance(p, oursNodes);
    maxBackward = Math.max(maxBackward, d);
    sumBackward += d * d;
  }

  return {
    maxForward,
    maxBackward,
    rmsForward: Math.sqrt(sumForward / oursNodes.length),
    rmsBackward: Math.sqrt(sumBackward / refNodes.length)
  };
}

function loadPreparedParams(configPath) {
  const content = fs.readFileSync(configPath, 'utf8');
  const parsed = MWGConfigParser.parse(content);
  if (!parsed.type) throw new Error('Unable to detect model type');

  const typedParams = coerceConfigParams(parsed.params);
  if (parsed.blocks && Object.keys(parsed.blocks).length > 0) {
    typedParams._blocks = parsed.blocks;
  }
  if (!isMWGConfig(content)) {
    applyAthImportDefaults(parsed, typedParams);
  }

  const defaults = getDefaults(parsed.type);
  const merged = { ...defaults, ...typedParams };
  return prepareGeometryParams(merged, { type: parsed.type, applyVerticalOffset: true });
}

function findReferenceMsh(root, baseName) {
  const freeStanding = path.join(root, baseName, 'ABEC_FreeStanding', `${baseName}.msh`);
  const infiniteBaffle = path.join(root, baseName, 'ABEC_InfiniteBaffle', `${baseName}.msh`);
  if (fs.existsSync(freeStanding)) return freeStanding;
  if (fs.existsSync(infiniteBaffle)) return infiniteBaffle;
  return null;
}

function compareMsh(oursText, refText) {
  const ours = parseMsh(oursText);
  const ref = parseMsh(refText);
  const errors = [];

  if (ours.nodeCount !== ref.nodeCount) {
    errors.push(`node count mismatch ${ours.nodeCount} != ${ref.nodeCount}`);
  }
  if (ours.elementCount !== ref.elementCount) {
    errors.push(`triangle count mismatch ${ours.elementCount} != ${ref.elementCount}`);
  }

  if (ours.physicalNames.size !== ref.physicalNames.size) {
    errors.push(`physical name count mismatch ${ours.physicalNames.size} != ${ref.physicalNames.size}`);
  } else {
    for (const [id, name] of ref.physicalNames.entries()) {
      const oursName = ours.physicalNames.get(id);
      if (oursName !== name) {
        errors.push(`physical name mismatch tag ${id}: ${oursName} != ${name}`);
      }
    }
  }

  const tags = new Set([...ours.tagCounts.keys(), ...ref.tagCounts.keys()]);
  for (const tag of tags) {
    const oursCount = ours.tagCounts.get(tag) || 0;
    const refCount = ref.tagCounts.get(tag) || 0;
    if (oursCount !== refCount) {
      errors.push(`tag ${tag} triangle count mismatch ${oursCount} != ${refCount}`);
    }
  }

  const cloud = compareVertexCloud(ours.nodes, ref.nodes);
  if (cloud.maxForward > VERTEX_CLOUD_MAX || cloud.maxBackward > VERTEX_CLOUD_MAX) {
    errors.push(
      `vertex cloud max mismatch fwd=${cloud.maxForward.toFixed(4)} back=${cloud.maxBackward.toFixed(4)} > ${VERTEX_CLOUD_MAX}`
    );
  }

  return { errors, cloud, ours, ref };
}

async function runParity({ root = DEFAULT_ROOT, outRoot = DEFAULT_OUT } = {}) {
  if (!fs.existsSync(root)) {
    console.log(`[ath-parity] skipped: reference root not found: ${root}`);
    return 0;
  }

  const gmshBackendUrl = process.env.ATH_PARITY_BACKEND_URL || DEFAULT_BACKEND_URL;
  const gmshMode = process.env.ATH_PARITY_GMSH_MODE || 'auto';

  ensureDir(outRoot);

  const configs = fs.readdirSync(root)
    .filter((name) => name.toLowerCase().endsWith('.txt'))
    .sort((a, b) => a.localeCompare(b));

  if (configs.length === 0) {
    console.log(`[ath-parity] skipped: no config files under ${root}`);
    return 0;
  }

  let failures = 0;

  for (const configFile of configs) {
    const base = path.basename(configFile, '.txt');
    const configPath = path.join(root, configFile);
    const refMshPath = findReferenceMsh(root, base);

    if (!refMshPath) {
      console.log(`${base}: SKIP (missing ATH .msh reference)`);
      continue;
    }

    let prepared;
    try {
      prepared = loadPreparedParams(configPath);
    } catch (err) {
      failures += 1;
      console.log(`${base}: FAIL (config parse/prep error: ${err.message})`);
      continue;
    }

    const artifacts = buildGeometryArtifacts(prepared, {
      includeEnclosure: Number(prepared.encDepth || 0) > 0
    });
    const payload = artifacts.simulation;

    const outDir = path.join(outRoot, base);
    ensureDir(outDir);
    const generatedGeoPath = path.join(outDir, `${base}.geo`);
    const generatedPath = path.join(outDir, `${base}.msh`);

    const { geoText } = buildGmshGeo(prepared, artifacts.mesh, payload, { mshVersion: '2.2' });
    fs.writeFileSync(generatedGeoPath, geoText, 'utf8');

    let generatedMsh;
    try {
      generatedMsh = await generateMshWithGmsh(geoText, {
        mshVersion: '2.2',
        backendUrl: gmshBackendUrl,
        mode: gmshMode
      });
    } catch (err) {
      failures += 1;
      console.log(`${base}: FAIL (gmsh generation error: ${err.message})`);
      continue;
    }

    fs.writeFileSync(generatedPath, generatedMsh, 'utf8');

    const refMsh = fs.readFileSync(refMshPath, 'utf8');
    const result = compareMsh(generatedMsh, refMsh);

    if (result.errors.length > 0) {
      failures += 1;
      console.log(`${base}: FAIL`);
      for (const error of result.errors) {
        console.log(`  - ${error}`);
      }
      console.log(
        `  - cloud rms: fwd=${result.cloud.rmsForward.toFixed(4)} back=${result.cloud.rmsBackward.toFixed(4)}`
      );
    } else {
      console.log(
        `${base}: OK (cloud max fwd=${result.cloud.maxForward.toFixed(4)}, back=${result.cloud.maxBackward.toFixed(4)})`
      );
    }
  }

  if (failures > 0) {
    console.log(`\nATH parity failed for ${failures} config(s).`);
    return 1;
  }

  console.log('\nATH parity checks passed.');
  return 0;
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  const root = process.argv[2] || DEFAULT_ROOT;
  const outRoot = process.argv[3] || DEFAULT_OUT;
  runParity({ root, outRoot })
    .then((code) => {
      process.exitCode = code;
    })
    .catch((err) => {
      console.error(`[ath-parity] fatal: ${err.message}`);
      process.exitCode = 1;
    });
}

export { runParity };
