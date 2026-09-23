import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import {
  etaSeconds,
  formatClock,
  isIndeterminate,
  parseFrequencyProgress,
  solveDetailLine,
  solveStageWord,
} from './solveProgress';

function partial(overrides: Partial<JobItem>): Pick<JobItem, 'status' | 'stage'> {
  return { status: 'running', stage: null, ...overrides } as Pick<JobItem, 'status' | 'stage'>;
}

describe('solveStageWord', () => {
  it('names every stage transition the requirement lists', () => {
    expect(solveStageWord(partial({ status: 'queued', stage: null }))).toBe('Received');
    expect(solveStageWord(partial({ status: 'running', stage: null }))).toBe('Solving');
    expect(solveStageWord(partial({ status: 'running', stage: 'mesh' }))).toBe('Preparing mesh');
    expect(solveStageWord(partial({ status: 'running', stage: 'assemble' }))).toBe('Solving');
    expect(solveStageWord(partial({ status: 'running', stage: 'solve' }))).toBe('Solving');
    expect(solveStageWord(partial({ status: 'running', stage: 'radiation_impedance' }))).toBe('Solving');
    expect(solveStageWord(partial({ status: 'running', stage: 'postprocess' }))).toBe('Combining');
    expect(solveStageWord(partial({ status: 'complete', stage: 'postprocess' }))).toBe('Done');
    expect(solveStageWord(partial({ status: 'error', stage: 'solve' }))).toBe('Failed');
    expect(solveStageWord(partial({ status: 'cancelled', stage: 'solve' }))).toBe('Cancelled');
  });
});

describe('parseFrequencyProgress', () => {
  it('reads "Solving frequency i/N" as i-1 completed (Metal/BEAT wording)', () => {
    expect(parseFrequencyProgress('Solving frequency 3/12 with Metal BEM')).toEqual({ completed: 2, total: 12 });
    expect(parseFrequencyProgress('Solving frequency 1/12 with BEAT Engine')).toEqual({ completed: 0, total: 12 });
  });

  it('reads "Solved frequency i/N" as i completed (official BEAT wording)', () => {
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
      .toEqual({ completed: 31, total: 160 });
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
  it('is hidden before two frequencies have completed', () => {
    expect(etaSeconds(30, null)).toBeNull();
    expect(etaSeconds(30, { completed: 0, total: 10 })).toBeNull();
    expect(etaSeconds(30, { completed: 1, total: 10 })).toBeNull();
  });

  it('projects the remaining time from the average time per frequency so far', () => {
    // 2 done in 20 s -> 10 s/freq -> 8 remaining -> 80 s.
    expect(etaSeconds(20, { completed: 2, total: 10 })).toBeCloseTo(80);
  });

  it('is zero once every frequency is accounted for', () => {
    expect(etaSeconds(100, { completed: 10, total: 10 })).toBe(0);
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

describe('solveDetailLine', () => {
  it('reads engine, source count and domain for a CAD import', () => {
    const line = solveDetailLine({
      config_summary: { drive_channel_ids: ['a', 'b', 'c'] },
      solve_options: { engine: 'metal', symmetry: 'half' } as JobItem['solve_options'],
    });
    expect(line).toBe('METAL · 3 sources · half');
  });

  it('falls back to a frequency count for a parametric design with no drive channels', () => {
    const line = solveDetailLine({
      config_summary: {},
      solve_options: { engine: 'bempp', symmetry: 'full', num_frequencies: 24 } as JobItem['solve_options'],
    });
    expect(line).toBe('BEMPP · 24 freq · full');
  });
});
