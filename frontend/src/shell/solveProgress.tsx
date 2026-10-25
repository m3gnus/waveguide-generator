import { useOptionalSolveControl } from './JobsCoordinator';
import type { SolveReadinessState } from '../jobs/solveReadiness';
import { workspaceNavigation } from './workspaceNavigation';
import { useCallback, useSyncExternalStore } from 'react';
import { isActiveJobStatus, type FrequencyProgress, type JobItem } from '../api/jobsSocket';

let clockNow = Date.now();
let clockTimer: ReturnType<typeof setInterval> | null = null;
const clockListeners = new Set<() => void>();
function subscribeClock(listener: () => void): () => void {
  clockListeners.add(listener);
  clockNow = Date.now();
  listener();
  if (!clockTimer) clockTimer = setInterval(() => {
    clockNow = Date.now();
    clockListeners.forEach((notify) => notify());
  }, 1_000);
  return () => {
    clockListeners.delete(listener);
    if (!clockListeners.size && clockTimer) { clearInterval(clockTimer); clockTimer = null; }
  };
}
export function useSolveClock(active = true): number {
  const subscribe = useCallback((listener: () => void) => active ? subscribeClock(listener) : () => undefined, [active]);
  useSyncExternalStore(subscribe, () => clockNow, () => clockNow);
  return Date.now();
}

/**
 * One place that names a solve's stage for the user, shared by every surface
 * that shows a run in flight -- the Jobs rail card and the CAD Solve card's
 * one-line status alike, so CAD and parametric solves are described the same
 * way (Magnus, 2026-09-23: "same component/design" for both modes; the
 * pre-job CAD operation pipeline joins the same vocabulary too, so the
 * handoff from "a CAD operation is being prepared" to "its job is solving"
 * never steps backward or reads two different ways).
 *
 * The backend's own stage names (`initializing`, `mesh`, `assemble`, `solve`,
 * `radiation_impedance`, `postprocess`, `cancelling`) are bookkeeping, not
 * user-facing words -- `server/jobs/runtime/core.py::_report_real_stage` and its
 * neighbours map engine-internal stages onto this small public set. This
 * module maps that set once more, onto the words a person watching a solve
 * actually wants.
 */
export type SolveStageWord =
  | 'Received'
  | 'Preparing mesh'
  | 'Starting…'
  | 'Solving'
  | 'Combining'
  | 'Cancelling…'
  | 'Waiting for you'
  | 'Ready to solve'
  | 'Needs a change'
  | 'Not prepared'
  | 'Needs recovery'
  | 'Done'
  | 'Failed'
  | 'Cancelled';

const RUNNING_STAGE_WORDS: Record<string, SolveStageWord> = {
  initializing: 'Preparing mesh',
  mesh: 'Preparing mesh',
  assemble: 'Starting…',
  solve: 'Solving',
  radiation_impedance: 'Solving',
  postprocess: 'Combining',
  cancelling: 'Cancelling…',
};

/** The stage a job is in, in the user's words. Real adapters emit
 * initializing -> mesh -> setup -> solve; dry run starts at mesh. */
export function solveStageWord(job: Pick<JobItem, 'status' | 'stage'> & Partial<Pick<JobItem, 'cad_state'>>): SolveStageWord {
  switch (job.status) {
    case 'complete': return 'Done';
    // What a kept request asks (`operations.reason_attention`): only a real
    // question waits for the user; a prepared one is ready; any other names
    // something to change. A hold with no class predates them: a question.
    case 'held': return job.cad_state?.attention === 'ready' ? 'Ready to solve'
      : job.cad_state?.attention === 'action' ? 'Needs a change' : 'Waiting for you';
    case 'error': return 'Failed';
    case 'cancelled': return 'Cancelled';
    case 'preparing':
    case 'queued': return 'Preparing mesh';
    default: return (job.stage && RUNNING_STAGE_WORDS[job.stage]) || 'Preparing mesh';
  }
}

export const MODEL_STATUS_TITLE = {
  solved: 'Solved', running: 'Solving', preparing: 'Preparing', waiting: 'Waiting for you', ready: 'Ready to solve', change: 'Needs a change', refused: 'Solve refused',
  failed: 'Solve failed', 'prepare-failed': 'Not prepared', 'never-solved': 'Not solved yet',
};

