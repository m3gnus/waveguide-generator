import { describe, expect, it } from 'vitest';
import { buildPolarCsv } from './exporters';
import { buildPolarFrdSet } from './frd';
import { directivityGrid, polarSeries } from './mappers';
import { withMeasurementAngle } from './measurementAngle';
import { levelAtAngle, renormalizeRows } from './normalization';
import type { ResultPayload } from './types';

const cases: Array<[string, unknown, number | null, number | null]> = [
  ['level', -6, -6, -6],
  ['zero level', 0, 0, 0],
  ['negative zero', -0, -0, -0],
  ['complex', [3, 4], 13.979400086720377, 13.979400086720377],
  ['zero magnitude', [0, 0], null, null],
  ['nonfinite real', [Infinity, 1], null, null],
  ['nonfinite imaginary', [1, NaN], null, null],
  ['null', null, null, null],
  ['undefined', undefined, null, null],
  ['nonfinite level', Infinity, null, null],
  ['NaN level', NaN, null, null],
  ['numeric string', '12', null, 12],
  ['empty string', '', null, null],
  ['blank string', ' ', null, 0],
  ['true', true, null, 1],
  ['false', false, null, 0],
  ['coerced complex components', ['3', '4'], 13.979400086720377, 13.979400086720377],
  ['null complex component', [null, 1], 0, 0],
  ['incomplete complex pair', [1], null, null],
];

function result(value: unknown): ResultPayload {
  return {
    frequencies: [100],
    spl_on_axis: { frequencies: [100], spl: [90] },
    directivity: { horizontal: [[[0, 0], [30, value]]] },
  } as ResultPayload;
}

describe('polar value compatibility across charts and exports', () => {
  it.each(cases)('keeps strict projections and CSV coercion for %s', (_label, value, strictDb, csvDb) => {
    const payload = result(value);
    const row = payload.directivity!.horizontal![0];
    expect(levelAtAngle([row[1]], 30)).toBe(strictDb);
    expect(directivityGrid(payload).data[1][2]).toBe(strictDb);
    expect(polarSeries(payload, 0, 'horizontal')[1]).toEqual([strictDb, 30]);
    expect(withMeasurementAngle(payload, 'horizontal', 30).spl_on_axis?.spl)
      .toEqual([strictDb === null ? null : 90 + strictDb]);
    expect(renormalizeRows([row], 0)[0][1][1]).toEqual(strictDb === null ? value : strictDb - 0);
    expect(buildPolarCsv(payload).split('\n').at(-2)).toBe(`100,horizontal,30,${csvDb ?? ''}`);
    const frd = buildPolarFrdSet(payload, 'compat')[1].text;
    const data = frd.split('\n').filter((line) => line && !line.startsWith('*'));
    expect(data).toEqual(strictDb === null ? [] : [`100.000000\t${strictDb.toFixed(4)}`]);
  });

  it('retains exact CSV and FRD bytes for mixed legacy samples', () => {
    const payload = {
      frequencies: cases.map((_, index) => 100 + index),
      directivity: { horizontal: cases.map(([, value]) => [[30, value]]) },
    } as ResultPayload;
    expect({
      csv: buildPolarCsv(payload),
      frd: buildPolarFrdSet(payload, 'compat'),
    }).toMatchSnapshot();
  });
});
