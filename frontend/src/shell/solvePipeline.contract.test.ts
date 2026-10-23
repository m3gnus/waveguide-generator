import { describe, expect, it } from 'vitest';

const sources = import.meta.glob('../**/*.{ts,tsx}', { eager: true, query: '?raw', import: 'default' }) as Record<string, string>;
const production = Object.entries(sources).filter(([path]) => !/\.(test|fixtures?)\./.test(path) && path !== '../api/generated/openapi.ts');
const owner = './JobsCoordinator.tsx';
const definers: Record<string, string> = {
  submitDesign: '../jobs/actions.ts',
  solveModel: '../jobs/actions.ts',
  rerunWithPatch: '../jobs/rerun.ts',
  solveCadAgain: '../jobs/cadSolve.ts',
  approveCadJob: '../jobs/cadSolve.ts',
  retryJob: '../api/jobsSocket.ts',
};
const names = Object.keys(definers).join('|');
// Preserve literals (including template URLs) while ignoring comments and their examples.
const literalsAndComments = /'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`|\/\/[^\n]*|\/\*[\s\S]*?\*\//g;
const restrictedModule = /\/(?:jobs\/(?:actions|rerun|cadSolve)|api\/jobsSocket)(?:\.tsx?)?$/;
const endpoints: Array<[RegExp, string[]]> = [
  [/^\/api\/solve$/, ['../jobs/actions.ts', '../jobs/rerun.ts']],
  [/^\/api\/models\/[^/]+\/solve$/, ['../jobs/actions.ts']],
  [/\/solve-again$/, ['../jobs/cadSolve.ts']],
  [/\/approvals$/, ['../jobs/cadSolve.ts']],
  [/^\/api\/jobs\/[^/]+\/retry$/, ['../api/jobsSocket.ts']],
];

/** Resolve from the scanned module, so same-directory imports cannot evade the guard. */
function modulePath(path: string, specifier: string): string {
  const parts = ['shell', ...path.split('/').slice(0, -1), ...specifier.split('/')];
  const resolved: string[] = [];
  for (const part of parts) {
    if (part === '..') resolved.pop();
    else if (part !== '.') resolved.push(part);
  }
  return `/${resolved.join('/')}`;
}

