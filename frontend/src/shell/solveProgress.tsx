import type { JobItem } from '../api/jobsSocket';

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
 * user-facing words -- `server/jobs/runtime.py::_report_real_stage` and its
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
  | 'Done'
  | 'Failed'
  | 'Cancelled';

const RUNNING_STAGE_WORDS: Record<string, SolveStageWord> = {
  initializing: 'Starting…',
  mesh: 'Preparing mesh',
  assemble: 'Solving',
  solve: 'Solving',
  radiation_impedance: 'Solving',
  postprocess: 'Combining',
  cancelling: 'Cancelling…',
};

/** The stage a job is in, in the user's words. `queued` is the word the
 * first ~100 ms after Solve needs -- accepted, not yet started. Once the
 * runtime marks a job `running` it is actively working even before its
 * first stage checkpoint lands, so a running job with no stage yet still
 * reads as "Solving", not as a second "Received". */
export function solveStageWord(job: Pick<JobItem, 'status' | 'stage'>): SolveStageWord {
  switch (job.status) {
    case 'complete': return 'Done';
    case 'error': return 'Failed';
    case 'cancelled': return 'Cancelled';
    case 'queued': return 'Received';
    default: return (job.stage && RUNNING_STAGE_WORDS[job.stage]) || 'Solving';
  }
}

/** The fields `RunLine` (CadSolveCard.tsx) needs from a `CadOperationSummary`
 * before its job exists -- deliberately narrow, so this module never has to
 * import the CAD operations API just to read three strings off it. */
export interface OperationProgressLike {
  state: string;
  stage: string | null;
  reason: string | null;
}

/** `server/cadlink/preparation.py`'s own stages, past "received": the
 * snapshot is validated, then meshed, then ready to submit. Mapped onto the
 * same words a job uses once it exists, so `operationStageWord` and
 * `solveStageWord` are one monotonic sequence read end to end: Received
 * (received/validating) -> Preparing mesh (preparing-mesh) -> Solving
 * (ready/submitted, and every job stage after that) -> Combining -> Done. */
const OPERATION_STAGE_WORDS: Record<string, SolveStageWord> = {
  'preparing-mesh': 'Preparing mesh',
  ready: 'Solving',
  submitted: 'Solving',
};

/** Reasons `needs_user_input` names, in the same short phrases
 * `CadOperationsSection.tsx`'s `REASON_COPY` uses -- only the ones that can
 * actually reach this pre-job status line. */
const OPERATION_REASON_WORDS: Record<string, string> = {
  setup_required: 'needs its solve settings',
  findings_need_review: 'blocking findings to review',
  frame_confirmation_required: 'needs its solver frame confirmed',
};

/** The stage word for a CAD operation that has no job yet (or never will --
 * `rejected`/`cancelled` are terminal outcomes a caller usually renders with
 * its own richer text instead of calling this). `accepted` is deliberately
 * absent: once an operation is accepted its status is its job's, read
 * through `solveStageWord`, not this function. */
export function operationStageWord(operation: OperationProgressLike): SolveStageWord {
  switch (operation.state) {
    case 'rejected': return 'Failed';
    case 'cancelled': return 'Cancelled';
    case 'cancel_requested': return 'Cancelling…';
    case 'needs_user_input': return 'Waiting for you';
    case 'recovery_required': return 'Failed';
    default: return (operation.stage && OPERATION_STAGE_WORDS[operation.stage]) || 'Received';
  }
}

/** The short reason line beside `operationStageWord`'s "Waiting for you" --
 * `null` when there is nothing more specific to say than the state itself. */
export function operationWaitingReason(operation: OperationProgressLike): string | null {
  if (operation.state !== 'needs_user_input' || !operation.reason) return null;
  return OPERATION_REASON_WORDS[operation.reason] ?? operation.reason;
}

export interface FrequencyChannel {
  /** 1-based: the drive channel this frequency count is within. */
  index: number;
  count: number;
}

export interface FrequencyProgress {
  /** How many of `total` frequencies are already solved. */
  completed: number;
  total: number;
  /** Present only for an imported multi-channel solve (BEAT/BEMPP imported
   * adapters), whose message also names which drive channel is in flight. */
  channel?: FrequencyChannel;
}

