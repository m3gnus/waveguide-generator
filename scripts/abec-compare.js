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
import {
  exportFullGeo,
  exportMSH,
  generateAbecProjectFile,
  generateAbecSolvingFile,
  generateAbecObservationFile,
  generateAbecCoordsFile,
  generateAbecStaticFile,
  generateBemppStarterScript
} from '../src/export/index.js';

const root = process.argv[2] || '_references/testconfigs';
const outRoot = process.argv[3] || '/tmp/ath-generated-abec';


function loadConfig(configPath) {
  const content = fs.readFileSync(configPath, 'utf8');
  const parsed = MWGConfigParser.parse(content);
  if (!parsed.type) throw new Error(`No type detected for ${configPath}`);

  const typedParams = coerceConfigParams(parsed.params);
  if (parsed.blocks && Object.keys(parsed.blocks).length > 0) {
    typedParams._blocks = parsed.blocks;
  }

  if (!isMWGConfig(content)) applyAthImportDefaults(parsed, typedParams);

  const defaults = getDefaults(parsed.type);
  const params = { ...defaults, ...typedParams };
  return { type: parsed.type, params };
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function findReferenceFolder(configFolder) {
  const freeStanding = path.join(configFolder, 'ABEC_FreeStanding');
  const infiniteBaffle = path.join(configFolder, 'ABEC_InfiniteBaffle');
  if (fs.existsSync(freeStanding)) return freeStanding;
  if (fs.existsSync(infiniteBaffle)) return infiniteBaffle;
  return null;
}

function listFilesRecursive(baseDir) {
  const files = [];
  const stack = [''];
  while (stack.length) {
    const rel = stack.pop();
    const abs = path.join(baseDir, rel);
    const entries = fs.readdirSync(abs, { withFileTypes: true });
    for (const entry of entries) {
      const nextRel = rel ? path.join(rel, entry.name) : entry.name;
      if (entry.isDirectory()) {
        stack.push(nextRel);
      } else if (entry.isFile()) {
        files.push(nextRel);
      }
    }
  }
  files.sort((a, b) => a.localeCompare(b));
  return files;
}

function normalizeText(text) {
  return text
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0)
    .join('\n');
}

function parseMshSummary(text) {
  const lines = text.split(/\r?\n/);
  const names = new Map();
  let nodeCount = 0;
  let elementCount = 0;
  const tagCounts = new Map();

  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i] === '$PhysicalNames') {
      const n = Number(lines[i + 1] || 0);
      for (let j = 0; j < n; j += 1) {
        const m = (lines[i + 2 + j] || '').match(/^2\s+(\d+)\s+"([^"]+)"$/);
        if (m) names.set(Number(m[1]), m[2]);
      }
    }
    if (lines[i] === '$Nodes') nodeCount = Number(lines[i + 1] || 0);
    if (lines[i] === '$Elements') {
      elementCount = Number(lines[i + 1] || 0);
      const start = i + 2;
      for (let j = 0; j < elementCount; j += 1) {
        const parts = (lines[start + j] || '').trim().split(/\s+/);
        if (parts.length < 6) continue;
        if (Number(parts[1]) !== 2) continue;
        const phys = Number(parts[3]);
        tagCounts.set(phys, (tagCounts.get(phys) || 0) + 1);
      }
    }
  }

  return { names, nodeCount, elementCount, tagCounts };
}

function compareFilesSemantic(relPath, oursAbs, refAbs) {
  const ext = path.extname(relPath).toLowerCase();
  const oursText = fs.readFileSync(oursAbs, ext === '.msh' ? 'utf8' : 'utf8');
  const refText = fs.readFileSync(refAbs, ext === '.msh' ? 'utf8' : 'utf8');

  if (ext === '.msh') {
    const ours = parseMshSummary(oursText);
    const ref = parseMshSummary(refText);

    if (ours.nodeCount !== ref.nodeCount) return `node count mismatch ${ours.nodeCount} != ${ref.nodeCount}`;
    if (ours.elementCount !== ref.elementCount) return `element count mismatch ${ours.elementCount} != ${ref.elementCount}`;
    if (ours.names.size !== ref.names.size) return `physical name count mismatch ${ours.names.size} != ${ref.names.size}`;

    for (const [id, name] of ref.names.entries()) {
      if (ours.names.get(id) !== name) {
        return `physical name mismatch id ${id}: ${ours.names.get(id)} != ${name}`;
      }
    }

    const tags = new Set([...ours.tagCounts.keys(), ...ref.tagCounts.keys()]);
    for (const tag of tags) {
      const a = ours.tagCounts.get(tag) || 0;
      const b = ref.tagCounts.get(tag) || 0;
      if (a !== b) return `tag ${tag} count mismatch ${a} != ${b}`;
    }

    return null;
  }

  const normalizedOurs = normalizeText(oursText);
  const normalizedRef = normalizeText(refText);
  if (normalizedOurs !== normalizedRef) {
    return 'normalized text mismatch';
  }

  return null;
}

