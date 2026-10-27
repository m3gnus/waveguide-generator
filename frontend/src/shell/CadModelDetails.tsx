import type { RegisteredMesh, MeshSummary } from '../api/meshImport';
import { sourceRoleLabel } from '../model/cadLabels';
import { useMemo, useState, type ReactNode } from 'react';
import type { CadReturnFinding, CadReturnIngestRecord } from '../api/cadlink';
import { cadMeshSizing } from '../api/cadMesh';
import { planeWords, recordDomainDecision } from '../api/domainDecision';
import type { DomainInterpretation } from '../api/domainInterpretation';
import { useCapabilities } from '../jobs/useCapabilities';
import { useImportedSolvePlan } from '../jobs/useImportedSolvePlan';
import { approvedCadFindings, cadJobSummary, useCadJobs } from '../jobs/cadSolve';
import { isMeshRecoveryFinding } from '../model/cadMeshRecovery';
import { useCurrentModelStatus } from '../model/currentModel';
import { useDeveloperDetails } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { openDiffers, useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { parseFrequencyList } from '../stores/frequencyList';
import { useImportedSolveOptionsStore } from '../stores/solveOptions';
import { CadDomainInterpretation, decisionLine, domainLine } from './CadDomainInterpretation';
import { CadDrawer } from './CadDrawer';
import { shortSha256 } from './CadOperationsSection';
import { CadSolverFrame } from './CadSolverFrameConfirm';
import { defaultSettingsNote } from './CadSolveInputs';
import { DeveloperDetails } from './DeveloperDetails';
import { findingEvidence, findingName, findingSentence } from './cadFindingWords';
import { pluralized } from './cadTime';
import { resolveEngineLabel } from './solveProgress';
import { workspaceNavigation } from './workspaceNavigation';

/**
 * One collapsed "Model details" on the Imported mesh tab (PLAN.md U4): what
 * the model is, how it is solved and which way it radiates (with the one
 * Change), the mesh Fusion set, the settings Solve uses, the checks, and FEM
 * regions. The model's state and its one action are its status line in
 * Results; nothing here repeats them. What the user can act on opens the details
 * by itself; the evidence behind each statement is developer detail.
 */

export const FRESHNESS_COPY: Record<string, string> = {
  current: 'Current — unchanged fingerprint and the saved design still agree with this generator.',
  body_modified: 'CAD body changed after linking. This returned geometry remains the solve truth.',
  missing_design: 'The linked design is not in this workspace registry; this is normal for a model from another machine.',
  design_changed: 'The linked Waveguide Generator design has changed since this geometry was exported.',
  generator_changed: 'The same saved design would export differently with the current generator.',
  unknown: 'Freshness could not be established from the available evidence.',
  unlinked: 'Authored in CAD, not linked to a Waveguide Generator design. WG works out which way the model radiates and shows it in Model details; Solve confirms it for the project.',
  mixed: 'The linked instances disagree about freshness; each instance carries its own verdict.',
};

export function freshnessSummary(record: CadReturnIngestRecord): string {
  if (record.freshness.verdict === 'unlinked') return 'unlinked';
  const instances = record.freshness.instances;
  if (instances.length === 0) return 'unknown';
  if (instances.every((instance) => instance.verdict === 'current')) return 'current';
  if (instances.every((instance) => instance.verdict === instances[0]?.verdict)) {
    return instances[0].verdict.replaceAll('_', ' ');
  }
  return 'mixed';
}

const ROLE_ORDER = ['LF', 'MF', 'HF', 'PORT_EXIT', 'PASSIVE_CARDIOID'];

function rank(role: string): number {
  const index = ROLE_ORDER.indexOf(role);
  return index < 0 ? ROLE_ORDER.length : index;
}

function compactValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

/** A length the symmetry verifier reports in STEP units (millimetres), to two
 * significant figures; null for anything that is not a number. */
function millimetres(value: unknown): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return `${Number(value.toPrecision(2))} mm`;
}

/** The domain reading a record states (M1c-auto), or null before it existed. */
export function recordInterpretation(record: CadReturnIngestRecord): DomainInterpretation | null {
  const value = record.domain_interpretation as DomainInterpretation | undefined;
  return value && typeof value === 'object' && typeof value.reading === 'string' ? value : null;
}

/** The model in one line: its body, its sources, and -- for a record prepared
 * before WG interpreted the domain -- which domain is solved. A newer record
 * states its domain on its own line (`CadDomainInterpretation`). */