export function runStatusWord(job: Pick<JobItem, 'status' | 'stage'> & Partial<Pick<JobItem, 'cad_state'>>): SolveStageWord {
  return solveStageWord(job);
}

/** The fields `RunLine` (CadSolveCard.tsx) needs from a `CadOperationSummary`
 * before its job exists -- deliberately narrow, so this module never has to
 * import the CAD operations API just to read three strings off it. */
export interface OperationProgressLike {
  state: string;
  stage: string | null;
  reason: string | null;
  /** What it asks of the user (`operations.reason_attention`); a kept
   * operation is "Waiting for you" only for a real question, and "Ready to
   * solve" when it is prepared. */
  attention?: 'question' | 'ready' | 'action' | 'failed' | null;
}

/** `server/cadlink/joblane/preparation.py`'s own stages, past "received": the
 * snapshot is validated, then meshed, then ready to submit. Mapped onto the
 * same words a job uses once it exists, so `operationStageWord` and
 * `solveStageWord` are one monotonic sequence read end to end: Received
 * (received/validating) -> Preparing mesh (preparing-mesh, ready/submitted,
 * queued, initializing, mesh) -> Starting (setup/assemble) -> Solving ->
 * Combining -> Done. */
const OPERATION_STAGE_WORDS: Record<string, SolveStageWord> = {
  'preparing-mesh': 'Preparing mesh',
  ready: 'Preparing mesh',
  submitted: 'Preparing mesh',
};

/** Short reasons shared by CAD request guidance and solve progress. */
export const OPERATION_REASON_WORDS: Record<string, string> = {
  preparation_failed: 'preparation failed',
  interrupted: 'interrupted',
  mesh_too_large: 'larger than WG\'s limit',
  solver_memory_exceeded: 'too large for this machine\'s memory',
  snapshot_invalid: 'preparation failed',
  snapshot_changed: 'changed after Fusion asked',
  return_unavailable: 'its return cannot be read',
  wglink_folder_unset: 'no WGLink folder selected',
  excitation_refused: 'its drive cannot be solved as set',
  model_cut_unsolvable: 'arrived cut open',
  setup_required: 'needs its solve settings',
  findings_need_review: 'blocking findings to review',
  frame_confirmation_required: 'its solver frame changed; press Solve again',
  reduced_axis_not_allowed: 'its cut cannot be solved along its axis',
  update_restart_pending: 'held for the update restart',
  ready_to_solve: 'ready to solve',
  engine_unavailable: 'the selected engine cannot solve it',
  engine_cannot_solve_return: 'the selected engine cannot solve this return',
  submission_refused: 'the jobs system refused it',
  return_unsupported: 'needs a newer Waveguide Generator',
  disk_full: 'the disk is full',
  model_needs_change: 'needs a change in Fusion',
  snapshot_unavailable: 'its return could not be read',
  preflight_blocked: 'Fusion refused it',
  request_payload_invalid: 'the request could not be read',
  baseline_conflict: 'the Fusion document changed',
  target_not_exact: 'the linked instance is ambiguous',
  superseded: 'replaced by a newer request',
  expired: 'expired',
  session_changed: 'Fusion restarted',
  publication_failed: 'could not be published',
  adapter_refused: 'Fusion refused it',
  adapter_failed: 'Fusion could not apply it',
  adapter_not_started: 'Fusion never started it',
};

/** A reason in a few plain words; never the raw code (every code the server
 * can store has words above; `server/tests/test_cad_held.py` checks). */
export function operationReasonWords(reason: string | null | undefined): string {
  return (reason && OPERATION_REASON_WORDS[reason]) || 'it needs your attention';
}

/** The stage word for a CAD operation that has no job yet (or never will --
 * `rejected`/`cancelled` are terminal outcomes). An accepted operation uses
 * its submitted stage only until its job appears. */
export function operationStageWord(operation: OperationProgressLike): SolveStageWord {
  switch (operation.state) {
    case 'rejected': return 'Failed';
    case 'cancelled': return 'Cancelled';
    case 'cancel_requested': return 'Cancelling…';
    case 'needs_user_input':
      // Only a real question waits for the user; a restart hold is queued again by itself.
      if (operation.attention === 'question') return 'Waiting for you';
      if (operation.attention === 'ready') return 'Ready to solve';
      return operation.reason === 'update_restart_pending' ? 'Received' : 'Not prepared';
    case 'recovery_required': return 'Needs recovery';
    default: return (operation.stage && OPERATION_STAGE_WORDS[operation.stage]) || 'Received';
  }
}