/** `server/solver/beat_imported.py` and `bempp_imported.py` report one
 * channel's frequency count at a time: "Solving frequency i/N of drive
 * channel c/C (id) with …". */
const CHANNEL_PATTERN = /(\d+)\s*\/\s*(\d+)\s*of\s*drive\s*channel\s*(\d+)\s*\/\s*(\d+)/i;
const PLAIN_PATTERN = /(\d+)\s*\/\s*(\d+)/;

/**
 * Every full-3D engine (`server/solver/beat.py`, `metal.py`, `bempp.py`,
 * `bempp_imported.py`, `beat_imported.py`, `official_beat.py`) reports its
 * own per-frequency stage message as "Solving frequency i/N …" or "Solved
 * frequency i/N …". Reading that text back out is deliberate: it is the one
 * place the frequency count and index already exist together, so adding a
 * parallel numeric field to every engine adapter would duplicate data the
 * message already carries.
 *
 * The printed number IS the completed count, for every engine, not one
 * behind it: each adapter's `progress`/`stage_callback` wrapper is invoked
 * from the vendored package's own sweep loop (`hornlab_metal_bem/sweep.py`,
 * `hornlab_beat_bem/sweep.py`, `hornlab_bempp_bem/sweep.py`) only *after*
 * that frequency's result has been appended -- and official BEAT's own
 * "Solved i/N" wording says the same thing directly. A message this cannot
 * parse (a batched engine, or a stage other than the main solve) yields no
 * frequency progress, which is the signal to fall back to an indeterminate
 * bar.
 */
export function parseFrequencyProgress(stageMessage: string | null): FrequencyProgress | null {
  if (!stageMessage) return null;
  const channelMatch = CHANNEL_PATTERN.exec(stageMessage);
  if (channelMatch) {
    const freqDone = Number(channelMatch[1]);
    const freqTotal = Number(channelMatch[2]);
    const channelIndex = Number(channelMatch[3]);
    const channelCount = Number(channelMatch[4]);
    if (
      ![freqDone, freqTotal, channelIndex, channelCount].every(Number.isFinite)
      || freqTotal <= 0 || channelCount <= 0
    ) return null;
    const total = channelCount * freqTotal;
    const completed = Math.max(0, Math.min(total, (channelIndex - 1) * freqTotal + freqDone));
    return { completed, total, channel: { index: channelIndex, count: channelCount } };
  }
  const match = PLAIN_PATTERN.exec(stageMessage);
  if (!match) return null;
  const printed = Number(match[1]);
  const total = Number(match[2]);
  if (!Number.isFinite(printed) || !Number.isFinite(total) || total <= 0) return null;
  return { completed: Math.max(0, Math.min(total, printed)), total };
}

/** Only the main solve stage carries per-frequency detail; every other
 * running stage (mesh, assemble, radiation impedance, combining) has a
 * single scalar `progress` and nothing to parse a frequency count from. */
export function isFrequencyStage(job: Pick<JobItem, 'stage'>): boolean {
  return job.stage === 'solve';
}

/** No frequency could be read from a solve-stage message: a batched engine
 * that reports one lump-sum progress fraction rather than a running count.
 * The bar for that case is indeterminate rather than a percentage that
 * cannot be trusted frequency by frequency. */
export function isIndeterminate(job: Pick<JobItem, 'status' | 'stage' | 'stage_message'>): boolean {
  return job.status === 'running' && isFrequencyStage(job) && parseFrequencyProgress(job.stage_message) === null;
}

/**
 * When each job's solve stage began, so an ETA's rate is measured against
 * the frequency loop alone -- never against meshing or a solver's warm-up,
 * which `job.started_at` includes and a frequency count says nothing about.
 * A page reload starts a fresh clock (the first render after reload reads
 * elapsed as 0 for whatever stage the job is already in), which only delays
 * the first ETA by up to two frequencies' worth of time; it never shows a
 * wrong one, which a clock seeded from `started_at` did.
 */
const stageClocks = new Map<string, { stage: string; startedAt: number }>();

export function resetSolveStageClocksForTests(): void {
  stageClocks.clear();
}