export function modelSummary(record: CadReturnIngestRecord): string {
  const bodies = (record.scope?.included ?? []).map((item) => String(item.name ?? item.object_id ?? '')).filter(Boolean);
  const roles = [...new Set((record.sources ?? []).map((source) => String(source.role ?? '').toUpperCase()).filter(Boolean))]
    .sort((a, b) => rank(a) - rank(b));
  const sourceCount = (record.sources ?? []).length;
  const declared = Array.isArray(record.symmetry?.declared_cut_planes)
    ? (record.symmetry.declared_cut_planes as unknown[]).map(String).filter((plane) => plane === 'x0' || plane === 'y0')
    : [];
  const applied = (Array.isArray(record.symmetry?.domain_planes)
    ? (record.symmetry.domain_planes as unknown[]).map(String)
    : record.symmetry?.cut_planes ?? []).filter((plane) => plane === 'x0' || plane === 'y0');
  const domain = declared.length
    ? `${declared.length === 1 ? 'half' : 'quarter'} model, cut on ${declared.map(planeWords).join(' and ')}`
    : applied.length
      ? `full model, WG mirrors it at ${applied.map(planeWords).join(' and ')}`
      : 'full model';
  return [
    bodies.length === 1 ? bodies[0] : bodies.length > 1 ? pluralized(bodies.length, 'body', 'bodies') : null,
    `${pluralized(sourceCount, 'source')}${roles.length ? ` (${roles.map(role => sourceRoleLabel(role)).join(', ')})` : ''}`,
    recordInterpretation(record) ? null : domain,
  ].filter(Boolean).join(' · ');
}

function hertz(value: number): string {
  if (!Number.isFinite(value)) return '—';
  if (value < 1_000) return `${Number(value.toPrecision(3))} Hz`;
  return `${Number((value / 1_000).toPrecision(3))} kHz`;
}

