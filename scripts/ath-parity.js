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
const DEFAULT_GMSH_TIMEOUT_MS = 45000;
const DEFAULT_PROBE_TIMEOUT_MS = 4000;
const DEFAULT_BACKEND_PROBE_TIMEOUT_MS = 2500;
const DEFAULT_SMOKE_TIMEOUT_MS = 7000;
const VERTEX_CLOUD_MAX = 0.35;
const SMOKE_GEO_TEXT = [
  'Point(1) = {0, 0, 0, 1};',
  'Point(2) = {1, 0, 0, 1};',
  'Point(3) = {0, 1, 0, 1};',
  'Line(1) = {1, 2};',
  'Line(2) = {2, 3};',
  'Line(3) = {3, 1};',
  'Curve Loop(1) = {1, 2, 3};',
  'Plane Surface(1) = {1};',
  'Physical Surface("SD1G0", 1) = {1};',
  'Physical Surface("SD1D1001", 2) = {1};'
].join('\n');

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function envNumber(name, fallback) {
  const raw = process.env[name];
  if (raw == null || raw === '') return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) return fallback;
  return Math.floor(value);
}

function formatError(err) {
  const code = err?.code ? `${err.code}: ` : '';
  const stderr = String(err?.stderr || '').trim().split(/\r?\n/).slice(-2).join(' | ');
  const base = err?.message || String(err);
  return stderr ? `${code}${base} [${stderr}]` : `${code}${base}`;
}

function classifyInfrastructureFailure(method, err, options = {}) {
  const smokePassed = Boolean(options.smokePassed);
  const message = formatError(err).toLowerCase();
  if (
    err?.code === 'ETIMEDOUT'
    || err?.name === 'AbortError'
    || message.includes('timed out')
    || message.includes('did not respond within')
  ) {
    if (smokePassed) {
      return { infrastructure: false, reason: `${method} timed out on ATH reference` };
    }
    return { infrastructure: true, reason: `${method} timed out` };
  }
  if (
    err?.code === 'ENOENT'
    || message.includes('cannot reach gmsh backend')
    || message.includes('fetch failed')
    || message.includes('service unavailable')
    || message.includes('executable not found')
    || message.includes('no module named gmsh')
  ) {
    return { infrastructure: true, reason: `${method} unavailable` };
  }
  return { infrastructure: false, reason: `${method} execution failed` };
}

class AthParityGenerationError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = 'AthParityGenerationError';
    this.infrastructure = Boolean(options.infrastructure);
    this.attempts = options.attempts || [];
    this.capabilities = options.capabilities || null;
  }
}

function normalizeGmshMode(mode) {
  const normalized = String(mode || 'auto').trim().toLowerCase();
  if (normalized === 'backend' || normalized === 'python' || normalized === 'cli' || normalized === 'auto') {
    return normalized;
  }
  return 'auto';
}

function commandAvailable(command, timeoutMs = DEFAULT_PROBE_TIMEOUT_MS) {
  try {
    execFileSync(command, ['--version'], { stdio: 'pipe', timeout: timeoutMs });
    return true;
  } catch {
    return false;
  }
}

function generateMshViaCli(geoText, { mshVersion = '2.2', timeoutMs = DEFAULT_GMSH_TIMEOUT_MS } = {}) {
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
      execFileSync('gmsh', args, { stdio: 'pipe', timeout: timeoutMs });
    } catch (err) {
      const stderr = String(err?.stderr || '');
      if (!stderr.includes('env: python: No such file or directory')) {
        throw err;
      }
      const gmshPath = execFileSync('which', ['gmsh'], { encoding: 'utf8' }).trim();
      execFileSync('python3', [gmshPath, ...args], { stdio: 'pipe', timeout: timeoutMs });
    }
    return fs.readFileSync(mshPath, 'utf8');
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

function generateMshViaPythonApi(geoText, { mshVersion = '2.2', timeoutMs = DEFAULT_GMSH_TIMEOUT_MS } = {}) {
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
      { stdio: 'pipe', timeout: timeoutMs }
    );
    return fs.readFileSync(mshPath, 'utf8');
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

function probePythonGmsh(timeoutMs) {
  try {
    const version = execFileSync(
      'python3',
      ['-c', 'import gmsh; print(getattr(gmsh, "__version__", "unknown"))'],
      { encoding: 'utf8', stdio: 'pipe', timeout: timeoutMs }
    ).trim();
    return { available: true, detail: version || 'unknown' };
  } catch (err) {
    return { available: false, detail: formatError(err) };
  }
}