/** The short reason line beside `operationStageWord`'s "Waiting for you" --
 * `null` when there is nothing more specific to say than the state itself. */
export function operationWaitingReason(operation: OperationProgressLike): string | null {
  if (operation.state !== 'needs_user_input' || !operation.reason) return null;
  return operationReasonWords(operation.reason);
}

/** A running solve's frequency count, as the server reads it from the
 * engine (`server/jobs/progress.py`, the one owner): `done` of `total`, and
 * for an imported multi-channel solve the channel the count is within. The
 * server keeps it through a solve's setup chatter; null means the engine
 * reports no count, which shows as an indeterminate bar. */
function frequencyOf(job: Pick<JobItem, 'status'> & Partial<Pick<JobItem, 'frequency_progress'>>): FrequencyProgress | null {
  return job.status === 'running' ? job.frequency_progress ?? null : null;
}

/**
 * When each job's solve stage began, so an ETA's rate is measured against
 * the frequency loop alone -- never against meshing or a solver's warm-up,
 * which `job.started_at` includes and a frequency count says nothing about.
 * A page reload starts a fresh clock and remembers the completed count then.
 * An ETA needs a later frequency checkpoint to establish a rate.
 */
const stageClocks = new Map<string, { startedAt: number; completedAtStart: number }>();
const observedStageWords = new Map<string, SolveStageWord>();

const STAGE_WORD_RANK: Partial<Record<SolveStageWord, number>> = {
  'Received': -1,
  'Preparing mesh': 0,
  'Starting…': 1,
  'Solving': 2,
  'Combining': 3,
  'Cancelling…': 4,
};

/**
 * Worker logs can report `setup` between BEAT sweep checkpoints. Keep the
 * highest user-facing stage for this job so a transient setup line cannot
 * rewind the shared CAD/parametric display. (The server keeps the frequency
 * count through the same chatter.)
 */
function stageWordObservedForJob(job: JobProgressLike): SolveStageWord {
  const stageWord = solveStageWord(job);
  if (job.status === 'complete' || job.status === 'error' || job.status === 'cancelled' || job.status === 'held') {
    stageClocks.delete(job.id);
    observedStageWords.delete(job.id);
    return stageWord;
  }
  const previous = observedStageWords.get(job.id);
  const previousRank = previous ? STAGE_WORD_RANK[previous] ?? -Infinity : -Infinity;
  const currentRank = STAGE_WORD_RANK[stageWord] ?? -Infinity;
  const forward = currentRank < previousRank ? previous! : stageWord;
  observedStageWords.set(job.id, forward);
  return forward;
}

export function resetSolveStageClocksForTests(): void {
  stageClocks.clear();
  observedStageWords.clear();
  clockNow = Date.now();
}

export function clearSolveStageClock(jobId: string): void {
  stageClocks.delete(jobId);
  observedStageWords.delete(jobId);
}

function stageSample(job: JobProgressLike, stageWord: SolveStageWord, frequency: FrequencyProgress | null, now: number): { elapsed: number; gained: number } {
  if (job.status === 'complete' || job.status === 'error' || job.status === 'cancelled' || job.status === 'held') {
    stageClocks.delete(job.id);
    return { elapsed: 0, gained: 0 };
  }
  const existing = stageClocks.get(job.id);
  // A non-solve message after the sweep began is transient (BEAT emits setup
  // callbacks between groups). Keep the original clock and its baseline.
  if (stageWord !== 'Solving' || !frequency) {
    return existing
      ? { elapsed: Math.max(0, (now - existing.startedAt) / 1000), gained: 0 }
      : { elapsed: 0, gained: 0 };
  }
  const jobId = job.id;
  // The runtime reserves 35–85% for the frequency sweep. Its normalized
  // value counts every axial tag group, even when the printed i/N restarts.
  const completed = frequency.channel && Number.isFinite(job.progress)
    ? Math.max(0, Math.min(1, (job.progress - 0.35) / 0.5))
    : frequency.done;
  const clock = stageClocks.get(jobId);
  if (!clock) {
    stageClocks.set(jobId, { startedAt: now, completedAtStart: completed });
    return { elapsed: 0, gained: 0 };
  }
  return { elapsed: Math.max(0, (now - clock.startedAt) / 1000), gained: Math.max(0, completed - clock.completedAtStart) };
}

