import { useSyncExternalStore, type ReactNode } from 'react';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';
import { useImportedSolvePlan } from '../jobs/useImportedSolvePlan';
import { useCadOperationsStore } from '../stores/cadOperations';
import { useCadReturnStore } from '../stores/cadReturn';
import { parseFrequencyList } from '../stores/frequencyList';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { OnScreenSolveStatus } from './CadOperationsSection';
import { CadDomainInterpretation } from './CadDomainInterpretation';
import { CadSolverFrame } from './CadSolverFrameConfirm';
import { defaultSettingsNote } from './CadSolveInputs';
import type { DomainInterpretation } from '../api/domainInterpretation';
import { recordDomainDecision } from '../api/domainDecision';
import { pluralized } from './cadTime';
import { useOptionalSolveControl } from './JobsCoordinator';
import { clearSolveStageClock, resolveEngineLabel, SolveProgressView } from './solveProgress';
import { workspaceNavigation } from './workspaceNavigation';

/** How long an accepted operation may have no matching job yet before the
 * status line stops assuming "it just hasn't arrived" and says so plainly.
 * A few seconds covers the ordinary gap between the jobs system accepting a
 * submission and its first `JobItem` reaching this browser; past that -- in
 * particular after a page reload finds an operation whose job is not, or is
 * no longer, in the list -- staying on "Solve submitted." forever would be
 * a silent lie. */
const JOB_APPEAR_GRACE_MS = 5_000;

const ROLE_ORDER = ['LF', 'MF', 'HF', 'PORT_EXIT', 'PASSIVE_CARDIOID'];

function rank(role: string): number {
  const index = ROLE_ORDER.indexOf(role);
  return index < 0 ? ROLE_ORDER.length : index;
}