function probeGmshCli(timeoutMs) {
  try {
    const version = execFileSync(
      'gmsh',
      ['-version'],
      { encoding: 'utf8', stdio: 'pipe', timeout: timeoutMs }
    ).trim();
    return { available: true, detail: version || 'unknown' };
  } catch (err) {
    const stderr = String(err?.stderr || '');
    if (stderr.includes('env: python: No such file or directory')) {
      try {
        const gmshPath = execFileSync('which', ['gmsh'], { encoding: 'utf8' }).trim();
        const version = execFileSync(
          'python3',
          [gmshPath, '-version'],
          { encoding: 'utf8', stdio: 'pipe', timeout: timeoutMs }
        ).trim();
        const pythonAliasReady = commandAvailable('python', timeoutMs);
        const note = pythonAliasReady
          ? 'python-wrapper via python3'
          : 'python-wrapper via python3 (missing `python` command on PATH)';
        return { available: true, detail: `${version || 'unknown'}; ${note}` };
      } catch (wrapperErr) {
        return { available: false, detail: formatError(wrapperErr) };
      }
    }
    return { available: false, detail: formatError(err) };
  }
}

async function probeBackend(backendUrl, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${backendUrl}/health`, { signal: controller.signal });
    if (!response.ok) {
      return { available: false, detail: `HTTP ${response.status}` };
    }
    return { available: true, detail: 'reachable' };
  } catch (err) {
    if (err?.name === 'AbortError') {
      return { available: false, detail: `timeout after ${timeoutMs}ms` };
    }
    return { available: false, detail: err?.message || String(err) };
  } finally {
    clearTimeout(timer);
  }
}

async function runSmokeCheck(method, { backendUrl, timeoutMs }) {
  try {
    if (method === 'backend') {
      const result = await generateMeshFromGeo(
        { geoText: SMOKE_GEO_TEXT, mshVersion: '2.2', binary: false, timeoutMs },
        backendUrl
      );
      if (typeof result?.msh === 'string' && result.generatedBy === 'gmsh') {
        return { ok: true, detail: 'mesh smoke ok' };
      }
      return { ok: false, detail: 'backend returned invalid smoke response' };
    }
    if (method === 'python') {
      const msh = generateMshViaPythonApi(SMOKE_GEO_TEXT, { timeoutMs, mshVersion: '2.2' });
      return {
        ok: msh.includes('$MeshFormat'),
        detail: msh.includes('$MeshFormat') ? 'mesh smoke ok' : 'missing $MeshFormat in smoke output'
      };
    }
    if (method === 'cli') {
      const msh = generateMshViaCli(SMOKE_GEO_TEXT, { timeoutMs, mshVersion: '2.2' });
      return {
        ok: msh.includes('$MeshFormat'),
        detail: msh.includes('$MeshFormat') ? 'mesh smoke ok' : 'missing $MeshFormat in smoke output'
      };
    }
  } catch (err) {
    return { ok: false, detail: formatError(err) };
  }
  return { ok: false, detail: `unknown method ${method}` };
}

async function buildGenerationContext({
  backendUrl,
  mode,
  probeTimeoutMs = DEFAULT_PROBE_TIMEOUT_MS,
  backendProbeTimeoutMs = DEFAULT_BACKEND_PROBE_TIMEOUT_MS,
  smokeTimeoutMs = DEFAULT_SMOKE_TIMEOUT_MS
}) {
  const normalizedMode = normalizeGmshMode(mode);
  const methodsByMode = {
    auto: ['backend', 'python', 'cli'],
    backend: ['backend'],
    python: ['python'],
    cli: ['cli']
  };
  const plannedMethods = methodsByMode[normalizedMode];
  const capabilities = {
    backend: { available: false, detail: 'not checked', smoke: { ok: false, detail: 'not checked' } },
    python: { available: false, detail: 'not checked', smoke: { ok: false, detail: 'not checked' } },
    cli: { available: false, detail: 'not checked', smoke: { ok: false, detail: 'not checked' } }
  };

  if (plannedMethods.includes('backend')) {
    capabilities.backend = await probeBackend(backendUrl, backendProbeTimeoutMs);
  }
  if (plannedMethods.includes('python')) {
    capabilities.python = probePythonGmsh(probeTimeoutMs);
  }
  if (plannedMethods.includes('cli')) {
    capabilities.cli = probeGmshCli(probeTimeoutMs);
  }

  for (const method of plannedMethods) {
    const capability = capabilities[method];
    if (!capability.available) {
      capability.smoke = { ok: false, detail: 'probe unavailable' };
      continue;
    }
    capability.smoke = await runSmokeCheck(method, {
      backendUrl,
      timeoutMs: smokeTimeoutMs
    });
  }

  const availableMethods = plannedMethods.filter(
    (method) => capabilities[method].available && capabilities[method].smoke.ok
  );
  return {
    mode: normalizedMode,
    backendUrl,
    plannedMethods,
    availableMethods,
    capabilities
  };
}

async function generateMshWithGmsh(
  geoText,
  {
    mshVersion = '2.2',
    backendUrl = DEFAULT_BACKEND_URL,
    mode = 'auto',
    timeoutMs = DEFAULT_GMSH_TIMEOUT_MS,
    context = null
  } = {}
) {
  const generationContext = context || await buildGenerationContext({ backendUrl, mode });
  const attempts = [];

  if (generationContext.availableMethods.length === 0) {
    throw new AthParityGenerationError(
      `no available gmsh generation methods for mode '${generationContext.mode}'`,
      {
        infrastructure: true,
        attempts,
        capabilities: generationContext.capabilities
      }
    );
  }

  for (const method of generationContext.availableMethods) {
    const startedAt = Date.now();
    try {
      if (method === 'backend') {
        const result = await generateMeshFromGeo(
          { geoText, mshVersion, binary: false, timeoutMs },
          backendUrl
        );
        if (result?.generatedBy === 'gmsh' && typeof result?.msh === 'string') {
          return result.msh;
        }
        throw new Error('backend returned an invalid Gmsh response');
      }
      if (method === 'python') {
        return generateMshViaPythonApi(geoText, { mshVersion, timeoutMs });
      }
      if (method === 'cli') {
        return generateMshViaCli(geoText, { mshVersion, timeoutMs });
      }
      throw new Error(`unsupported generation method: ${method}`);
    } catch (err) {
      const smokePassed = generationContext.capabilities?.[method]?.smoke?.ok === true;
      const classification = classifyInfrastructureFailure(method, err, { smokePassed });
      attempts.push({
        method,
        infrastructure: classification.infrastructure,
        reason: classification.reason,
        durationMs: Date.now() - startedAt,
        detail: formatError(err)
      });
      if (!classification.infrastructure) {
        throw new AthParityGenerationError(
          `non-infrastructure gmsh failure in '${method}': ${formatError(err)}`,
          {
            infrastructure: false,
            attempts,
            capabilities: generationContext.capabilities
          }
        );
      }
    }
  }

  throw new AthParityGenerationError(
    `unable to generate .msh via Gmsh (${generationContext.mode} mode)`,
    {
      infrastructure: true,
      attempts,
      capabilities: generationContext.capabilities
    }
  );
}

function printInfrastructureFixSteps(generationContext, { gmshTimeoutMs, strictInfra }) {
  const backend = generationContext.capabilities.backend;
  const python = generationContext.capabilities.python;
  const cli = generationContext.capabilities.cli;
  console.log('\n[ath-parity] infrastructure diagnostics / local fix steps:');
  if (!backend.available || !backend.smoke.ok) {
    console.log(
      `  - backend (${generationContext.backendUrl}): probe=${backend.detail}; smoke=${backend.smoke.detail}`
    );
    console.log('    fix: start backend with `python3 server/app.py` then verify `curl http://localhost:8000/health`.');
  }
  if (!python.available || !python.smoke.ok) {
    console.log(`  - python gmsh: probe=${python.detail}; smoke=${python.smoke.detail}`);
    console.log('    fix: install gmsh Python package in active interpreter: `python3 -m pip install "gmsh>=4.10,<5.0"`.');
  }
  if (!cli.available || !cli.smoke.ok || cli.detail.includes('missing `python` command on PATH')) {
    console.log(`  - gmsh CLI: probe=${cli.detail}; smoke=${cli.smoke.detail}`);
    console.log(
      '    fix: install a native gmsh binary on PATH (preferred), or add a `python` shim/alias that points to `python3`.'
    );
    console.log('    verify: `gmsh -version` and `python3 $(which gmsh) -version`.');
  }
  console.log(
    `  - reference generation timeout is ${gmshTimeoutMs}ms. If probes/smoke pass but ATH mesh still times out, use backend mode against a running backend and a larger timeout.`
  );
  console.log(
    `    example: ATH_PARITY_GMSH_MODE=backend ATH_PARITY_BACKEND_URL=http://localhost:8000 ATH_PARITY_GMSH_TIMEOUT_MS=120000 npm run test:ath`
  );
  if (!strictInfra) {
    console.log('  - strict mode is disabled for this run (`ATH_PARITY_STRICT_INFRA=0`). Set `ATH_PARITY_STRICT_INFRA=1` (or unset it) to hard-fail infra issues.');
  }
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
  const strictInfra = process.env.ATH_PARITY_STRICT_INFRA !== '0';
  const gmshTimeoutMs = envNumber('ATH_PARITY_GMSH_TIMEOUT_MS', DEFAULT_GMSH_TIMEOUT_MS);
  const probeTimeoutMs = envNumber('ATH_PARITY_PROBE_TIMEOUT_MS', DEFAULT_PROBE_TIMEOUT_MS);
  const backendProbeTimeoutMs = envNumber('ATH_PARITY_BACKEND_PROBE_TIMEOUT_MS', DEFAULT_BACKEND_PROBE_TIMEOUT_MS);
  const smokeTimeoutMs = envNumber('ATH_PARITY_SMOKE_TIMEOUT_MS', DEFAULT_SMOKE_TIMEOUT_MS);
  const generationContext = await buildGenerationContext({
    backendUrl: gmshBackendUrl,
    mode: gmshMode,
    probeTimeoutMs,
    backendProbeTimeoutMs,
    smokeTimeoutMs
  });

  console.log(
    `[ath-parity] gmsh mode=${generationContext.mode}, timeout=${gmshTimeoutMs}ms, ` +
    `methods=${generationContext.availableMethods.join(',') || 'none'}`
  );
  console.log(
    `[ath-parity] probes backend=${generationContext.capabilities.backend.available ? 'ok' : `unavailable (${generationContext.capabilities.backend.detail})`}, ` +
    `python=${generationContext.capabilities.python.available ? 'ok' : `unavailable (${generationContext.capabilities.python.detail})`}, ` +
    `cli=${generationContext.capabilities.cli.available ? 'ok' : `unavailable (${generationContext.capabilities.cli.detail})`}`
  );
  console.log(
    `[ath-parity] smoke backend=${generationContext.capabilities.backend.smoke.ok ? 'ok' : `fail (${generationContext.capabilities.backend.smoke.detail})`}, ` +
    `python=${generationContext.capabilities.python.smoke.ok ? 'ok' : `fail (${generationContext.capabilities.python.smoke.detail})`}, ` +
    `cli=${generationContext.capabilities.cli.smoke.ok ? 'ok' : `fail (${generationContext.capabilities.cli.smoke.detail})`}`
  );

  ensureDir(outRoot);

  const configs = fs.readdirSync(root)
    .filter((name) => name.toLowerCase().endsWith('.txt'))
    .sort((a, b) => a.localeCompare(b));

  if (configs.length === 0) {
    console.log(`[ath-parity] skipped: no config files under ${root}`);
    return 0;
  }

  let failures = 0;
  let passes = 0;
  let skippedMissingRef = 0;
  let skippedInfra = 0;
  let sawInfraFailure = false;

  for (const configFile of configs) {
    const base = path.basename(configFile, '.txt');
    const configPath = path.join(root, configFile);
    const refMshPath = findReferenceMsh(root, base);

    if (!refMshPath) {
      console.log(`${base}: SKIP (missing ATH .msh reference)`);
      skippedMissingRef += 1;
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
        mode: gmshMode,
        timeoutMs: gmshTimeoutMs,
        context: generationContext
      });
    } catch (err) {
      const isInfra = err instanceof AthParityGenerationError && err.infrastructure;
      if (isInfra && !strictInfra) {
        sawInfraFailure = true;
        skippedInfra += 1;
        console.log(`${base}: SKIP (gmsh infrastructure unavailable)`);
        const disabled = new Set(
          err.attempts
            .filter((attempt) => attempt.infrastructure)
            .map((attempt) => attempt.method)
        );
        if (disabled.size > 0) {
          generationContext.availableMethods = generationContext.availableMethods
            .filter((method) => !disabled.has(method));
          console.log(
            `  - disabling failed method(s) for remaining configs: ${[...disabled].join(', ')}`
          );
        }
      } else {
        failures += 1;
        console.log(`${base}: FAIL (gmsh generation error: ${err.message})`);
      }
      if (err instanceof AthParityGenerationError) {
        err.attempts.forEach((attempt) => {
          console.log(
            `  - ${attempt.method}: ${attempt.reason}; duration=${attempt.durationMs}ms; ${attempt.detail}`
          );
        });
      }
      if (strictInfra) {
        break;
      }
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
      passes += 1;
      console.log(
        `${base}: OK (cloud max fwd=${result.cloud.maxForward.toFixed(4)}, back=${result.cloud.maxBackward.toFixed(4)})`
      );
    }
  }

  console.log(
    `\nATH parity summary: ok=${passes}, fail=${failures}, skip_ref=${skippedMissingRef}, skip_infra=${skippedInfra}`
  );

  if (skippedInfra > 0 && !strictInfra) {
    console.log('[ath-parity] note: strict infra mode is disabled by ATH_PARITY_STRICT_INFRA=0.');
  }
  if (passes === 0 && (skippedInfra > 0 || failures > 0 || sawInfraFailure)) {
    printInfrastructureFixSteps(generationContext, { gmshTimeoutMs, strictInfra });
  }

  if (failures > 0) {
    console.log(`ATH parity failed for ${failures} config(s).`);
    return 1;
  }

  if (passes > 0) {
    console.log('ATH parity checks passed for available references.');
  } else {
    console.log('ATH parity completed with no runnable reference comparisons.');
  }
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