function run() {
  ensureDir(outRoot);
  const entries = fs.readdirSync(root, { withFileTypes: true });
  const configFiles = entries.filter((e) => e.isFile() && e.name.toLowerCase().endsWith('.txt')).sort((a, b) => a.name.localeCompare(b.name));

  let failures = 0;

  for (const cfg of configFiles) {
    const baseName = path.basename(cfg.name, '.txt');
    const configFolder = entries.find((e) => e.isDirectory() && e.name.toLowerCase() === baseName.toLowerCase());
    if (!configFolder) {
      console.log(`${baseName}: FAIL (missing config folder)`);
      failures += 1;
      continue;
    }

    const refAbec = findReferenceFolder(path.join(root, configFolder.name));
    if (!refAbec) {
      console.log(`${baseName}: SKIP (missing ABEC reference folder)`);
      continue;
    }

    const outDir = path.join(outRoot, baseName);
    ensureDir(outDir);

    let type;
    let params;
    try {
      ({ type, params } = loadConfig(path.join(root, cfg.name)));
    } catch (err) {
      console.log(`${baseName}: FAIL (${err.message})`);
      failures += 1;
      continue;
    }

    const prepared = prepareGeometryParams(params, { type });
    const artifacts = buildGeometryArtifacts(prepared, {
      includeEnclosure: Number(prepared.encDepth || 0) > 0
    });
    const payload = artifacts.simulation;
    const hornGeometry = buildGeometryArtifacts(prepared, {
      includeEnclosure: false,
      includeRearShape: false
    }).mesh;

    const mshName = `${baseName}.msh`;
    const generated = {
      'Project.abec': generateAbecProjectFile({
        solvingFileName: 'solving.txt',
        observationFileName: 'observation.txt',
        meshFileName: mshName
      }),
      'solving.txt': generateAbecSolvingFile(prepared, {
        interfaceEnabled: Boolean(payload.metadata?.interfaceEnabled)
      }),
      'observation.txt': generateAbecObservationFile({
        polarBlocks: prepared._blocks,
        allowDefaultPolars: !(prepared._blocks && Number(prepared.abecSimType || 2) === 1)
      }),
      [mshName]: exportMSH(payload.vertices, payload.indices, payload.surfaceTags, {
        verticalOffset: payload.metadata?.verticalOffset || 0
      }),
      'bem_mesh.geo': exportFullGeo(hornGeometry.vertices, prepared, {
        outputName: baseName,
        useSplines: true,
        ringCount: hornGeometry.ringCount,
        fullCircle: hornGeometry.fullCircle
      }),
      'Results/coords.txt': generateAbecCoordsFile(hornGeometry.vertices, hornGeometry.ringCount),
      'Results/static.txt': generateAbecStaticFile(payload.vertices),
      [`${baseName}_bempp.py`]: generateBemppStarterScript({ meshFileName: mshName, sourceTag: 2 })
    };

    for (const [rel, content] of Object.entries(generated)) {
      const abs = path.join(outDir, rel);
      ensureDir(path.dirname(abs));
      fs.writeFileSync(abs, content);
    }

    const requiredFiles = listFilesRecursive(refAbec);
    const missing = [];
    const mismatches = [];

    for (const rel of requiredFiles) {
      const refAbs = path.join(refAbec, rel);
      const oursAbs = path.join(outDir, rel);
      if (!fs.existsSync(oursAbs)) {
        missing.push(rel);
        continue;
      }

      const mismatch = compareFilesSemantic(rel, oursAbs, refAbs);
      if (mismatch) {
        mismatches.push(`${rel}: ${mismatch}`);
      }
    }

    if (missing.length === 0 && mismatches.length === 0) {
      console.log(`${baseName}: OK`);
    } else {
      failures += 1;
      console.log(`${baseName}: FAIL`);
      missing.forEach((m) => console.log(`  missing: ${m}`));
      mismatches.forEach((m) => console.log(`  mismatch: ${m}`));
    }
  }

  if (failures > 0) {
    console.log(`\nABEC compare failures: ${failures}`);
    process.exitCode = 1;
  } else {
    console.log('\nABEC compare passed.');
  }
}

run();
