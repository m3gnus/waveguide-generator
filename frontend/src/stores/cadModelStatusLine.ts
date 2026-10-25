import type { JobItem } from '../api/jobsSocket';
import { isFailedCadPreparation, isHeldCadJob } from '../jobs/cadSolve';
import { relativeTime } from '../shell/cadTime';
import { OPERATION_REASON_WORDS } from '../shell/solveProgress';
import type { CadModelRequest, selectCadModelState } from './cadModelState';

/**
 * What became of a CAD request, in the classes Fusion reads from WG's
 * request-status file. It reads the same job fields as
 * `server/cadlink/joblane/request_status.py::project_request_status` (the
 * job's status, then `cad_state.held`), so WG and Fusion cannot disagree
 * about a request. Both sides run the same table of states:
 * `server/tests/fixtures/cad_request_states.json`.
 */
export type CadRequestState = 'preparing' | 'queued' | 'solving' | 'solved' | 'cancelled' | 'needsYou' | 'couldNotFinish';

export function cadRequestState(job: Pick<JobItem, 'status' | 'cad_state'>): CadRequestState {
  switch (job.status) {
    case 'preparing': return 'preparing';
    case 'queued': return 'queued';
    case 'running': return 'solving';
    case 'complete': return 'solved';
    case 'cancelled': return 'cancelled';
    default: return isHeldCadJob(job) && !isFailedCadPreparation(job) ? 'needsYou' : 'couldNotFinish';
  }
}

/** The one action a status line offers. `prepare` stands until preparation is
 * automatic (it is the only Prepare left on screen); the rest are the plan's. */
export type CadStatusAction = 'solve' | 'choose' | 'try-again' | 'show-results' | 'prepare' | 'none';

export type CadStatusTone = 'idle' | 'busy' | 'ready' | 'attention' | 'done';

export interface CadModelStatusLine {
  /** The sentence the model card shows, once. */
  text: string;
  /** A few words for the status bar ("ready · 3,035 triangles"). */
  short: string;
  tone: CadStatusTone;
  action: CadStatusAction;
  /** The action's button label, or null for `none`. */
  actionLabel: string | null;
  /** The request a choose / try-again acts on; null for the browser's own preparation. */
  jobId: string | null;
}

export interface CadStatusContext {
  /** Results are on screen: a solved model needs no Show results. */
  resultsShown?: boolean;
  /** Solve waits for its engine (`SolveControl.waitingForEngine`). */
  waitingForEngine?: boolean;
  /** Where models come from when none is selected. */
  application?: string;
}

type CadModelView = Pick<ReturnType<typeof selectCadModelState>,
  'selected' | 'label' | 'phase' | 'preparation' | 'heldRequest' | 'meshChoice' | 'failureMessage' | 'triangles' | 'canPrepare' | 'record'>;

const ACTION_LABEL: Record<Exclude<CadStatusAction, 'none'>, string> = {
  solve: 'Solve', choose: 'Choose…', 'try-again': 'Try again', 'show-results': 'Show results', prepare: 'Prepare',
};

const ENGINE_HOLDS = new Set(['engine_unavailable', 'engine_cannot_solve_return']);

function line(text: string, short: string, tone: CadStatusTone, action: CadStatusAction = 'none', jobId: string | null = null,
  actionLabel: string | null = action === 'none' ? null : ACTION_LABEL[action]): CadModelStatusLine {
  return { text, short, tone, action, actionLabel, jobId };
}

/** How far a solve got, from the server's own count (`job.frequency_progress`)
 * when the job carries it; otherwise its overall fraction. */
function solving(job: JobItem): CadModelStatusLine {
  const frequency = job.frequency_progress;
  if (frequency && frequency.total > 0) {
    return line(`Solving · ${frequency.done} of ${frequency.total} frequencies`, `solving ${frequency.done}/${frequency.total}`, 'busy');
  }
  const percent = Math.round((job.progress ?? 0) * 100);
  return percent > 0 ? line(`Solving · ${percent}%`, `solving ${percent}%`, 'busy') : line('Solving…', 'solving…', 'busy');
}

/**
 * The selected CAD model's status, in one sentence with at most one action.
 * Every surface that states the model's state reads this: the model card
 * shows it once; the status bar uses `short`; the viewport says it when it has
 * nothing to draw; Results states it without the action.
 *
 * `request` is `currentModelStatus` for the model on screen, the same request
 * `selectCadModelState` was given.
 */
