import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const SRC_ROOT = path.join(ROOT, 'src');

const LEGACY_EXCEPTIONS = new Set([
  'app/App.js->ui/paramPanel.js',
  'app/App.js->ui/simulationPanel.js',
  'app/configImport.js->ui/feedback.js',
  'app/configImport.js->ui/fileOps.js',
  'app/events.js->ui/fileOps.js',
  'app/params.js->geometry/index.js',
  'app/updates.js->ui/feedback.js',
  'ui/simulation/SimulationPanel.js->solver/index.js'
]);

const BOUNDARY_RULES = [
  {
    id: 'app-must-use-modules-boundary',
    fromLayer: 'app',
    forbiddenTargets: new Set(['geometry', 'solver', 'export', 'ui']),
    message: 'src/app must call modules instead of importing internal geometry/solver/export/ui packages directly.'
  },
  {
    id: 'ui-must-not-call-core-internals',
    fromLayer: 'ui',
    forbiddenTargets: new Set(['geometry', 'solver', 'export']),
    message: 'src/ui should call modules (or ui-only helpers), not geometry/solver/export internals.'
  },
  {
    id: 'modules-must-not-import-app',
    fromLayer: 'modules',
    forbiddenTargets: new Set(['app']),
    message: 'src/modules are app-facing APIs and must not depend on src/app internals.'
  },
  {
    id: 'viewer-must-not-depend-on-app-ui-or-solver',
    fromLayer: 'viewer',
    forbiddenTargets: new Set(['app', 'modules', 'ui', 'solver', 'export']),
    message: 'src/viewer should remain a rendering package and avoid app/module/ui/solver/export dependencies.'
  }
];

const IMPORT_RE = /^\s*import\s+[\s\S]*?\s+from\s+['\"]([^'\"]+)['\"]/gm;
const DYNAMIC_IMPORT_RE = /import\(\s*['\"]([^'\"]+)['\"]\s*\)/g;

function listJsFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...listJsFiles(full));
      continue;
    }
    if (entry.isFile() && full.endsWith('.js')) {
      out.push(full);
    }
  }
  return out;
}

function extractImportSpecs(content) {
  const specs = [];
  let match = null;
  IMPORT_RE.lastIndex = 0;
  while ((match = IMPORT_RE.exec(content)) !== null) {
    specs.push(match[1]);
  }
  DYNAMIC_IMPORT_RE.lastIndex = 0;
  while ((match = DYNAMIC_IMPORT_RE.exec(content)) !== null) {
    specs.push(match[1]);
  }
  return specs;
}

function resolveImport(importerFile, spec) {
  if (!spec.startsWith('.')) {
    return null;
  }
  const resolved = path.resolve(path.dirname(importerFile), spec);
  if (!resolved.startsWith(SRC_ROOT + path.sep)) {
    return null;
  }
  if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
    return resolved;
  }
  if (fs.existsSync(`${resolved}.js`)) {
    return `${resolved}.js`;
  }
  const indexPath = path.join(resolved, 'index.js');
  if (fs.existsSync(indexPath)) {
    return indexPath;
  }
  return null;
}

function toSrcRelative(filePath) {
  return path.relative(SRC_ROOT, filePath).replace(/\\/g, '/');
}

function layerFor(filePath) {
  const relative = toSrcRelative(filePath);
  const [top] = relative.split('/');
  return top;
}

function collectViolations() {
  const violations = [];
  const files = listJsFiles(SRC_ROOT);

  for (const file of files) {
    const importerLayer = layerFor(file);
    const content = fs.readFileSync(file, 'utf8');
    const specs = extractImportSpecs(content);

    for (const spec of specs) {
      const targetFile = resolveImport(file, spec);
      if (!targetFile) continue;

      const targetLayer = layerFor(targetFile);
      const fromRel = toSrcRelative(file);
      const toRel = toSrcRelative(targetFile);
      const edgeKey = `${fromRel}->${toRel}`;

      for (const rule of BOUNDARY_RULES) {
        if (importerLayer !== rule.fromLayer) continue;
        if (!rule.forbiddenTargets.has(targetLayer)) continue;
        if (LEGACY_EXCEPTIONS.has(edgeKey)) continue;
        violations.push({
          ruleId: rule.id,
          edgeKey,
          message: rule.message
        });
      }
    }
  }

  return violations;
}

test('frontend import boundaries only allow approved cross-layer dependencies', () => {
  const violations = collectViolations();

  assert.equal(
    violations.length,
    0,
    violations
      .map((entry) => `[${entry.ruleId}] ${entry.edgeKey} :: ${entry.message}`)
      .join('\n')
  );
});