/**
 * Estimated remaining time from work completed since this browser first saw
 * the solve stage. One later checkpoint establishes an initial rate.
 */
export function etaSeconds(elapsedSinceStageStarted: number, frequency: Pick<FrequencyProgress, 'done' | 'total'> | null, gained = frequency?.done ?? 0): number | null {
  if (!frequency || gained <= 0) return null;
  const remaining = frequency.total - frequency.done;
  if (remaining <= 0) return 0;
  const perFrequency = elapsedSinceStageStarted / gained;
  return perFrequency * remaining;
}

/** Always mm:ss, unlike the run list's compact "12 s" / "1:02" -- the live
 * progress readout is a clock, and a clock does not change its own format
 * partway through counting. */
export function formatClock(seconds: number): string {
  const clamped = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(clamped / 60);
  const rest = clamped % 60;
  return `${minutes}:${rest.toString().padStart(2, '0')}`;
}

/** Display names for the engine slugs `solve_options.engine` carries.
 * `CadSolveCard.tsx`'s settings line prefers a capability verdict's own
 * `label` when a solve plan is on hand (CAD Link mode, before a job exists);
 * this is what both that line and this module fall back to otherwise --
 * one name per engine, never a bare `.toUpperCase()` of the slug. */
const ENGINE_LABELS: Record<string, string> = {
  auto: 'Auto',
  metal: 'Metal-BEM',
  beat: 'BEAT Engine',
  'beat-cpu': 'BEAT CPU',
  'beat-metal': 'BEAT Metal',
  'beat-cuda': 'BEAT CUDA',
  'beat-rocm': 'BEAT ROCm',
  official_beat: 'Official BEAT',
  bempp: 'BEMPP',
  circsym: 'Axisymmetric (removed)',
  axisym: 'Axisymmetric (removed)',
  dryrun: 'Dry run',
};

export function resolveEngineLabel(engine: string | null | undefined): string | null {
  const value = typeof engine === 'string' ? engine.trim() : '';
  if (!value) return null;
  const known = ENGINE_LABELS[value.toLowerCase()];
  if (known) return known;
  return value.split(/[_-]+/).filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ');
}

/** The domain a job actually solved, not the one it was asked for:
 * `solve_options.symmetry` is the request ("auto" more often than not);
 * `config_summary.symmetry` is `symmetry_metadata` as
 * `server/jobs/runtime/`'s `_config_summary`/resolution path recorded it,
 * whose `resolved` field is the mode actually used ("half", "quarter", …). */
function resolvedDomain(job: Pick<JobItem, 'config_summary'>): string | null {
  const symmetry = job.config_summary?.symmetry;
  if (symmetry && typeof symmetry === 'object' && !Array.isArray(symmetry)) {
    const value = symmetry as Record<string, unknown>;
    if (value.domain === 'continuous-axisymmetric') return 'Axisymmetric (removed)';
    const resolved = value.resolved;
    if (typeof resolved === 'string') {
      if (resolved === 'full' || resolved === 'full_3d') return 'full';
      if (resolved === 'quarter') return 'quarter';
      if (resolved === 'half' || resolved === 'half_xz' || resolved === 'half_yz') return 'half';
    }
    const quadrants = value.resolved_quadrants;
    if (quadrants === 1234) return 'full';
    if (quadrants === 12 || quadrants === 14) return 'half';
    if (quadrants === 1) return 'quarter';
  }
  return null;
}

/** "Metal · 3 sources · half" -- the engine, what is being solved, and the
 * domain actually solved, in one line. Source count comes from a CAD
 * import's drive channels; a parametric design has no such list, so its
 * frequency count stands in for it instead. */
export function solveDetailLine(job: Pick<JobItem, 'config_summary' | 'solve_options'>): string {
  const engine = resolveEngineLabel(job.solve_options?.engine);
  const channels = job.config_summary?.drive_channel_ids;
  const sourceCount = Array.isArray(channels) ? channels.length : null;
  const frequencyCount = job.solve_options?.num_frequencies;
  const sources = sourceCount !== null
    ? `${sourceCount} source${sourceCount === 1 ? '' : 's'}`
    : frequencyCount
      ? `${frequencyCount} freq`
      : null;
  const domain = resolvedDomain(job);
  return [engine, sources, domain].filter(Boolean).join(' · ');
}