function elapsedInStageSeconds(jobId: string, stage: string | null, now: number): number {
  if (!stage) {
    stageClocks.delete(jobId);
    return 0;
  }
  const existing = stageClocks.get(jobId);
  if (!existing || existing.stage !== stage) {
    stageClocks.set(jobId, { stage, startedAt: now });
    return 0;
  }
  return Math.max(0, (now - existing.startedAt) / 1000);
}

/**
 * Estimated remaining time, from the average time per frequency solved
 * since the solve stage began. Requires at least two completed frequencies:
 * one gives no rate to average, and would swing wildly on the very next
 * update.
 */
export function etaSeconds(elapsedSinceStageStarted: number, frequency: FrequencyProgress | null): number | null {
  if (!frequency || frequency.completed < 2) return null;
  const remaining = frequency.total - frequency.completed;
  if (remaining <= 0) return 0;
  const perFrequency = elapsedSinceStageStarted / frequency.completed;
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
  auto: 'AUTO',
  metal: 'Metal',
  beat: 'BEAT Engine',
  official_beat: 'Official BEAT',
  bempp: 'BEMPP',
  circsym: 'CircSym',
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
 * `server/jobs/runtime.py`'s `_config_summary`/resolution path recorded it,
 * whose `resolved` field is the mode actually used ("half", "quarter", …). */
function resolvedDomain(job: Pick<JobItem, 'config_summary'>): string | null {
  const symmetry = job.config_summary?.symmetry;
  if (symmetry && typeof symmetry === 'object' && !Array.isArray(symmetry)) {
    const resolved = (symmetry as Record<string, unknown>).resolved;
    if (typeof resolved === 'string' && resolved) return resolved;
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
  'id' | 'status' | 'stage' | 'stage_message' | 'progress' | 'started_at' | 'queued_at' | 'config_summary' | 'solve_options'
>;

/** One "frequency i of N[, channel c of C][, ETA m:ss]" line, built from the
 * same parsed `FrequencyProgress` and elapsed-in-stage clock both variants
 * of `SolveProgressView` use, so the wording never drifts between them. */
function frequencyLine(frequency: FrequencyProgress, eta: number | null): string {
  const parts = [`frequency ${frequency.completed} of ${frequency.total}`];
  if (frequency.channel) parts.push(`channel ${frequency.channel.index} of ${frequency.channel.count}`);
  if (eta !== null) parts.push(`ETA ${formatClock(eta)}`);
  return parts.join(' · ');
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
 * job exists alike. Neither keeps its own memory of what stage a run is on
 * -- both re-derive it from `job`/`operation` every render, which is what
 * makes a page reload show the right stage without asking the server
 * anything new before the socket resends its snapshot.
 */
export function SolveProgressView({
  job,
  operation,
  now,
  variant = 'full',
}: {
  job?: JobProgressLike | null;
  /** A CAD operation with no job yet: `variant="compact"` only. */
  operation?: OperationProgressLike | null;
  /** `Date.now()`-scale clock driving the live elapsed/ETA readout. */
  now?: number;
  variant?: 'full' | 'compact';
}) {
  if (!job) {
    if (!operation) return null;
    const stageWord = operationStageWord(operation);
    const reason = operationWaitingReason(operation);
    return <span className="solve-progress solve-progress-compact">
      <span className="job-stage-word">{stageWord}</span>
      {reason && <span className="solve-progress-meta"> · {reason}</span>}
    </span>;
  }

  const frequency = isFrequencyStage(job) ? parseFrequencyProgress(job.stage_message) : null;
  const indeterminate = isIndeterminate(job);
  const elapsedSeconds = now !== undefined
    ? Math.max(0, (now - Date.parse(job.started_at ?? job.queued_at ?? '')) / 1000)
    : null;
  const elapsedInStage = now !== undefined ? elapsedInStageSeconds(job.id, job.stage, now) : 0;
  const eta = frequency ? etaSeconds(elapsedInStage, frequency) : null;
  const stageWord = solveStageWord(job);
  const percent = Math.round((job.progress ?? 0) * 100);

  if (variant === 'compact') {
    const meta = [
      frequency ? frequencyLine(frequency, eta) : indeterminate ? null : `${percent}%`,
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
    {frequency && <p className="job-frequency">{frequencyLine(frequency, eta)}</p>}
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