function violations(path: string, raw: string): string[] {
  const source = raw.replace(literalsAndComments, (token) => token.startsWith('//') || token.startsWith('/*') ? ' ' : token);
  const problems: string[] = [];
  const references = new RegExp(`\\b(${names})\\b`);
  // Re-exports must not hide a submitter behind an alias. Namespace and dynamic
  // imports of these modules are deliberately disallowed: use auditable named imports.
  for (const match of source.matchAll(/\b(import|export)\s+([^;'"`]*?)\s+from\s*['"]([^'"]+)['"]/g)) {
    if (match[2].trim().startsWith('type ')) continue;
    if (restrictedModule.test(modulePath(path, match[3])) && (match[1] === 'export' || path !== owner)
      && (references.test(match[2]) || /\*/.test(match[2]))) problems.push('submission import/re-export');
  }
  for (const match of source.matchAll(/\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g)) {
    if (restrictedModule.test(modulePath(path, match[1]))) problems.push('dynamic submission import');
  }
  const code = source.replace(literalsAndComments, ' ');
  if (path !== owner) {
    // Follow named aliases of the socket, including destructuring a bound retry.
    const socketNames = ['jobsSocket'];
    for (const match of source.matchAll(/\bjobsSocket\s+as\s+(\w+)/g)) socketNames.push(match[1]);
    for (const socket of socketNames) {
      if (new RegExp(`\\b${socket}\\s*(?:\\?\\.)?\\s*(?:\\.\\s*retryJob|\\[\\s*['"]retryJob['"]\\s*\\])`).test(source)
        || new RegExp(`\\{[^{}]*\\bretryJob\\b[^{}]*\\}\\s*=\\s*${socket}\\b`).test(code)) problems.push('socket retry bypass');
    }
  }
  // A definer may define the primitive, but cannot call it itself. In particular,
  // the old approveCadJob -> solveCadAgain wrapper must fail this same guard.
  for (const [name, definer] of Object.entries(definers)) {
    if (path !== definer) continue;
    const withoutDefinition = code.replace(new RegExp(`\\b(?:function\\s+|async\\s+)${name}\\s*\\(`), '(');
    if (new RegExp(`\\b${name}\\b`).test(withoutDefinition)) problems.push(`definer calls ${name}`);
  }
  // Endpoints are owned individually, not by a blanket exemption for all API files.
  for (const token of source.match(literalsAndComments) ?? []) {
    const url = token.slice(1, -1);
    for (const [endpoint, allowed] of endpoints) {
      if (endpoint.test(url) && !allowed.includes(path)) problems.push(`direct submission endpoint: ${url}`);
    }
  }
  return problems;
}

describe('one Solve owner', () => {
  it('confines submitters, their callers and their HTTP endpoints to their owners', () => {
    expect(production.flatMap(([path, source]) => violations(path, source).map((problem) => `${path}: ${problem}`))).toEqual([]);
  });

  it.each([
    `import { submitDesign as send } from '../jobs/actions'; send();`,
    `import * as x from '../jobs/actions'; x.submitDesign();`,
    `import * as x from '../api/jobsSocket'; x.jobsSocket.retryJob('id');`,
    `const x = await import('../jobs/cadSolve'); x.solveCadAgain();`,
    `export { solveModel as send } from '../jobs/actions';`,
    `export * from '../jobs/rerun';`,
    `import { jobsSocket } from '../api/jobsSocket'; const { retryJob: retry } = jobsSocket; retry('id');`,
    `import { jobsSocket as socket } from '../api/jobsSocket'; socket.retryJob('id');`,
    `jobsSocket['retryJob']('id');`,
    `fetch('/api/solve', { method: 'POST' });`,
    'fetch(`/api/models/${id}/solve`, { method: "POST" });',
    'post(`/api/jobs/${id}/solve-again`, {});',
    'post(`/api/jobs/${id}/approvals`, {});',
    'fetch(`/api/jobs/${id}/retry`, { method: "POST" });',
  ])('rejects a submission bypass: %s', (source) => {
    expect(violations('./Bypass.tsx', source).length).toBeGreaterThan(0);
  });

  it('also resolves same-directory submission imports', () => {
    expect(violations('../jobs/bypass.ts', `import * as x from './actions'; x.submitDesign();`)).not.toEqual([]);
    expect(violations('../jobs/bypass.ts', `const x = import('./cadSolve');`)).not.toEqual([]);
  });

  it('rejects the old approval wrapper inside its defining module', () => {
    expect(violations('../jobs/cadSolve.ts', `
      export function solveCadAgain(jobId, press) { return post(\`/api/jobs/\${jobId}/solve-again\`, press); }
      export async function approveCadJob(jobId, approvals) {
        await post(\`/api/jobs/\${jobId}/approvals\`, approvals);
        return solveCadAgain(jobId, { approvals });
      }
    `)).toContain('definer calls solveCadAgain');
  });

  it('allows each primitive definition and only the coordinator as its production caller', () => {
    for (const [name, definer] of Object.entries(definers)) {
      const definition = name === 'retryJob' ? `class Socket { async retryJob(id) {} }` : `export async function ${name}() {}`;
      expect(violations(definer, definition)).toEqual([]);
      expect(violations(owner, `import { ${name} } from '${definer.slice(0, -3)}'; ${name}();`)).toEqual([]);
    }
    expect(violations('../jobs/cadSolve.ts', `fetch('/api/solve', { method: 'POST' })`)).not.toEqual([]);
  });

  it('exposes no bypass on the coordinator bridge', () => {
    const source = sources['./JobsCoordinator.tsx'];
    const bridge = source.slice(source.indexOf('interface CoordinatorBridgeSnapshot'), source.indexOf('const bridgeListeners'));
    expect(bridge).not.toMatch(/\b(run|acceptSubmission|solveCurrentCadImport|retry)\s*[:(]/);
    expect(bridge).toContain('continueRun(');
  });
});

/** Display identities and verdict vocabulary have one owner. Lifecycle ingestion
 * reads (loading/adopting artifacts) and run-list grouping are not display verdicts. */
function displayVerdictViolations(raw: string): string[] {
  const source = raw.replace(literalsAndComments, token => token.startsWith('//') || token.startsWith('/*') ? ' ' : token);
  const problems: string[] = [];
  if (/\b(?:function|const|type|interface)\s+(?:runVerdict|runDisplayVerdict|runMatchesContext|RunContext|RunContextVerdict)\b/.test(source)) problems.push('private verdict definition');
  const identities = ['(?:\\w+\\??\\.)?cad_source(?:\\??\\.\\s*(?:ingest_id|solve_model_sha256))?', 'jobDesignFingerprint\\([^)]*\\)'];
  // Follow local aliases too, so splitting a raw identity comparison over two
  // statements does not turn it into an allowed display selector.
  for (let pass = 0; pass < 3; pass += 1) {
    for (const match of source.matchAll(/\b(?:const|let)\s+(\w+)\s*=\s*([^;\n]+)/g)) {
      if (identities.some(identity => new RegExp(identity).test(match[2])) && !identities.includes(`\\b${match[1]}\\b(?:\\??\\.(?:ingest_id|solve_model_sha256))?`)) identities.push(`\\b${match[1]}\\b(?:\\??\\.(?:ingest_id|solve_model_sha256))?`);
    }
  }
  const liveIdentity = '(?:context|model|viewport|coherenceContext)\\??\\.\\s*(?:key|ingestId|displayedIngestId|displayedSolveModelSha256|designFingerprint)';
  for (const identity of identities) {
    if (new RegExp(`(?:${identity})\\s*[!=]==?\\s*${liveIdentity}|${liveIdentity}\\s*[!=]==?\\s*(?:${identity})`).test(source)) problems.push('private run-versus-viewport comparison');
  }
  return problems;
}

/** Inspect each spatial consumer, so a valid call elsewhere in the file cannot
 * mask one field path falling back to the chart-only legacy exception. */
function spatialSelectorViolations(raw: string): string[] {
  const problems: string[] = [];
  for (const name of ['fieldPlaneJob', 'femFieldJob']) {
    const start = raw.indexOf(`export function ${name}(`);
    const next = raw.indexOf('\nexport ', start + 1);
    const body = start < 0 ? '' : raw.slice(start, next < 0 ? undefined : next);
    if (!/const candidate = spatialResult\(context, \{ primary: selectedJobId \}, jobs, provisional\);/.test(body)) {
      problems.push(`${name}: spatial selection bypass`);
    }
  }
  if (!/fieldEnabled && fieldSource === 'exterior' && fieldJobId === availableFieldJob\?\.id\s*&& <FieldPlaneProbeTooltip/.test(raw)) {
    problems.push('FieldPlaneProbeTooltip: spatial selection bypass');
  }
  // Viewport has no use for the standalone-chart exception.
  if (/\bshownResult\b/.test(raw.replace(literalsAndComments, token => token.startsWith('//') || token.startsWith('/*') ? ' ' : token))) problems.push('chart selector in viewport');
  return problems;
}

describe('one current model and shown result owner', () => {
  it('keeps run-versus-viewport verdicts in currentModel/runCoherence', () => {
    const owners = ['../model/currentModel.ts', '../results/runCoherence.ts'];
    expect(production.filter(([path]) => !owners.includes(path)).flatMap(([path, source]) =>
      displayVerdictViolations(source).map(problem => `${path}: ${problem}`))).toEqual([]);
  });

  it.each([
    `function runDisplayVerdict(job, context) { return 'current'; }`,
    `const match = job.cad_source?.ingest_id === model.ingestId;`,
    `const ingest = job.cad_source?.ingest_id; return ingest === context.ingestId;`,
    `const source = job.cad_source; const ingest = source.ingest_id; return viewport.ingestId === ingest;`,
    `return jobDesignFingerprint(job) === context.designFingerprint;`,
  ])('rejects a private display verdict: %s', source => {
    expect(displayVerdictViolations(source)).not.toEqual([]);
  });

  it('routes both geometry field paths through the spatial selector', () => {
    expect(spatialSelectorViolations(sources['../viewport/Viewport.tsx'])).toEqual([]);
    expect(sources['../viewport/Viewport.tsx']).toContain('spatialResultId={availablePlaneJob?.id ?? null}');
    const canvas = sources['../viewport/ViewportCanvas.tsx'];
    expect(canvas).toContain('const fieldCurrent = spatialResultId !== null && spatialResultId === fieldJobId;');
    expect(canvas).toContain('active={fieldCurrent}');
    expect(sources['../viewport/FieldPlane.tsx']).toContain('props.active && state.enabled');
    expect(canvas).toContain("if (clipMode === 'field-plane' && fieldCurrent && fieldPlane)");
  });

  it.each(['fieldPlaneJob', 'femFieldJob', 'FieldPlaneProbeTooltip'])('rejects a spatial-selector bypass in %s even if the other paths are guarded', name => {
    const source = sources['../viewport/Viewport.tsx'];
    const start = source.indexOf(`export function ${name}(`);
    const changed = name === 'FieldPlaneProbeTooltip'
      ? source.replace(' && fieldJobId === availableFieldJob?.id', '')
      : source.slice(0, start) + source.slice(start).replace('spatialResult(context, { primary: selectedJobId }, jobs, provisional)',
      'shownResult(context, { primary: selectedJobId }, jobs, provisional).job');
    expect(spatialSelectorViolations(changed)).toContain(`${name}: spatial selection bypass`);
  });

  it('uses the effective solve representation for live and saved parametric identity', () => {
    const source = sources['../results/runCoherence.ts'];
    expect(source).toContain('canonicalJson(serializeSolveDesign(design))');
    expect(source).toContain('design ? designFingerprint(design) : null');
    expect(source).not.toMatch(/\bserializeDesign\(/);
  });

  it('routes every result consumer to the shared selector', () => {
    for (const path of ['./ResultsPanel.tsx', '../results/exportContext.ts']) {
      expect(sources[path]).toMatch(/\bshownResult\(/);
    }
    for (const path of ['../jobs/RunExportControl.tsx', '../jobs/autoExportRun.ts', '../results/exporters.ts']) {
      expect(sources[path]).toMatch(/\bresultExportSelection\(/);
    }
    expect(sources['./JobsCoordinator.tsx']).toMatch(/\bcurrentModel\(/);
    expect(sources['./JobsCoordinator.tsx']).not.toMatch(/cadViewportGeometry|viewportGeometry\.cad/);
    expect(sources['../viewport/Viewport.tsx']).toMatch(/currentModelScene\(/);
  });
});
