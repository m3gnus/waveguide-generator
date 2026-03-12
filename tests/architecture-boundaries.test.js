import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const SRC_ROOT = path.join(ROOT, 'src');

const LEGACY_EXCEPTIONS = new Set([
  'app/exports.js->ui/feedback.js',
  'app/exports.js->ui/fileOps.js'
]);
const MODULE_BROWSER_EDGE_EXCEPTIONS = new Set([
  'modules/design/useCases.js->state.js',
  'modules/geometry/useCases.js->state.js',
  'modules/simulation/useCases.js->state.js',
  'modules/simulation/workspaceTasks.js->ui/workspace/folderWorkspace.js',
  'modules/simulation/workspaceTasks.js->ui/workspace/taskIndex.js',
  'modules/simulation/workspaceTasks.js->ui/workspace/taskManifest.js',
  'modules/ui/index.js->ui/fileOps.js',
  'modules/ui/useCases.js->ui/feedback.js',
  'modules/ui/useCases.js->ui/fileOps.js',
  'modules/ui/useCases.js->ui/workspace/folderWorkspace.js'
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

const MODULE_BROWSER_EDGE_RULES = [
  {
    id: 'modules-must-not-import-ambient-state',
    matchesTarget: (toRel) => toRel === 'state.js',
    message: 'src/modules should receive state snapshots from the app edge instead of importing GlobalState directly.'
  },
  {
    id: 'modules-must-not-import-browser-file-feedback-helpers',
    matchesTarget: (toRel) => toRel === 'ui/fileOps.js' || toRel === 'ui/feedback.js',
    message: 'src/modules should use app/UI adapters for file writing and toast feedback instead of browser helpers directly.'
  },
  {
    id: 'modules-must-not-import-workspace-internals',
    matchesTarget: (toRel) => toRel.startsWith('ui/workspace/'),
    message: 'src/modules should not own folder-workspace persistence internals directly.'
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

      if (importerLayer !== 'modules') {
        continue;
      }

      for (const rule of MODULE_BROWSER_EDGE_RULES) {
        if (!rule.matchesTarget(toRel)) continue;
        if (MODULE_BROWSER_EDGE_EXCEPTIONS.has(edgeKey)) continue;
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

test('module use-case files do not reference browser globals directly outside approved adapters', () => {
  const allowedFiles = new Set([
    'modules/ui/useCases.js'
  ]);
  const files = listJsFiles(path.join(SRC_ROOT, 'modules'));
  const violations = [];

  for (const file of files) {
    const relativePath = toSrcRelative(file);
    if (allowedFiles.has(relativePath)) {
      continue;
    }

    const content = fs.readFileSync(file, 'utf8');
    if (/\bwindow\b|\bdocument\b/.test(content)) {
      violations.push(relativePath);
    }
  }

  assert.equal(
    violations.length,
    0,
    violations.join('\n')
  );
});

test('runtime frontend files do not rely on the __waveguideApp ambient global', () => {
  const files = listJsFiles(SRC_ROOT);
  const violations = [];

  for (const file of files) {
    const relativePath = toSrcRelative(file);
    const content = fs.readFileSync(file, 'utf8');
    if (content.includes('__waveguideApp')) {
      violations.push(relativePath);
    }
  }

  assert.equal(
    violations.length,
    0,
    violations.join('\n')
  );
});

test('runtime frontend files do not rely on the window.app ambient global', () => {
  const files = listJsFiles(SRC_ROOT);
  const violations = [];

  for (const file of files) {
    const relativePath = toSrcRelative(file);
    const content = fs.readFileSync(file, 'utf8');
    if (content.includes('window.app')) {
      violations.push(relativePath);
    }
  }

  assert.equal(
    violations.length,
    0,
    violations.join('\n')
  );
});

test('ui simulation workflow files must not import GlobalState directly', () => {
  const simulationUiRoot = path.join(SRC_ROOT, 'ui', 'simulation');
  const files = listJsFiles(simulationUiRoot);
  const violations = [];

  for (const file of files) {
    const content = fs.readFileSync(file, 'utf8');
    const specs = extractImportSpecs(content);
    for (const spec of specs) {
      const targetFile = resolveImport(file, spec);
      if (!targetFile) continue;
      if (toSrcRelative(targetFile) !== 'state.js') continue;
      violations.push(`${toSrcRelative(file)}->state.js`);
    }
  }

  assert.equal(
    violations.length,
    0,
    violations.join('\n')
  );
});

test('ui simulation workflow files must not import workspace internals directly', () => {
  const simulationUiRoot = path.join(SRC_ROOT, 'ui', 'simulation');
  const files = listJsFiles(simulationUiRoot);
  const violations = [];

  for (const file of files) {
    const content = fs.readFileSync(file, 'utf8');
    const specs = extractImportSpecs(content);
    for (const spec of specs) {
      const targetFile = resolveImport(file, spec);
      if (!targetFile) continue;
      if (!toSrcRelative(targetFile).startsWith('ui/workspace/')) continue;
      violations.push(`${toSrcRelative(file)}->${toSrcRelative(targetFile)}`);
    }
  }

  assert.equal(
    violations.length,
    0,
    violations.join('\n')
  );
});