type JobProgressLike = Pick<
  JobItem,
  'id' | 'status' | 'stage' | 'stage_message' | 'progress' | 'started_at' | 'queued_at' | 'config_summary' | 'solve_options' | 'error_message'
> & Partial<Pick<JobItem, 'frequency_progress'>>;

/** One "frequency i of N[, channel c of C][, ETA m:ss]" line, built from the
 * same server-read `FrequencyProgress` and elapsed-in-stage clock both variants
 * of `SolveProgressView` use, so the wording never drifts between them. */
function frequencyLine(frequency: FrequencyProgress, eta: number | null, overall: number | null): string {
  const parts = [`frequency ${frequency.done} of ${frequency.total}`];
  if (frequency.channel) parts.push(`channel ${frequency.channel.index} of ${frequency.channel.count}`);
  if (overall !== null) parts.push(`work ${Math.round(overall * 100)}%`);
  if (eta !== null) parts.push(`ETA ${formatClock(eta)}`);
  return parts.join(' · ');
}

/** Readiness is a pre-job stage of this same progress view. */
function useReadinessProgress(enabled: boolean, readiness?: SolveReadinessState | null, held = false) {
  const control = useOptionalSolveControl();
  if (!enabled) return null;
  const blocker = readiness === undefined ? control?.readiness : readiness;
  const visible = held || control?.readinessRequested;
  const waiting = control?.waitingForEngine === true;
  const warmingEngine = control?.waitingEngine && control.waitingEngine !== 'auto' ? control.waitingEngine : blocker?.engine ?? control?.waitingEngine;
  const message = waiting ? `${resolveEngineLabel(warmingEngine) || warmingEngine} is warming up. The solve starts when it is ready.` : visible ? blocker?.message ?? (control?.disabled ? control.title : null) : null;
  if (!message && !control?.actionError && !control?.preparationError) return null;
  const errors = [...new Set([control?.actionError, control?.preparationError])]
    .filter((error): error is string => !!error);
  // A contextual action error may already contain the blocker's diagnostic.
  const messageInError = !!message && errors.some(error => error.endsWith(message));
  const order = (remedy: NonNullable<SolveReadinessState['remedies']>[number]) =>
    remedy.kind === 'use_engine' ? remedy.same_solver ? 1 : 2 : remedy.kind === 'settings' ? 3 : 0;
  const remedies = [...(visible && !waiting ? blocker?.remedies ?? [] : [])].sort((a, b) => order(a) - order(b));
  return <div className="solve-progress solve-readiness" role="status" aria-live="polite">
    {message && !messageInError && <span>{message}</span>}
    {waiting && <button type="button" className="btn" onClick={() => control?.stopWaiting()}>Stop</button>}
    {remedies.map((remedy, index) => {
      switch (remedy.kind) {
        case 'prepare': {
          const label = resolveEngineLabel(remedy.engine) || remedy.engine;
          if (remedy.disabled_reason) return message?.includes(remedy.disabled_reason) ? null : <span key={index}>{remedy.disabled_reason}</span>;
          return <button type="button" className="btn" key={index} disabled={!control || !!control.preparingEngine}
            onClick={() => { void control?.prepareAndSolve(remedy.engine); }}>
            {control?.preparingEngine === remedy.engine ? `Preparing ${label}…` : `Prepare ${label} and solve`}
          </button>;
        }
        case 'use_engine': {
          const label = resolveEngineLabel(remedy.engine) || remedy.engine;
          return <button type="button" className="btn" key={index} disabled={!control} onClick={() => control?.useEngine(remedy.engine)}>
            Use {label}
          </button>;
        }
        case 'wait': return null;
        case 'settings': return <button type="button" className="btn" key={index} onClick={() => workspaceNavigation.navigate('simulation')}>Open settings</button>;
      }
    })}
    {remedies.some(remedy => remedy.kind === 'use_engine' && !remedy.same_solver)
      && <span className="solve-readiness-note">Another solver’s results will differ.</span>}
    {errors.map(error => <span key={error} role="alert">{error}</span>)}
  </div>;
}

