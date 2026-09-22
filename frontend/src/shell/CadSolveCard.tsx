import { useSyncExternalStore } from 'react';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';
import { useImportedSolvePlan } from '../jobs/useImportedSolvePlan';
import { useCadOperationsStore } from '../stores/cadOperations';
import { useCadReturnStore } from '../stores/cadReturn';
import { parseFrequencyList } from '../stores/frequencyList';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { OnScreenSolveStatus } from './CadOperationsSection';
import { CadSolverFrame } from './CadSolverFrameConfirm';
import { pluralized } from './cadTime';
import { useOptionalSolveControl } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

const ROLE_ORDER = ['LF', 'MF', 'HF', 'PORT_EXIT', 'PASSIVE_CARDIOID'];

function rank(role: string): number {
  const index = ROLE_ORDER.indexOf(role);
  return index < 0 ? ROLE_ORDER.length : index;
}

function planePhrase(plane: string): string {
  return plane === 'x0' ? 'x = 0' : plane === 'y0' ? 'y = 0' : plane;
}

/** The model in one line: its body, its sources, and which domain is solved. */
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
    domain,
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
  const labelOf = (name: string) => plan?.engines.find((verdict) => verdict.name === name)?.label || name.toUpperCase();
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

/** How the latest finished request for this snapshot ended: its job's progress,
 * failure or cancellation. Acceptance is not success: the job decides. */
function RunLine({ record }: { record: CadReturnIngestRecord }) {
  const operations = useCadOperationsStore((state) => state.operations);
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const latest = newest(Object.values(operations).filter((operation) => operation.kind === 'prepare_and_solve'
    && operation.snapshot?.manifestSha256 === record.manifest_sha256
    && ['accepted', 'rejected', 'cancelled'].includes(operation.state)));
  if (!latest) return null;
  let text: string;
  let tone: 'ok' | 'info' | 'warn' = 'info';
  if (latest.state === 'rejected') {
    text = `Refused: ${latest.message ?? latest.reason ?? 'no reason given'}`;
    tone = 'warn';
  } else if (latest.state === 'cancelled') {
    text = 'Dismissed before it was solved.';
  } else {
    const job: JobItem | undefined = jobs.find((item) => item.id === latest.jobId);
    switch (job?.status) {
      case 'queued': text = 'Solve queued.'; break;
      case 'running': text = `Solving · ${Math.round((job.progress ?? 0) * 100)}%`; break;
      case 'complete': text = 'Solved · its results are in Results.'; tone = 'ok'; break;
      case 'error': text = `Solve failed: ${job.error_message ?? 'no reason given'}`; tone = 'warn'; break;
      case 'cancelled': text = `Solve cancelled${job.error_message ? `: ${job.error_message}` : '.'}`; tone = 'warn'; break;
      default: text = 'Solve submitted.';
    }
  }
  return <p className={`cad-solve-run cad-solve-run-${tone}`} role="status" data-run-operation-id={latest.operationId}>{text}</p>;
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
  return <section className="cad-solve-card" aria-label={`Solve ${label}`}>
    <p className="cad-solve-summary">{modelSummary(record)}</p>
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
