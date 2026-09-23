import type { JobItem } from '../api/jobsSocket';

/**
 * One place that names a solve's stage for the user, shared by every surface
 * that shows a run in flight -- the Jobs rail card and the CAD Solve card's
 * one-line status alike, so CAD and parametric solves are described the same
 * way (Magnus, 2026-09-23: "same component/design" for both modes).
 *
 * The backend's own stage names (`mesh`, `assemble`, `solve`,
 * `radiation_impedance`, `postprocess`) are bookkeeping, not user-facing
 * words -- `server/jobs/runtime.py::_report_real_stage` maps engine-internal
 * stages onto this small public set. This module maps that set once more,
 * onto the five words a person watching a solve actually wants: Received,
 * Preparing mesh, Solving, Combining, Done/Failed/Cancelled.
 */
export type SolveStageWord =
  | 'Received'
  | 'Preparing mesh'
  | 'Solving'
  | 'Combining'
  | 'Done'
  | 'Failed'
  | 'Cancelled';

const RUNNING_STAGE_WORDS: Record<string, SolveStageWord> = {
  mesh: 'Preparing mesh',
  assemble: 'Solving',
  solve: 'Solving',
  radiation_impedance: 'Solving',
  postprocess: 'Combining',
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

export interface FrequencyProgress {
  /** How many frequencies are already solved (not the one in flight). */
  completed: number;
  total: number;
}

/**
 * Every full-3D engine (`server/solver/beat.py`, `metal.py`,
 * `bempp.py`, `bempp_imported.py`, `beat_imported.py`, `official_beat.py`)
 * reports its own per-frequency stage message as "Solving frequency i/N …"
 * or "Solved frequency i/N …". Reading that text back out is deliberate: it
 * is the one place the frequency count and index already exist together, so
 * adding a parallel numeric field to every engine adapter would duplicate
 * data the message already carries. A message this cannot parse (a batched
 * engine, or a stage other than the main solve) yields no frequency
 * progress, which is the signal to fall back to an indeterminate bar.
 */
export function parseFrequencyProgress(stageMessage: string | null): FrequencyProgress | null {
  if (!stageMessage) return null;
  const match = /(\d+)\s*\/\s*(\d+)/.exec(stageMessage);
  if (!match) return null;
  const index = Number(match[1]);
  const total = Number(match[2]);
  if (!Number.isFinite(index) || !Number.isFinite(total) || total <= 0) return null;
  // "Solving frequency 3/12" means frequency 3 is in flight -- 2 are done.
  // "Solved frequency 3/12" means 3 are already done. Case-insensitive so a
  // future engine's message still parses.
  const alreadySolved = /^solved\b/i.test(stageMessage.trim());
  const completed = Math.max(0, Math.min(total, alreadySolved ? index : index - 1));
  return { completed, total };
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
 * Estimated remaining time, from the average time per frequency solved so
 * far. Requires at least two completed frequencies: one gives no rate to
 * average, and would swing wildly on the very next update.
 */
export function etaSeconds(elapsedSeconds: number, frequency: FrequencyProgress | null): number | null {
  if (!frequency || frequency.completed < 2) return null;
  const remaining = frequency.total - frequency.completed;
  if (remaining <= 0) return 0;
  const perFrequency = elapsedSeconds / frequency.completed;
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

/**
 * The one progress display, fed by nothing but `JobItem` -- the row the jobs
 * websocket already keeps, and what a page reload reads back too. CAD Link
 * mode and parametric mode submit through the same jobs system and get the
 * same `JobItem` back, so this is the one place solve progress renders,
 * never a CAD copy and a parametric copy of the same bar: `JobsPanel`'s run
 * card uses `variant="full"`, and the CAD Solve card's one-line status uses
 * `variant="compact"`. Neither keeps its own memory of what stage a run is
 * on -- both re-derive it from `job` every render, which is what makes a
 * page reload show the right stage without asking the server anything new
 * before the socket resends its snapshot.
 */
export function SolveProgressView({
  job,
  now,
  variant = 'full',
}: {
  job: Pick<JobItem, 'status' | 'stage' | 'stage_message' | 'progress' | 'started_at' | 'queued_at' | 'config_summary' | 'solve_options'>;
  /** `Date.now()`-scale clock driving the live elapsed/ETA readout. Only
   * needed for `variant="full"`, which shows the ETA line. */
  now?: number;
  variant?: 'full' | 'compact';
}) {
  const frequency = isFrequencyStage(job) ? parseFrequencyProgress(job.stage_message) : null;
  const indeterminate = isIndeterminate(job);
  const elapsedSeconds = now !== undefined
    ? Math.max(0, (now - Date.parse(job.started_at ?? job.queued_at ?? '')) / 1000)
    : null;
  const eta = frequency && elapsedSeconds !== null ? etaSeconds(elapsedSeconds, frequency) : null;
  const stageWord = solveStageWord(job);
  const percent = Math.round((job.progress ?? 0) * 100);

  if (variant === 'compact') {
    const detail = frequency ? `frequency ${frequency.completed + 1} of ${frequency.total}` : `${percent}%`;
    return <span className="solve-progress solve-progress-compact">{stageWord}{indeterminate ? '' : ` · ${detail}`}</span>;
  }

  const detailLine = solveDetailLine(job);
  return <div className="solve-progress solve-progress-full">
    <div className="job-stage">
      <span className="job-stage-word">{stageWord}</span>
      <span>{job.stage_message ?? job.stage ?? 'waiting…'}</span>
      {!indeterminate && <b>{percent}%</b>}
    </div>
    {frequency && <p className="job-frequency">frequency {frequency.completed + 1} of {frequency.total}{eta !== null && <> · ETA {formatClock(eta)}</>}</p>}
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

function engineLabel(engine: unknown): string | null {
  const value = typeof engine === 'string' ? engine.trim() : '';
  return value ? value.toUpperCase() : null;
}

/** "Metal · 3 sources · half" -- the engine, what is being solved, and the
 * domain, in one line. Source count comes from a CAD import's drive
 * channels; a parametric design has no such list, so its frequency count
 * stands in for it instead. */
export function solveDetailLine(job: Pick<JobItem, 'config_summary' | 'solve_options'>): string {
  const engine = engineLabel(job.solve_options?.engine);
  const channels = job.config_summary?.drive_channel_ids;
  const sourceCount = Array.isArray(channels) ? channels.length : null;
  const frequencyCount = job.solve_options?.num_frequencies;
  const sources = sourceCount !== null
    ? `${sourceCount} source${sourceCount === 1 ? '' : 's'}`
    : frequencyCount
      ? `${frequencyCount} freq`
      : null;
  const domain = typeof job.solve_options?.symmetry === 'string' && job.solve_options.symmetry
    ? job.solve_options.symmetry
    : null;
  return [engine, sources, domain].filter(Boolean).join(' · ');
}