export function cadModelStatusLine(model: CadModelView, request: CadModelRequest | null, context: CadStatusContext = {}): CadModelStatusLine {
  const { label } = model;
  if (!model.selected) {
    return line(`No CAD model yet. In ${context.application ?? 'Fusion'}, use Waveguide Generator ▸ Send or Solve.`, 'none selected', 'idle');
  }
  const job = request?.job ?? null;
  const requestState = job ? cadRequestState(job) : null;
  const state = job?.cad_state ?? null;
  const prepared = model.preparation === 'prepared';
  // A question for the user: the held request's pop-up, or the mesh-size
  // choice asked in this window. Choose… opens it.
  if (model.phase === 'waiting') {
    const reason = model.heldRequest?.cad_state?.reason ?? (model.meshChoice ? 'mesh_too_large' : null);
    const why = reason === 'mesh_too_large' ? `: it is ${OPERATION_REASON_WORDS.mesh_too_large}.` : ' before it can be solved.';
    return line(`${label} needs your choice${why}`, 'needs your choice', 'attention', 'choose', model.heldRequest?.id ?? null);
  }
  // A refusal the request still carries (an older row can keep its job
  // preparing): its words, before any stage it seems to be in.
  if (request?.kind === 'refused' && job && model.phase !== 'preparing') {
    return line(request.message || `WG could not solve ${label}.`, 'not solved', 'attention', prepared ? 'solve' : 'none', job.id);
  }
  // WG prepares the model: in this window, or on the server for a request.
  // Either outranks a stale or missing mesh, which it is replacing.
  if (model.phase === 'preparing' || (requestState === 'preparing' && !prepared)) {
    return line(`Preparing ${label}…`, 'preparing…', 'busy');
  }
  if (requestState === 'preparing') return line('Starting the solve…', 'starting…', 'busy');
  if (requestState === 'queued') return line(`${label} is queued to solve.`, 'queued', 'busy');
  if (requestState === 'solving' && job) return solving(job);
  // The engine gates: Results owns their details and remedies.
  if (context.waitingForEngine || (request?.kind === 'waiting' && state?.reason && ENGINE_HOLDS.has(state.reason))) {
    return state?.reason === 'engine_cannot_solve_return'
      ? line(`The selected engine cannot solve ${label}. Results says what to change.`, 'engine cannot solve it', 'attention')
      : line('Solve engine is getting ready…', 'engine getting ready…', 'busy');
  }
  // WG's own failed preparation, in the server's calm words, with Try again
  // where the server offers it.
  if (request?.kind === 'prepare-failed' && job) {
    const retry = state?.options?.some(option => option.id === 'retry');
    const text = model.failureMessage || request.message || `WG could not prepare ${label}.`;
    return line(text, 'could not prepare', 'attention', retry ? 'try-again' : 'none', job.id);
  }
  if (model.preparation === 'prepare-failed') {
    return line(model.failureMessage || `WG could not prepare ${label}.`, 'could not prepare', 'attention',
      model.canPrepare ? 'try-again' : 'none');
  }
  // Its mesh no longer matches the settings or the return: what was solved
  // or prepared before is not what Solve would use now.
  if (model.preparation === 'stale') {
    return line(`${label} changed since it was prepared.`, 'needs preparing again', 'attention',
      model.canPrepare ? 'prepare' : 'none', null, 'Prepare again');
  }
  if (requestState === 'solved' && request?.kind === 'solved' && job) {
    const run = job.run_number ? ` · run #${job.run_number}` : '';
    const when = job.completed_at ? ` · ${relativeTime(job.completed_at)}` : '';
    return line(`Solved${run}${when}`, 'solved', 'done', context.resultsShown ? 'none' : 'show-results', job.id);
  }
  if (request?.kind === 'failed' && job) {
    return line(`WG could not finish solving ${label}.`, 'could not finish', 'attention', 'try-again', job.id);
  }
  // A prepared request kept until Solve, or one that names a change (a frame
  // changed elsewhere): Solve solves what is on screen.
  if (request?.kind === 'waiting' && job && prepared) {
    if (state?.attention === 'ready') return ready(model);
    return line(request.message || `${label} needs a change before it can be solved.`, 'needs a change', 'attention', 'solve', job.id);
  }
  if (model.phase === 'stopped') {
    return line(model.failureMessage || `WG could not solve ${label}.`, 'not solved', 'attention');
  }
  if (prepared) return ready(model);
  return line(`${label} is not prepared yet.`, 'not prepared', 'attention', model.canPrepare ? 'prepare' : 'none');
}

function ready(model: CadModelView): CadModelStatusLine {
  const triangles = model.triangles === null ? '' : ` · ${model.triangles.toLocaleString()} triangles`;
  return line(`Ready to solve${triangles}`, `ready${triangles}`, 'ready', 'solve');
}
