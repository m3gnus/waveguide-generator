import { describe, expect, it } from 'vitest';
import {
  advanceRunSequence,
  decorateRunName,
  nextRunLabel,
  runNameDateFor,
  runSequenceFor,
} from './runNaming';

const FORMATS = {
  runNameDatePosition: 'off',
  runNameDateFormat: 'yymmdd',
  runNameNumberPosition: 'suffix',
  runNameNumberFormat: 'natural',
} as const;

describe('run labels derived from the design name', () => {
  const now = new Date(2026, 7, 12, 12);

  it('numbers the runs of one design in sequence without touching its name', () => {
    let state = { runSequenceName: '', runSequenceNext: 1 };
    expect(nextRunLabel('Tritonia-M', { ...FORMATS, ...state }, now)).toBe('Tritonia-M1');
    state = advanceRunSequence(state, 'Tritonia-M');
    expect(nextRunLabel('Tritonia-M', { ...FORMATS, ...state }, now)).toBe('Tritonia-M2');
    state = advanceRunSequence(state, 'Tritonia-M');
    expect(state).toEqual({ runSequenceName: 'Tritonia-M', runSequenceNext: 3 });
  });

  it('restarts numbering when the design is renamed', () => {
    const state = advanceRunSequence(advanceRunSequence({ runSequenceName: '', runSequenceNext: 1 }, 'asro'), 'asro');
    expect(runSequenceFor(state, 'asro')).toBe(3);
    expect(runSequenceFor(state, 'asro-mk2')).toBe(1);
    expect(nextRunLabel('asro-mk2', { ...FORMATS, ...state }, now)).toBe('asro-mk21');
  });

  it('can turn numbering off or pad it to a fixed width', () => {
    const state = { runSequenceName: 'horn', runSequenceNext: 2 };
    expect(nextRunLabel('horn', { ...FORMATS, ...state, runNameNumberPosition: 'off' }, now)).toBe('horn');
    expect(nextRunLabel('horn', { ...FORMATS, ...state, runNameNumberFormat: '2-digit' }, now)).toBe('horn02');
    expect(nextRunLabel('horn', { ...FORMATS, ...state, runNameNumberFormat: '3-digit' }, now)).toBe('horn002');
  });

  it('leaves labels undated by default and supports both date positions', () => {
    const undated = { ...FORMATS, runNameNumberPosition: 'off' } as const;
    expect(decorateRunName('horn', undated)).toBe('horn');
    expect(decorateRunName('horn', { ...undated, runNameDatePosition: 'prefix' }, 1, now)).toBe('260812_horn');
    expect(decorateRunName('horn', { ...undated, runNameDatePosition: 'suffix' }, 1, now)).toBe('horn_260812');
    expect(runNameDateFor(now, 'yyyy-mm-dd')).toBe('2026-08-12');
  });

  it('numbers the core before adding a date suffix', () => {
    const state = { runSequenceName: 'horn', runSequenceNext: 2 };
    expect(nextRunLabel('horn', { ...FORMATS, ...state, runNameDatePosition: 'suffix' }, now))
      .toBe('horn2_260812');
  });

  it('falls back to Untitled rather than inventing a name', () => {
    expect(nextRunLabel('', { ...FORMATS, runSequenceName: '', runSequenceNext: 1 }, now)).toBe('Untitled1');
  });
});
