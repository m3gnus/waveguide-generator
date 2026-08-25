import { describe, expect, it } from 'vitest';
import { limitingSummary, maxOutputChartSeries, maxOutputMissingReason, maxOutputOf, memberLabelOf } from './maxOutput';
import type { MaxOutputMetadata, ResultPayload } from './types';

const FREQUENCIES = [100, 1_000, 10_000];

function maxOutput(overrides: Partial<MaxOutputMetadata> = {}): MaxOutputMetadata {
  return {
    frequencies: [...FREQUENCIES],
    members: {
      'drive-lf': {
        spl_max_db: [110, 118, null],
        headroom_db: [10, 18, null],
        limit: ['xmax', 'xmax', null],
        excursion_fraction: 0.31,
        power_fraction: 0.08,
        voltage_fraction: null,
      },
      'drive-hf': {
        spl_max_db: [null, 124, 126],
        headroom_db: [null, 24, 26],
        limit: [null, 'power', 'power'],
        power_fraction: 0.06,
      },
    },
    combined: {
      spl_max_db: [110, 118, 126],
      headroom_db: [10, 18, 26],
      limit: ['xmax', 'xmax', 'power'],
      limiting_member: ['drive-lf', 'drive-lf', 'drive-hf'],
    },
    caveat: 'small-signal swept-sine ceilings',
    ...overrides,
  };
}

function result(record: MaxOutputMetadata | null): ResultPayload {
  return {
    frequencies: [...FREQUENCIES],
    spl_on_axis: { frequencies: [...FREQUENCIES], spl: [100, 100, 100] },
    metadata: {
      combine: {
        members: ['drive-lf', 'drive-hf'],
        crossovers_hz: [1_000],
        ...(record ? { max_output: record } : {}),
      },
    },
  } as unknown as ResultPayload;
}

describe('maximum output', () => {
  it('reads the record off a combined channel and nothing else', () => {
    expect(maxOutputOf(result(maxOutput()))).not.toBeNull();
    expect(maxOutputOf(result(null))).toBeNull();
    expect(maxOutputOf({ frequencies: FREQUENCIES } as ResultPayload)).toBeNull();
  });

  it('draws the system, each member, and where the run actually sits', () => {
    const series = maxOutputChartSeries(result(maxOutput()), (member) => member.toUpperCase());
    expect(series.map((trace) => trace.name)).toEqual([
      'System maximum', 'DRIVE-LF alone', 'DRIVE-HF alone', 'At the shown level',
    ]);
    expect(series[0].role).toBe('system');
    expect(series[0].data).toEqual([[100, 110], [1_000, 118], [10_000, 126]]);
    // A member outside its band has no ceiling there, and leaves a gap rather
    // than a line dropped to zero.
    expect(series[1].data).toEqual([[100, 110], [1_000, 118], [10_000, null]]);
    expect(series.at(-1)!.role).toBe('shown');
  });

  it('says which member holds the system back, and on what', () => {
    expect(limitingSummary(maxOutput(), (member) => member.toUpperCase()))
      .toBe('DRIVE-LF \u00b7 Xmax \u00b7 67% of band');
  });

  it('names an unrated member rather than leaving a gap in the curve', () => {
    const record = maxOutput({ unlimited_members: ['drive-hf'] });
    expect(limitingSummary(record, (member) => member.toUpperCase()))
      .toBe('no ceiling known for DRIVE-HF');
  });

  it('labels a member by the band role the run recorded for it', () => {
    const label = memberLabelOf(result(maxOutput()));
    expect(label('drive-lf')).toBe('drive-lf');
    const roled = result(maxOutput());
    (roled.metadata!.combine as Record<string, unknown>).member_roles = ['LF', 'HF'];
    expect(memberLabelOf(roled)('drive-hf')).toBe('HF');
    // An unroled member keeps the id it was authored with rather than blanking.
    (roled.metadata!.combine as Record<string, unknown>).member_roles = [null, null];
    expect(memberLabelOf(roled)('drive-hf')).toBe('drive-hf');
  });

  it('says nothing rather than guessing when nothing limited the run', () => {
    const record = maxOutput({
      combined: {
        spl_max_db: [null, null, null],
        headroom_db: [null, null, null],
        limit: [null, null, null],
        limiting_member: [null, null, null],
      },
    });
    expect(limitingSummary(record)).toBeNull();
    expect(limitingSummary(null)).toBeNull();
  });

  it('blames the missing input rather than reporting an absence', () => {
    expect(maxOutputMissingReason(result(null))).toContain('Xmax or a rated power');
    expect(maxOutputMissingReason({} as ResultPayload)).toContain('combined channel');
  });
});