function duration(seconds: number): string | null {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 60) return '<1 min';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `~${minutes} min`;
  return `~${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** The sweep Solve uses, in words; `broken` when its frequency list cannot be read. */
function useSweep(): { text: string; count: number; broken: boolean } {
  const start = useCadReturnStore((state) => state.frequencyStartHz);
  const end = useCadReturnStore((state) => state.frequencyEndHz);
  const rangeCount = useCadReturnStore((state) => state.frequencyCount);
  const mode = useImportedSolveOptionsStore((state) => state.frequencyMode);
  const listText = useImportedSolveOptionsStore((state) => state.frequencyListText);
  if (mode !== 'list') return { text: `${hertz(start)}–${hertz(end)} · ${rangeCount} freq`, count: rangeCount, broken: false };
  const listed = parseFrequencyList(listText).frequencies;
  return listed
    ? { text: pluralized(listed.length, 'listed frequency', 'listed frequencies'), count: listed.length, broken: false }
    : { text: 'frequency list to fix', count: 0, broken: true };
}

/** The settings Solve uses, in one line: sweep, frequency count, engine and a
 * time estimate. They are edited where they always were, in Simulation. */
function SettingsLine({ record, sweep }: { record: CadReturnIngestRecord; sweep: ReturnType<typeof useSweep> }) {
  const engine = useImportedSolveOptionsStore((state) => state.engine);
  const plan = useImportedSolvePlan(true).plan;
  // The engine chosen in the solver selector; Auto names what it resolves to.
  // A capability verdict's own `label` is preferred when a solve plan is on
  // hand; `resolveEngineLabel` (./solveProgress, shared with the run
  // progress line) is the same fallback either way -- never a bare
  // `.toUpperCase()` of the slug.
  const labelOf = (name: string) => plan?.engines.find((verdict) => verdict.name === name)?.label || resolveEngineLabel(name) || name;
  const requested = engine.trim().toLowerCase();
  const engineWords = requested === 'auto'
    ? `${resolveEngineLabel(requested)}${plan?.engine ? ` (${labelOf(plan.engine)})` : ''}`
    : labelOf(requested);
  const measured = record.sizing_estimate?.measured;
  const perFrequency = measured && typeof measured === 'object'
    ? (measured as Record<string, unknown>).solve_seconds_per_freq
    : undefined;
  const estimate = typeof perFrequency === 'number' && sweep.count > 0 ? duration(perFrequency * sweep.count) : null;
  return <p className={`cad-solve-settings${sweep.broken ? ' cad-solve-settings-broken' : ''}`} data-solve-settings="">
    <span>{[sweep.text, engineWords, estimate].filter(Boolean).join(' · ')}</span>
    {' '}<button
      className="link-button"
      data-action="open-settings"
      title="Drivers, crossover, sweep, directivity and the solver live in the Simulation tab."
      onClick={() => workspaceNavigation.navigate('simulation')}
    >Settings…</button>
  </p>;
}

/** How much memory a mesh's solve needs, as the Fusion add-in bands it
 * (`wglink_mesh_sizing.feasibility_from_ram_gb`): a sentence per band above
 * "ok", and whether it needs the user. */
const MEMORY_BAND: Record<string, { sentence: string; attention: boolean }> = {
  caution: { sentence: 'Solving it needs a fair amount of memory.', attention: false },
  warn: { sentence: 'Solving it needs a lot of memory and may be slow; a coarser mesh solves faster.', attention: true },
  infeasible: { sentence: 'It needs more memory than most computers have; make the mesh coarser before solving.', attention: true },
};

/** The memory band of a record's mesh, when it is above "ok". */
export function meshMemoryBand(record: CadReturnIngestRecord) {
  const feasibility = cadMeshSizing(record).feasibility;
  return typeof feasibility === 'string' ? MEMORY_BAND[feasibility] ?? null : null;
}

/**
 * The mesh a preparation was built with, in one line, in the words the
 * Simulation tab uses: the sizes (set in Fusion when the return carries
 * them), its triangles and memory, a sentence when the memory is high, and
 * where to change it. The sizing numbers behind it are developer detail.
 */
export function MeshLine({ record }: { record: CadReturnIngestRecord }) {
  const snapshot = record.mesh_snapshot ?? null;
  const sizes = snapshot
    ? { walls: snapshot.wall_size_mm, sources: snapshot.source_size_mm, zones: snapshot.surface_groups ?? [] }
    : { walls: record.mesh_sizes?.rigid_size_mm, sources: record.mesh_sizes?.source_size_mm ?? {}, zones: record.mesh_sizes?.surface_groups ?? [] };
  const sizing = cadMeshSizing(record);
  const triangles = record.mesh?.stats.triangle_count ?? (typeof sizing.n_triangles === 'number' ? sizing.n_triangles : null);
  const band = meshMemoryBand(record);
  const parts = [
    typeof sizes.walls === 'number' ? `Walls ${sizes.walls} mm` : null,
    ...(record.sources ?? []).map((source) => typeof sizes.sources[source.id] === 'number'
      ? `${sourceRoleLabel(source.role)} source ${sizes.sources[source.id]} mm` : null),
    ...sizes.zones.map((zone) => `${zone.name} — size zone ${zone.size_mm} mm`),
    typeof triangles === 'number' ? `${triangles.toLocaleString('en-US')} triangles` : null,
    typeof sizing.ram_gb === 'number' ? sizing.ram_gb < 0.05 ? 'memory < 0.1 GB' : `memory ≈ ${sizing.ram_gb.toFixed(1)} GB` : null,
  ].filter(Boolean);
  return <div className={`cad-mesh-line${band?.attention ? ' cad-mesh-line-attention' : ''}`} data-mesh-origin={snapshot ? 'fusion' : 'wg'}>
    <p><b>{snapshot ? 'Mesh (set in Fusion)' : 'Mesh'}</b>{parts.length ? ` · ${parts.join(' · ')}` : ''}</p>
    {band && <p className="cad-mesh-memory" role={band.attention ? 'status' : undefined}>{band.sentence}</p>}
    {snapshot && <p className="cad-detail">To change these, open Waveguide Generator ▸ Sources &amp; Mesh… in Fusion and Send again.</p>}
    <DeveloperDetails summary="Mesh sizing">
      {Object.entries(sizing).map(([key, value]) => <div className="cad-row" key={key}><b>{key.replaceAll('_', ' ')}</b><span>{compactValue(value)}</span></div>)}
    </DeveloperDetails>
  </div>;
}

type CheckState = 'ok' | 'info' | 'warn';

interface CheckDescriptor {
  key: string;
  name: string;
  state: CheckState;
  verdict: string;
  /** Hover: what this check verifies, spelled out. */
  title: string;
  /** What the user can act on, one chevron away (what was skipped and why, a
   * freshness error). Absent when the verdict is whole. */
  detail?: ReactNode;
  /** The measurements behind the verdict (residuals, derivations): developer
   * detail, shown only with "Show developer details". */
  evidence?: ReactNode;
}

const CHECK_GLYPH: Record<CheckState, string> = { ok: '✓', info: 'i', warn: '!' };

/** A finding's sentence, or a calm one for a finding that carries only
 * structured evidence (which is developer detail). */
function findingDetail(finding: CadReturnFinding): string {
  return findingSentence(finding) ?? 'Recorded when WG prepared this model.';
}

/** One row per verification the preparation performed. A check asks for
 * attention only when it deviates; the mesh's size is the mesh line. */
function recordChecks(record: CadReturnIngestRecord): CheckDescriptor[] {
  const checks: CheckDescriptor[] = [];

  // Scope
  const scopeClean = record.scope.status === 'clean';
  const included = record.scope.included ?? [];
  // Sketches and planes are never part of a STEP body: not news.
  const skipped = (record.scope.skipped ?? []).filter((item) => item.kind !== 'construction');
  const scopeFindings = record.findings.filter((finding) => finding.kind === 'scope-degradation');
  checks.push({
    key: 'scope',
    name: 'Scope',
    state: scopeClean ? 'ok' : 'warn',
    verdict: scopeClean
      ? included.length
        ? `${pluralized(included.length, 'body', 'bodies')} exported, nothing skipped`
        : 'the exported exterior was complete'
      : `${pluralized(record.scope.degraded_skip_count, 'object')} skipped — solve is degraded`,
    title: 'Everything the CAD document exported for the acoustic exterior, and anything it had to skip.',
    detail: (included.length || skipped.length || scopeFindings.length) ? <>
      {included.map((item, index) => <p key={`${String(item.object_id)}-${index}`} className="cad-detail"><b>Included · {String(item.name ?? item.object_id ?? 'Unnamed object')}</b>{item.body_kind ? ` — ${item.body_kind}` : ''}</p>)}
      {skipped.map((item, index) => <p key={`${String(item.object_id)}-${index}`} className={`cad-detail scope-skip-${item.severity ?? 'info'}`}><b>Skipped · {String(item.name ?? item.object_id ?? item.kind ?? 'Unnamed object')}</b> — {String(item.reason ?? item.kind ?? 'not included')} · {String(item.severity ?? 'info')}</p>)}
      {skipped.length === 0 && scopeFindings.map((finding) => <p key={finding.id} className="cad-detail"><b>{String(finding.object_id ?? 'Unnamed object')}</b> — {findingDetail(finding)}</p>)}
    </> : undefined,
  });

  // Freshness. A model authored in CAD has no WG design to be fresh against,
  // so there is nothing to check.
  const freshness = freshnessSummary(record);
  if (freshness !== 'unlinked') checks.push({
    key: 'freshness',
    name: 'Freshness',
    state: freshness === 'current' ? 'ok' : 'warn',
    verdict: freshness,
    title: FRESHNESS_COPY[freshness.replaceAll(' ', '_')] ?? 'Whether this returned geometry still matches the linked WG design and generator.',
    // Each linked instance's verdict, and an error to act on; which instance
    // (its id) is developer detail.
    detail: record.freshness.instances.length ? <>{record.freshness.instances.map((instance, index) => <div className={`cad-verdict ${instance.verdict === 'current' ? 'ok' : 'warn'}`} key={instance.instance_id}>
          <b>{record.freshness.instances.length > 1 ? `Linked instance ${index + 1}` : 'Linked instance'}</b><span>{FRESHNESS_COPY[instance.verdict] ?? instance.verdict}</span>
          {instance.error && <small>{instance.error}</small>}
        </div>)}</> : undefined,
    evidence: record.freshness.instances.length ? <p className="cad-detail">Instances: {record.freshness.instances.map((instance) => instance.instance_id).join(', ')}</p> : undefined,
  });

  // Symmetry. How the model is solved is the line above the checks; the
  // check speaks only when a cut the CAD author declared does not mirror.
  const planes = Object.entries(record.symmetry.planes ?? {});
  const declared = new Set(Array.isArray(record.symmetry.declared_cut_planes)
    ? (record.symmetry.declared_cut_planes as unknown[]).map(String)
    : []);
  // Only a plane the CAD author declared is a promise; a candidate WG tried and
  // rejected is simply a plane the design is not symmetric about.
  const rejectedDeclared = planes.filter(([name, verdict]) => !verdict.accepted && declared.has(name));
  const appliedCuts = record.symmetry.cut_planes ?? [];
  const domainPlanes = Array.isArray(record.symmetry.domain_planes)
    ? (record.symmetry.domain_planes as unknown[]).map(String)
    : appliedCuts;
  const cadDomain = appliedCuts.length >= 2 ? 'quarter domain' : appliedCuts.length === 1 ? 'half domain' : 'full domain';
  checks.push({
    key: 'symmetry',
    name: 'Symmetry',
    state: rejectedDeclared.length === 0 ? 'ok' : 'warn',
    verdict: rejectedDeclared.length === 0
      ? domainPlanes.length === 0
        ? 'Full model'
        : `${domainPlanes.length >= 2 ? 'Quarter' : 'Half'} model · mirrored at ${domainPlanes.map(planeWords).join(' and ')}`
      : `declared cut ${rejectedDeclared.map(([name]) => planeWords(name)).join(', ')} does not mirror · solving ${cadDomain}`,
    title: 'Mirror planes WG re-tested on the returned STEP after CAD edits, bodies and source tags were applied. A rejected plane keeps the larger safe domain instead of inheriting the parametric reduction.',
    evidence: <>
      {planes.map(([name, verdict]) => {
        const residual = millimetres(verdict.max_residual_step_units ?? verdict.residuals);
        const offModel = millimetres(verdict.worst_off_model_distance_step_units);
        const details = [
          verdict.reason ? String(verdict.reason) : null,
          residual ? `max residual ${residual}` : null,
          offModel ? `worst off-model ${offModel}` : null,
        ].filter(Boolean).join(' · ');
        return <div className="cad-row" key={name}><b>{name}</b><span className={verdict.accepted ? 'ok-text' : 'warn-text'}>{verdict.accepted ? 'accepted' : 'rejected'}</span><small>{details}</small></div>;
      })}
      {planes.length === 0 && <p>No coordinate plane verdicts were recorded.</p>}
      {appliedCuts.length > 0 && <p className="cad-detail">Applied cuts: {appliedCuts.join(', ')}</p>}
    </>,
  });

  // Healing
  const healed = Boolean(record.healing.performed);
  checks.push({
    key: 'healing',
    name: 'Healing',
    state: healed ? 'info' : 'ok',
    verdict: healed ? 'mesh repaired' : 'not needed',
    title: 'WG repairs the mesh when needed and checks the result before solving.',
  });

  // Polar grid
  const polarAxes = Object.entries((record.polar_grid_derivation.axes ?? {}) as Record<string, { symmetry_accepted?: boolean; minimum_deg?: number; maximum_deg?: number; plane?: string }>);
  const widened = polarAxes.filter(([, axis]) => axis.symmetry_accepted === false);
  checks.push({
    key: 'polar',
    name: 'Polar grid',
    state: widened.length ? 'info' : 'ok',
    verdict: polarAxes.length === 0
      ? 'no derivation recorded'
      : widened.length
        ? `${widened.map(([axis]) => axis).join(', ')} widened to a full sweep`
        : 'follows the accepted symmetry',
    title: 'The directivity sweep derived from the symmetry verdicts. An axis whose mirror plane was rejected is swept over the full circle; sweeps may widen but never narrow.',
    evidence: <>
      {polarAxes.map(([axis, spec]) => <div className="cad-row" key={axis}><b>{axis}</b><span>{spec.minimum_deg ?? 0}° … {spec.maximum_deg ?? 180}°{spec.symmetry_accepted === false ? ' · widened' : ''}</span></div>)}
      {Object.entries(record.polar_grid_derivation).filter(([key]) => key !== 'axes').map(([key, value]) => <div className="cad-row" key={key}><b>{key.replaceAll('_', ' ')}</b><span>{compactValue(value)}</span></div>)}
    </>,
  });

  return checks;
}

function CheckRow({ check }: { check: CheckDescriptor }) {
  // A failing check arrives open; the user can still fold it away, so the
  // element is stateful rather than a hard-wired `open` attribute React would
  // keep re-asserting on every render.
  const [open, setOpen] = useState(check.state === 'warn');
  const developerDetails = useDeveloperDetails();
  const evidence = developerDetails ? check.evidence : undefined;
  if (!check.detail && !evidence) {
    return <div className={`cad-check cad-check-${check.state}`} title={check.title}>
      <span className="cad-check-glyph" aria-hidden="true">{CHECK_GLYPH[check.state]}</span>
      <b>{check.name}</b>
      <span className="cad-check-verdict">{check.verdict}</span>
    </div>;
  }
  return <details
    className={`cad-check cad-check-${check.state}`}
    open={open}
    onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
  >
    <summary title={check.title}>
      <span className="cad-check-glyph" aria-hidden="true">{CHECK_GLYPH[check.state]}</span>
      <b>{check.name}</b>
      <span className="cad-check-verdict">{check.verdict}</span>
      <span className="cad-check-chevron" aria-hidden="true">›</span>
    </summary>
    <div className="cad-check-detail">{check.detail}{evidence}</div>
  </details>;
}

/** Non-blocking findings that only record how the domain was read, as asked:
 * the model's summary and domain line already say so. */
const RECORD_ONLY: ReadonlySet<string> = new Set([
  'declared-reduced-domain',
  'interpreted-reduced-domain',
  'recovered-reduced-domain',
  'domain-solved-as-shown',
]);

/** Whether a finding that blocks nothing is still worth a line: every one is,
 * because it can limit confidence in the result (WG cannot say whether the
 * model is stale, say), except a domain reading the summary already states,
 * mesh recovery (the viewport's line), and the unlinked freshness line of a
 * Fusion-first model: no WG design exists to be stale against, which is what
 * such a model is, not news about it. */
function isNote(finding: CadReturnFinding): boolean {
  if (finding.blocking || isMeshRecoveryFinding(finding) || RECORD_ONLY.has(finding.kind)) return false;
  return !(finding.kind === 'freshness' && finding.verdict === 'unlinked');
}

/** The check row that reports the same problem as a finding of this kind. */
const FINDING_CHECK: Record<string, string> = {
  'scope-degradation': 'scope',
  freshness: 'freshness',
};

/** Whether anything in the checks needs the user, and how to say so. A
 * blocking finding the user approved needs nothing more, nor does the check
 * that reports the same thing. */
function checksVerdict(record: CadReturnIngestRecord, checks: CheckDescriptor[], approved: ReadonlySet<string>): { problems: number; notes: number } {
  const settled = new Set(record.findings.filter((finding) => finding.blocking && approved.has(finding.id))
    .map((finding) => FINDING_CHECK[finding.kind]).filter(Boolean));
  const warned = new Set(checks.filter((check) => check.state === 'warn' && !settled.has(check.key)).map((check) => check.key));
  // A blocking finding and the failing check that reports the same thing are
  // one problem, not two.
  const unreported = record.findings.filter((finding) => finding.blocking && !approved.has(finding.id)
    && !warned.has(FINDING_CHECK[finding.kind] ?? '')).length;
  return { problems: warned.size + unreported, notes: record.findings.filter(isNote).length };
}

/** "Checks passed", or one line per check that deviates; every row with its
 * evidence under developer details. Then the findings: those to approve, and
 * those that limit confidence in the result, in words and never by id. */
function Checks({ record, checks, approved }: { record: CadReturnIngestRecord; checks: CheckDescriptor[]; approved: ReadonlySet<string> }) {
  const developerDetails = useDeveloperDetails();
  const toApprove = record.findings.filter((finding) => finding.blocking && !approved.has(finding.id)).length;
  const shown = developerDetails ? checks : checks.filter((check) => check.state !== 'ok');
  const blocking = record.findings.filter((finding) => finding.blocking);
  const notes = record.findings.filter(isNote);
  return <div className="cad-checks">
    {shown.length < checks.length && <div className="cad-check cad-check-ok" title={checks.filter((check) => !shown.includes(check)).map((check) => check.name).join(', ')}>
      <span className="cad-check-glyph" aria-hidden="true">✓</span>
      <b>Checks</b>
      <span className="cad-check-verdict">{shown.length ? 'the others passed' : 'passed'}</span>
    </div>}
    {shown.map((check) => <CheckRow key={check.key} check={check}/>)}
    {blocking.length > 0 && <div className="cad-check-findings">
      <p className="cad-check-findings-head">{toApprove ? `${pluralized(toApprove, 'finding')} to approve` : `${pluralized(blocking.length, 'finding')} approved`}</p>
      {blocking.map((finding) => {
        const done = approved.has(finding.id);
        return <div key={finding.id} className={`cad-check ${done ? 'cad-check-ok' : 'cad-check-warn'}`} data-finding-approved={done ? '' : undefined}>
          <span className="cad-check-glyph" aria-hidden="true">{done ? '✓' : '!'}</span>
          <b>{findingName(finding)}</b>
          <span className="cad-check-verdict">{findingDetail(finding)}{done
            ? <small className="cad-blocking-suffix" title="You approved this for this preparation. The approval is recorded with the run."> · approved</small>
            : <small
              className="cad-blocking-suffix"
              title="WG asks you to approve this before it solves the model. The approval is recorded with the run."
            > · blocking</small>}</span>
          {!findingSentence(finding) && <DeveloperDetails summary="Evidence"><p className="cad-detail">{findingEvidence(finding)}</p></DeveloperDetails>}
        </div>;
      })}
    </div>}
    {notes.length > 0 && <div className="cad-check-notes">
      {notes.map((finding) => <div key={finding.id} className="cad-check cad-check-info">
        <span className="cad-check-glyph" aria-hidden="true">i</span>
        <b>{findingName(finding)}</b>
        <span className="cad-check-verdict">{findingDetail(finding)}</span>
        {!findingSentence(finding) && <DeveloperDetails summary="Evidence"><p className="cad-detail">{findingEvidence(finding)}</p></DeveloperDetails>}
      </div>)}
    </div>}
  </div>;
}

/** FEM regions, when the record has any and this server offers them. */
function FemRegions({ record }: { record: CadReturnIngestRecord }) {
  return record.fem_regions?.length ? <OfferedFemRegions record={record}/> : null;
}

function OfferedFemRegions({ record }: { record: CadReturnIngestRecord }) {
  const { femRegionsOffered } = useCapabilities();
  if (!femRegionsOffered || !record.fem_regions?.length) return null;
  return <section className="cad-fem-regions" aria-label="FEM regions">
    <h5>FEM regions</h5>
    {record.fem_regions.map((region) => <div key={region.region_id}>
      <b>{region.name}</b>
      <p className="cad-detail">{region.stats.tetrahedra.toLocaleString()} tetrahedra · {region.stats.nodes.toLocaleString()} nodes · {region.mesh_size_mm} mm region size</p>
      {Object.entries(region.stats.surface_groups).map(([groupId, stats]) => {
        const group = region.surface_groups.find((candidate) => candidate.group_id === groupId);
        return <div className="cad-row" key={groupId}>
          <b>{group?.name ?? (groupId === '_walls' ? 'Unclaimed walls' : groupId)}</b>
          <span>{stats.kind.replaceAll('-', ' ')} · {stats.triangle_count.toLocaleString()} triangles · {stats.area_mm2.toLocaleString(undefined, { maximumFractionDigits: 2 })} mm²
            {group?.kind === 'moving' && <> · {group.relative_level_db ?? 0} dB relative motion</>}
            {group?.lining && <> · Miki lining: {group.lining.thickness_mm} mm · {group.lining.flow_resistivity_pa_s_per_m2} Pa·s/m²</>}
          </span>
        </div>;
      })}
    </div>)}
  </section>;
}

/** Whether the solver frame shown needs the user: WG suggests another axis
 * the user has not kept the frame against, or the model's own axis cannot be
 * solved. A frame changed elsewhere only informs (Solve already follows it),
 * so its notice waits inside Model details without opening it. */
function useFrameNeedsUser(ingestId: string, unlinked: boolean): boolean {
  return useCadSolverFrameStore((state) => {
    const view = state.frames[ingestId];
    if (!unlinked || !view || view.linked) return false;
    return Boolean(openDiffers(view, state.keptDiffers[ingestId])
      || (!view.picked && view.frame?.notAllowed));
  });
}

/** The domain a record states, in words, without its Change. */
function domainWords(interpretation: DomainInterpretation, decision: ReturnType<typeof recordDomainDecision>): string {
  return (decision ? decisionLine(decision, interpretation) : domainLine(interpretation)).text;
}

export function CadModelDetails({ record, label, prepared, fetcher }: {
  record: CadReturnIngestRecord;
  label: string;
  /** The record on screen is what Solve uses: its domain, axis and settings
   * are shown; a stale or superseded one keeps only what it is. */
  prepared: boolean;
  fetcher?: typeof fetch;
}) {
  const { status } = useCurrentModelStatus(record);
  const latestJob = status.job;
  const latest = latestJob?.cad_state ? cadJobSummary(latestJob) : null;
  const solved = latest?.state === 'accepted';
  const unlinked = record.freshness?.verdict === 'unlinked';
  const interpretation = recordInterpretation(record);
  const decision = recordDomainDecision(record);
  const jobs = useCadJobs();
  const approvedIds = approvedCadFindings(jobs, record.manifest_sha256)?.finding_ids;
  const approvedKey = (approvedIds ?? []).join('|');
  const approved = useMemo(() => new Set(approvedKey ? approvedKey.split('|') : []), [approvedKey]);
  const checks = useMemo(() => recordChecks(record), [record]);
  const memory = meshMemoryBand(record);
  const verdict = checksVerdict(record, checks, approved);
  const problems = verdict.problems + (memory?.attention ? 1 : 0);
  const notes = verdict.notes;
  const sweep = useSweep();
  // The frame gate of a request for this model, which its ladder points here.
  const frameGate = status.kind === 'waiting' && latestJob?.cad_state?.reason === 'frame_confirmation_required';
  const frameNeedsUser = useFrameNeedsUser(record.ingest_id, prepared && unlinked) || (prepared && unlinked && frameGate);
  const domainRefused = prepared && !!decision && decisionLine(decision, interpretation).refused;
  const chip = problems > 0 ? `${problems} ${problems === 1 ? 'needs' : 'need'} attention` : notes > 0 ? pluralized(notes, 'note') : undefined;
  const [shown, setShown] = useState(false);
  return <CadDrawer
    key={record.ingest_id}
    title="Model details"
    chip={chip}
    openWhen={problems > 0 || notes > 0 || frameNeedsUser || domainRefused || (prepared && sweep.broken)}
    onToggle={setShown}
    keepMounted
    warning={problems > 0}
    className="cad-model-details"
  >
    <p className="cad-solve-summary">{modelSummary(record)}</p>
    {/* A record no longer prepared still says how it was read, without the
        Change only a prepared one offers. */}
    {!prepared && interpretation && <p className="cad-domain-line">{domainWords(interpretation, decision)}</p>}
    {prepared && interpretation && <CadDomainInterpretation
      ingestId={record.ingest_id}
      interpretation={interpretation}
      decision={decision}
      fetcher={fetcher}
    />}
    {/* Which way it radiates, and the one Change. After WG solved it along
        its own axis, a pick is confirmed for the project at once: a Solve
        from Fusion would otherwise never carry it. */}
    {prepared && unlinked && <CadSolverFrame
      ingestId={record.ingest_id}
      manifestSha256={record.manifest_sha256}
      label={label}
      confirmOnDone={solved && !!latest?.frameAxisAutomatic}
      visible={shown}
      fetcher={fetcher}
    />}
    <MeshLine record={record}/>
    {prepared && <SettingsLine record={record} sweep={sweep}/>}
    {/* No settings were recorded for this model, so WG solved it with its
        defaults; the backend's own words say so and where to change them. */}
    {prepared && solved && latest?.setupDefaults && <p className="cad-detail cad-solve-defaults" data-setup-defaults="true">
      {defaultSettingsNote(latest?.message ?? null, latestJob?.status === 'complete')}
    </p>}
    <Checks record={record} checks={checks} approved={approved}/>
    <FemRegions record={record}/>
    <DeveloperDetails className="cad-model-provenance">
      <p
        className="cad-detail"
        title="The snapshot is the returned model, named by its manifest hash. The preparation holds the mesh on screen that would be solved."
      >Snapshot <code>{shortSha256(record.manifest_sha256)}</code> · Preparation <code>{record.ingest_id}</code></p>
    </DeveloperDetails>
  </CadDrawer>;
}

/** File input uses the same Model details drawer, check row and developer disclosure. */
export function FileModelDetails({ record, summary, axes, setup }: {
  record: RegisteredMesh | null; summary: MeshSummary | null; axes: ReactNode; setup: unknown;
}) {
  const origins = new Set(summary?.groups.map(group => group.role_from));
  return <CadDrawer title="Model details" className="cad-model-details" keepMounted>
    {axes}
    {record && <CheckRow check={{ key: 'file-checks', name: 'Checks', state: 'ok', verdict: 'passed', title: 'The mesh checks passed.' }}/>}
    <p className="cad-detail">Roles: {origins.has('file') && origins.has('name') ? 'from the file / guessed from names'
      : origins.has('file') ? 'from the file' : origins.has('name') ? 'guessed from names' : 'chosen in Surfaces'}</p>
    {Object.entries(record?.source_file?.transform?.removed_lids ?? {}).map(([plane, lid]) =>
      <p className="cad-detail" key={plane}>Removed the closed face on {planeWords(plane)} · {lid.triangle_count.toLocaleString()} triangles</p>)}
    {summary && <p className="cad-detail">{summary.triangle_count.toLocaleString()} triangles · {summary.node_count.toLocaleString()} points</p>}
    <DeveloperDetails>
      <pre>{JSON.stringify({ groups: summary?.groups, checks: record?.mesh?.integrity, preparation: record?.ingest_id, setup }, null, 2)}</pre>
    </DeveloperDetails>
  </CadDrawer>;
}
