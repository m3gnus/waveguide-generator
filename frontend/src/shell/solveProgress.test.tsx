import { beforeEach, describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { JobItem } from '../api/jobsSocket';
import {
  etaSeconds,
  formatClock,
  isIndeterminate,
  operationStageWord,
  operationWaitingReason,
  parseFrequencyProgress,
  resetSolveStageClocksForTests,
  resolveEngineLabel,
  solveDetailLine,
  solveStageWord,
  SolveProgressView,
  type OperationProgressLike,
} from './solveProgress';

function partial(overrides: Partial<JobItem>): Pick<JobItem, 'status' | 'stage'> {
  return { status: 'running', stage: null, ...overrides } as Pick<JobItem, 'status' | 'stage'>;
}

describe('solveStageWord', () => {
  it('names every stage transition the requirement lists', () => {
    expect(solveStageWord(partial({ status: 'queued', stage: null }))).toBe('Preparing mesh');
    expect(solveStageWord(partial({ status: 'running', stage: null }))).toBe('Preparing mesh');
    expect(solveStageWord(partial({ status: 'running', stage: 'initializing' }))).toBe('Preparing mesh');
    expect(solveStageWord(partial({ status: 'running', stage: 'mesh' }))).toBe('Preparing mesh');
    expect(solveStageWord(partial({ status: 'running', stage: 'assemble' }))).toBe('Starting…');
    expect(solveStageWord(partial({ status: 'running', stage: 'solve' }))).toBe('Solving');
    expect(solveStageWord(partial({ status: 'running', stage: 'radiation_impedance' }))).toBe('Solving');
    expect(solveStageWord(partial({ status: 'running', stage: 'postprocess' }))).toBe('Combining');
    expect(solveStageWord(partial({ status: 'running', stage: 'cancelling' }))).toBe('Cancelling…');
    expect(solveStageWord(partial({ status: 'complete', stage: 'postprocess' }))).toBe('Done');
    expect(solveStageWord(partial({ status: 'error', stage: 'solve' }))).toBe('Failed');
    expect(solveStageWord(partial({ status: 'cancelled', stage: 'solve' }))).toBe('Cancelled');
  });

  it('moves forward through the stage sequences emitted by every adapter', () => {
    const rank = { 'Preparing mesh': 0, 'Starting…': 1, Solving: 2, Combining: 3, Done: 4 } as const;
    const sequences = [
      // runtime.py starts real jobs at initializing; full-3D adapters mesh
      // before their setup callback. CircSym emits mesh_prepare itself.
      ['queued', 'initializing', 'mesh', 'assemble', 'solve', 'postprocess', 'complete'],
      // Imported BEAT/BEMPP use the already prepared CAD mesh, then setup.
      ['queued', 'initializing', 'assemble', 'solve', 'postprocess', 'complete'],
      // Dry run starts at mesh and assembles before its synthetic solve.
      ['queued', 'mesh', 'assemble', 'solve', 'postprocess', 'complete'],
    ];
    for (const sequence of sequences) {
      const words = sequence.map((stage) => solveStageWord(partial(stage === 'queued'
        ? { status: 'queued', stage: null }
        : stage === 'complete' ? { status: 'complete', stage: null } : { stage })));
      expect(words).toEqual([...words].sort((a, b) => rank[a as keyof typeof rank] - rank[b as keyof typeof rank]));
    }
  });
});

function op(overrides: Partial<OperationProgressLike> = {}): OperationProgressLike {
  return { state: 'received', stage: null, reason: null, ...overrides };
}

describe('operationStageWord', () => {
  it('reads server/cadlink/preparation.py stages onto the same words a job uses, monotonically', () => {
    expect(operationStageWord(op({ state: 'received', stage: null }))).toBe('Received');
    expect(operationStageWord(op({ state: 'received', stage: 'validating' }))).toBe('Received');
    expect(operationStageWord(op({ state: 'processing', stage: 'preparing-mesh' }))).toBe('Preparing mesh');
    expect(operationStageWord(op({ state: 'processing', stage: 'ready' }))).toBe('Preparing mesh');
    expect(operationStageWord(op({ state: 'processing', stage: 'submitted' }))).toBe('Preparing mesh');
  });

  it('shows a clear waiting-for-you state, distinct from the pipeline stages', () => {
    expect(operationStageWord(op({ state: 'needs_user_input', reason: 'setup_required' }))).toBe('Waiting for you');
    expect(operationWaitingReason(op({ state: 'needs_user_input', reason: 'setup_required' })))
      .toBe('needs its solve settings');
    expect(operationWaitingReason(op({ state: 'received' }))).toBeNull();
  });

  it('names cancellation and terminal outcomes', () => {
    expect(operationStageWord(op({ state: 'cancel_requested' }))).toBe('Cancelling…');
    expect(operationStageWord(op({ state: 'rejected' }))).toBe('Failed');
    expect(operationStageWord(op({ state: 'cancelled' }))).toBe('Cancelled');
    expect(operationStageWord(op({ state: 'recovery_required' }))).toBe('Needs recovery');
  });
});

describe('parseFrequencyProgress', () => {
  it('reads the printed number as the completed count -- every engine\'s callback fires after that frequency finishes', () => {
    // hornlab_metal_bem/sweep.py, hornlab_beat_bem/sweep.py and
    // hornlab_bempp_bem/sweep.py all invoke progress_callback(i, total, freq)
    // only after appending that frequency's result, and the WG adapters
    // (server/solver/metal.py, beat.py, bempp.py) print index+1 -- so
    // "Solving frequency 3/12" means 3 are already done, not 2.
    expect(parseFrequencyProgress('Solving frequency 3/12 with Metal BEM')).toEqual({ completed: 3, total: 12 });
    expect(parseFrequencyProgress('Solving frequency 1/12 with BEAT Engine')).toEqual({ completed: 1, total: 12 });
    expect(parseFrequencyProgress('Solving frequency 12/12 with BEMPP BEM')).toEqual({ completed: 12, total: 12 });
  });

  it('reads official BEAT\'s own "Solved i/N" wording the same way', () => {
    expect(parseFrequencyProgress('Solved frequency 3/12')).toEqual({ completed: 3, total: 12 });
  });

  it('returns null for a message with no frequency count', () => {
    expect(parseFrequencyProgress('Configuring BEMPP BEM solve (cpu)')).toBeNull();
    expect(parseFrequencyProgress(null)).toBeNull();
  });

  it('does not mistake an unrelated count for a frequency count', () => {
    // The passive-cardioid radiation-impedance campaign has its own i/N in
    // its message; callers gate this on job.stage === 'solve' themselves, but
    // the parser itself is honest about whatever i/N it is handed.
    expect(parseFrequencyProgress('Solving passive-cardioid radiation impedance 32/160 at 400 Hz'))
      .toEqual({ completed: 32, total: 160 });
  });

  it('keeps an imported message as a channel count, never a false overall count', () => {
    // A rear-facing axial channel can solve two tag groups, each restarting
    // i/N. Overall work comes from the job progress field.
    expect(parseFrequencyProgress('Solving frequency 2/8 of drive channel 1/3 (hf) with BEAT Engine'))
      .toEqual({ completed: 2, total: 8, channel: { index: 1, count: 3 } });
    expect(parseFrequencyProgress('Solving frequency 3/8 of drive channel 2/3 (mf) with BEMPP BEM'))
      .toEqual({ completed: 3, total: 8, channel: { index: 2, count: 3 } });
    expect(parseFrequencyProgress('Solving frequency 8/8 of drive channel 3/3 (lf) with BEAT Engine'))
      .toEqual({ completed: 8, total: 8, channel: { index: 3, count: 3 } });
  });
});

describe('isIndeterminate', () => {
  it('is indeterminate only on the solve stage with no frequency count in the message', () => {
    expect(isIndeterminate({ status: 'running', stage: 'solve', stage_message: 'Configuring the sweep' })).toBe(true);
    expect(isIndeterminate({ status: 'running', stage: 'solve', stage_message: 'Solving frequency 1/6' })).toBe(false);
    expect(isIndeterminate({ status: 'running', stage: 'mesh', stage_message: null })).toBe(false);
    expect(isIndeterminate({ status: 'complete', stage: 'solve', stage_message: null })).toBe(false);
  });
});

describe('etaSeconds', () => {
  it('is hidden until frequency work advances after the clock starts', () => {
    expect(etaSeconds(30, null)).toBeNull();
    expect(etaSeconds(30, { completed: 0, total: 10 })).toBeNull();
    expect(etaSeconds(30, { completed: 1, total: 10 }, 0)).toBeNull();
  });

  it('projects the remaining time from the average time per frequency so far', () => {
    // 2 done in 20 s -> 10 s/freq -> 8 remaining -> 80 s.
    expect(etaSeconds(20, { completed: 2, total: 10 })).toBeCloseTo(80);
  });

  it('uses only work completed after a late mount as its rate', () => {
    expect(etaSeconds(20, { completed: 7, total: 10 }, 2)).toBe(30);
    expect(etaSeconds(20, { completed: 7, total: 10 }, 0)).toBeNull();
    expect(etaSeconds(10, { completed: 2, total: 10 }, 1)).toBe(80);
  });

  it('is zero once every frequency is accounted for', () => {
    expect(etaSeconds(100, { completed: 10, total: 10 })).toBe(0);
  });

  it('matches a worked example: 20 s of meshing/warm-up, then 1 s/frequency x 100', () => {
    // At frequency 3, only the 3 s spent in the solve stage counts -- not the
    // 23 s since the job itself started. 3 done in 3 s -> 1 s/freq -> 97
    // remaining -> 97 s = 1:37, not the ~18:47 that job-started_at gave when
    // meshing/warm-up was folded into the rate.
    const seconds = etaSeconds(3, { completed: 3, total: 100 });
    expect(seconds).not.toBeNull();
    expect(formatClock(seconds!)).toBe('1:37');
  });
});

describe('formatClock', () => {
  it('is always mm:ss, including under a minute', () => {
    expect(formatClock(0)).toBe('0:00');
    expect(formatClock(5)).toBe('0:05');
    expect(formatClock(65)).toBe('1:05');
    expect(formatClock(3_661)).toBe('61:01');
  });
});

describe('resolveEngineLabel', () => {
  it('names the known engines, not a bare uppercase of the slug', () => {
    expect(resolveEngineLabel('metal')).toBe('Metal');
    expect(resolveEngineLabel('beat')).toBe('BEAT Engine');
    expect(resolveEngineLabel('official_beat')).toBe('Official BEAT');
    expect(resolveEngineLabel('bempp')).toBe('BEMPP');
    expect(resolveEngineLabel('auto')).toBe('AUTO');
    expect(resolveEngineLabel('beat-metal')).toBe('BEAT Metal');
    for (const engine of ['beat-cpu', 'beat-metal', 'beat-cuda', 'beat-rocm']) {
      expect(resolveEngineLabel(engine)).not.toMatch(/[·—]/);
    }
    expect(resolveEngineLabel('axisym')).toBe('CircSym');
  });

  it('title-cases an unrecognized slug rather than shouting it', () => {
    expect(resolveEngineLabel('some_future_engine')).toBe('Some Future Engine');
  });

  it('is null for nothing to show', () => {
    expect(resolveEngineLabel(null)).toBeNull();
    expect(resolveEngineLabel('')).toBeNull();
  });
});

describe('solveDetailLine', () => {
  it('reads the resolved domain, not the requested one, and the labelled engine', () => {
    const line = solveDetailLine({
      config_summary: { drive_channel_ids: ['a', 'b', 'c'], symmetry: { resolved: 'half', requested: 'auto' } },
      solve_options: { engine: 'metal', symmetry: 'auto' } as JobItem['solve_options'],
    });
    expect(line).toBe('Metal · 3 sources · half');
  });

  it('falls back to a frequency count for a parametric design with no drive channels', () => {
    const line = solveDetailLine({
      config_summary: { symmetry: { resolved_quadrants: 1234 } },
      solve_options: { engine: 'bempp', symmetry: 'full', num_frequencies: 24 } as JobItem['solve_options'],
    });
    expect(line).toBe('BEMPP · 24 freq · full');
  });

  it('maps both half domains and quarter from the backend quadrants', () => {
    for (const [quadrants, domain] of [[12, 'half'], [14, 'half'], [1, 'quarter']] as const) {
      expect(solveDetailLine({ config_summary: { symmetry: { resolved_quadrants: quadrants } }, solve_options: { engine: 'axisym' } as JobItem['solve_options'] }))
        .toContain(domain);
    }
  });

  it('names the continuous CircSym domain before its quadrant metadata', () => {
    expect(solveDetailLine({
      config_summary: { symmetry: { domain: 'continuous-axisymmetric', resolved_quadrants: 1 } },
      solve_options: { engine: 'axisym' } as JobItem['solve_options'],
    })).toBe('CircSym · axisymmetric');
  });

  it('normalizes CAD half plane names to the same domain words as parametric solves', () => {
    for (const resolved of ['half_xz', 'half_yz']) {
      expect(solveDetailLine({
        config_summary: { symmetry: { resolved } },
        solve_options: { engine: 'metal' } as JobItem['solve_options'],
      })).toBe('Metal · half');
    }
  });

  it('omits the domain when config_summary carries no resolved symmetry', () => {
    const line = solveDetailLine({
      config_summary: {},
      solve_options: { engine: 'metal', symmetry: 'auto', num_frequencies: 10 } as JobItem['solve_options'],
    });
    expect(line).toBe('Metal · 10 freq');
  });
});

describe('SolveProgressView (component)', () => {
  beforeEach(() => { resetSolveStageClocksForTests(); });

  it('renders nothing for neither a job nor an operation', () => {
    expect(renderToStaticMarkup(<SolveProgressView variant="compact"/>)).toBe('');
  });
});