/**
 * The one progress display, fed by nothing but a `JobItem` (or, before a
 * CAD-submitted job exists, a `CadOperationSummary`'s own state) -- the row
 * the jobs websocket already keeps and replays, and what a page reload reads
 * back too. CAD Link mode and parametric mode submit through the same jobs
 * system and get the same `JobItem` back, so this is the one place solve
 * progress renders, never a CAD copy and a parametric copy of the same bar:
 * `JobsPanel`'s run card uses `variant="full"`, and the CAD Solve card's
 * one-line status uses `variant="compact"`, before and after its operation's
 * job exists alike. Both job views share the same per-job stage observer and
 * the server's own frequency count (`job.frequency_progress`), so a noisy
 * worker callback cannot rewind one surface or reset its ETA. A page reload
 * starts from the job snapshot the socket resends.
 */
export function SolveProgressView({
  job,
  operation,
  showReadiness = false,
  readiness,
  held = false,
  now,
  variant = 'full',
}: {
  job?: JobProgressLike | null;
  /** Only the Results solve status owns pre-job readiness and remedies. */
  showReadiness?: boolean;
  readiness?: SolveReadinessState | null;
  held?: boolean;
  /** A CAD operation with no job yet: `variant="compact"` only. */
  operation?: OperationProgressLike | null;
  /** `Date.now()`-scale clock driving the live elapsed/ETA readout. */
  now?: number;
  variant?: 'full' | 'compact';
}) {
  const tick = useSolveClock(Boolean(job && isActiveJobStatus(job.status)));
  const time = now ?? tick;
  const pending = useReadinessProgress(showReadiness, readiness, held);
  if (pending) return pending;
  if (!job) {
    if (!operation) return null;
    const stageWord = operationStageWord(operation);
    const reason = operationWaitingReason(operation);
    return <span className="solve-progress solve-progress-compact">
      <span className="job-stage-word">{stageWord}</span>
      {reason && <span className="solve-progress-meta"> · {reason}</span>}
    </span>;
  }

  const stageWord = stageWordObservedForJob(job);
  const frequency = stageWord === 'Solving' ? frequencyOf(job) : null;
  const indeterminate = job.status === 'running' && stageWord === 'Solving' && frequency === null;
  const elapsedSeconds = Math.max(0, (time - Date.parse(job.started_at ?? job.queued_at ?? '')) / 1000);
  const sample = stageSample(job, stageWord, frequency, time);
  const normalized = frequency?.channel && Number.isFinite(job.progress)
    ? Math.max(0, Math.min(1, (job.progress - 0.35) / 0.5)) : null;
  const eta = frequency ? etaSeconds(sample.elapsed, normalized === null ? frequency : { done: normalized, total: 1 }, sample.gained) : null;
  const percent = Math.round((job.progress ?? 0) * 100);

  if (variant === 'compact') {
    if (!isActiveJobStatus(job.status)) return null;
    const meta = [
      frequency ? frequencyLine(frequency, eta, normalized) : indeterminate ? null : `${percent}%`,
      elapsedSeconds !== null ? formatClock(elapsedSeconds) : null,
      solveDetailLine(job) || null,
    ].filter(Boolean).join(' · ');
    return <span className="solve-progress solve-progress-compact">
      <span className="job-stage-word">{stageWord}</span>
      {meta && <span className="solve-progress-meta"> · {meta}</span>}
    </span>;
  }

  const detailLine = solveDetailLine(job);
  return <div className="solve-progress solve-progress-full">
    <div className="job-stage">
      <span className="job-stage-word">{stageWord}</span>
      <span>{job.stage_message ?? job.stage ?? 'waiting…'}</span>
      {!indeterminate && <b>{percent}%</b>}
    </div>
    {frequency && <p className="job-frequency">{frequencyLine(frequency, eta, normalized)}</p>}
    <div
      className={`progress${indeterminate ? ' indeterminate' : ''}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={indeterminate ? undefined : 100}
      aria-valuenow={indeterminate ? undefined : percent}
      aria-valuetext={indeterminate
        ? `${job.stage_message ?? job.stage ?? 'working'} -- progress not reported per frequency`
        : `${percent}% -- ${job.stage_message ?? job.stage ?? 'waiting'}`}
    ><i style={indeterminate ? undefined : { width: `${Math.max(0, Math.min(100, job.progress * 100))}%` }}/></div>
    {detailLine && <p className="job-detail">{detailLine}</p>}
  </div>;
}