function planePhrase(plane: string): string {
  return plane === 'x0' ? 'x = 0' : plane === 'y0' ? 'y = 0' : plane;
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
    ? `${declared.length === 1 ? 'half' : 'quarter'} model, cut on ${declared.map(planePhrase).join(' and ')}`
    : applied.length
      ? `full model, WG mirrors it at ${applied.map(planePhrase).join(' and ')}`
      : 'full model';
  return [
    bodies.length === 1 ? bodies[0] : bodies.length > 1 ? pluralized(bodies.length, 'body', 'bodies') : null,
    `${pluralized(sourceCount, 'source')}${roles.length ? ` (${roles.join(', ')})` : ''}`,
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

/** The settings Solve uses, in one line: sweep, frequency count, engine and a
 * time estimate. They are edited where they always were, in Simulation. */
function SettingsLine({ record }: { record: CadReturnIngestRecord }) {
  const start = useCadReturnStore((state) => state.frequencyStartHz);
  const end = useCadReturnStore((state) => state.frequencyEndHz);
  const rangeCount = useCadReturnStore((state) => state.frequencyCount);
  const mode = useSolveOptionsStore((state) => state.frequencyMode);
  const listText = useSolveOptionsStore((state) => state.frequencyListText);
  const engine = useSolveOptionsStore((state) => state.engine);
  const plan = useImportedSolvePlan(true).plan;
  const listed = mode === 'list' ? parseFrequencyList(listText).frequencies : null;
  const count = mode === 'list' ? listed?.length ?? 0 : rangeCount;
  const sweep = mode === 'list'
    ? listed ? `${pluralized(listed.length, 'listed frequency', 'listed frequencies')}` : 'frequency list to fix'
    : `${hertz(start)}–${hertz(end)} · ${count} freq`;
  // The engine chosen in the solver selector; AUTO names what it resolves to.
  // A capability verdict's own `label` is preferred when a solve plan is on
  // hand; `resolveEngineLabel` (./solveProgress, shared with the run
  // progress line) is the same fallback either way -- never a bare
  // `.toUpperCase()` of the slug.
  const labelOf = (name: string) => plan?.engines.find((verdict) => verdict.name === name)?.label || resolveEngineLabel(name) || name;
  const requested = engine.trim().toLowerCase();
  const engineWords = requested === 'auto'
    ? `AUTO${plan?.engine ? ` (${labelOf(plan.engine)})` : ''}`
    : labelOf(requested);
  const measured = record.sizing_estimate?.measured;
  const perFrequency = measured && typeof measured === 'object'
    ? (measured as Record<string, unknown>).solve_seconds_per_freq
    : undefined;
  const estimate = typeof perFrequency === 'number' && count > 0 ? duration(perFrequency * count) : null;
  return <p className="cad-solve-settings" data-solve-settings="">
    <span>{[sweep, engineWords, estimate].filter(Boolean).join(' · ')}</span>
    {' '}<button
      className="link-button"
      data-action="open-settings"
      title="Drivers, crossover, sweep, directivity, engine and mesh detail live in the Simulation tab."
      onClick={() => workspaceNavigation.navigate('simulation')}
    >Settings…</button>
  </p>;
}

function newest(operations: CadOperationSummary[]): CadOperationSummary | null {
  return operations.reduce<CadOperationSummary | null>((latest, operation) => (
    !latest || (operation.updatedAt ?? '') > (latest.updatedAt ?? '') ? operation : latest
  ), null);
}

/**
 * The latest `prepare_and_solve` request for this snapshot, at whatever
 * stage it is at -- before its job exists, while it waits on the user, or
 * after it has one. Every state is a candidate, not just the terminal ones:
 * filtering to `accepted`/`rejected`/`cancelled` used to mean a *new*
 * request for the same snapshot, still `received` or `processing`, was
 * invisible to `newest()`, so the previous request's own outcome (often
 * "Solved · its results are in Results.") kept showing under the new one
 * until it too reached one of those three states. Reading every state keeps
 * `newest()` honest about which request is actually the latest one.
 *
 * The one line this renders is one monotonic sequence end to end: the CAD
 * operation's own stage (`operationStageWord`, ./solveProgress) up to
 * `ready`/`submitted`, then the job's own stage (`solveStageWord`, the same
 * module) from the moment it exists -- never a step backward, and never a
 * previous run's outcome shown under a new request.
 */
function RunLine({ record }: { record: CadReturnIngestRecord }) {
  const operations = useCadOperationsStore((state) => state.operations);
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const latest = newest(Object.values(operations).filter((operation) => operation.kind === 'prepare_and_solve'
    && operation.snapshot?.manifestSha256 === record.manifest_sha256));
  if (!latest) return null;
  const latestJob = latest.jobId ? jobs.find((item) => item.id === latest.jobId) : undefined;
  let tone: 'ok' | 'info' | 'warn' = 'info';
  let body: ReactNode;
  if (latest.state === 'rejected') {
    body = `Refused: ${latest.message ?? latest.reason ?? 'no reason given'}`;
    tone = 'warn';
  } else if (latest.state === 'cancelled') {
    body = 'Dismissed before it was solved.';
  } else if (latest.state !== 'accepted') {
    // received, processing, needs_user_input, recovery_required or
    // cancel_requested: no job exists yet (or ever will). The shared
    // component reads the operation's own stage/state, the same vocabulary
    // its job will use once it has one.
    if (latest.state === 'needs_user_input') tone = 'warn';
    body = <SolveProgressView operation={latest} variant="compact"/>;
  } else {
    const job: JobItem | undefined = jobs.find((item) => item.id === latest.jobId);
    switch (job?.status) {
      case 'queued':
        body = <SolveProgressView job={job} variant="compact"/>;
        break;
      case 'running':
        // The same progress component JobsPanel's run cards use, in its
        // compact form -- one design for both CAD Link and parametric mode,
        // fed by nothing but this job's own state (works the same whether
        // this browser pressed Solve or a waiting Fusion request advanced).
        body = <SolveProgressView job={job} variant="compact"/>;
        break;
      case 'complete': clearSolveStageClock(job.id); body = <SolveProgressView job={job} variant="compact"/>; tone = 'ok'; break;
      case 'error': clearSolveStageClock(job.id); body = <SolveProgressView job={job} variant="compact"/>; tone = 'warn'; break;
      case 'cancelled': clearSolveStageClock(job.id); body = <SolveProgressView job={job} variant="compact"/>; tone = 'warn'; break;
      default: {
        // Accepted, but no matching job in the list -- ordinarily because it
        // has not arrived yet. Past a short grace window (in particular
        // after a reload that finds no such job at all) that assumption
        // stops being honest, so the line says so instead of sitting on
        // "Solve submitted." forever.
        const updatedMs = Date.parse(latest.updatedAt ?? '');
        const stale = Number.isFinite(updatedMs) && Date.now() - updatedMs > JOB_APPEAR_GRACE_MS;
        body = stale ? "Accepted, but its run isn't showing in the jobs list."
          : <SolveProgressView operation={latest} variant="compact"/>;
        if (stale) tone = 'warn';
      }
    }
  }
  return <>
    <p className={`cad-solve-run cad-solve-run-${tone}`} role="status" data-run-operation-id={latest.operationId}>{body}</p>
    {/* No settings were recorded for this model, so WG solved it with its
        defaults; the backend's own words say so and where to change them. */}
    {latest.state === 'accepted' && latest.setupDefaults
      && <p className="cad-detail cad-solve-defaults" data-setup-defaults="true">
        {defaultSettingsNote(latest.message, latestJob?.status === 'complete')}
      </p>}
  </>;
}

/**
 * The one Solve card of a CAD model (PLAN.md M1b, with M1e's frame line).
 *
 * What Solve will use, on the card: the model in one line, which way it
 * radiates, the settings; then any request for this very snapshot that waits,
 * and one Solve. That press is the same command as the top bar's: it captures
 * the snapshot, settings, frame and domain on screen, confirms the frame shown,
 * remembers the settings for the model's project, and advances one durable
 * operation -- continuing a waiting Fusion request for this snapshot rather
 * than starting a second. It never pulls a newer snapshot and never approves
 * a finding: those keep their own actions.
 */
export function CadSolveCard({ record, label, fetcher }: {
  record: CadReturnIngestRecord;
  label: string;
  fetcher?: typeof fetch;
}) {
  const solve = useOptionalSolveControl();
  const unlinked = record.freshness?.verdict === 'unlinked';
  // Only the CAD command: this card exists only in CAD Link mode.
  const available = Boolean(solve?.cadMode);
  const disabled = !available || solve!.disabled;
  const interpretation = recordInterpretation(record);
  return <section className="cad-solve-card" aria-label={`Solve ${label}`}>
    <p className="cad-solve-summary">{modelSummary(record)}</p>
    {interpretation && <CadDomainInterpretation
      ingestId={record.ingest_id}
      interpretation={interpretation}
      decision={recordDomainDecision(record)}
      fetcher={fetcher}
    />}
    {unlinked && <CadSolverFrame ingestId={record.ingest_id} manifestSha256={record.manifest_sha256} label={label} fetcher={fetcher}/>}
    <SettingsLine record={record}/>
    <OnScreenSolveStatus record={record}/>
    <RunLine record={record}/>
    <div className="cad-solve-actions">
      <button
        className="primary cad-primary-action"
        data-action="solve"
        disabled={disabled}
        aria-busy={solve?.submitting ?? false}
        aria-label={`Solve: ${label}`}
        title={solve?.title ?? 'Solve is not available here.'}
        onClick={() => solve?.solve()}
      >{solve?.submitting ? 'Solving…' : 'Solve'}</button>
      {/* A disabled button cannot be hovered usefully: the reason is on screen. */}
      {available && solve!.disabled && !solve!.submitting && <span className="cad-detail cad-solve-blocker">{solve!.title}</span>}
    </div>
  </section>;
}
