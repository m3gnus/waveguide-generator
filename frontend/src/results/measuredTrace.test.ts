import { describe, expect, it } from 'vitest';
import { measuredTraceLabel, parseMeasuredTrace } from './measuredTrace';

/** Trimmed from a real REW "Export measurement as text" file. */
const REW_EXPORT = `* Measurement: 260318tritonia-q_v03 hor 0
* Dated: 18-Mar-2026 14:22:07
* Source: USB Audio CODEC, mic UMIK-1 7042831, 90deg cal
* Format: 256k Log Swept Sine 20 to 20000 Hz
* Smoothing: 1/12 octave
* Note: 1 m, 2.83 V, gated 5.4 ms
* Measurement data measured with REW V5.31.3
*
* Freq(Hz) SPL(dB) Phase(degrees)
200.000	86.418	-141.203
250.000	87.902	-160.774
315.000	89.117	179.336
400.000	90.004	158.912
500.000	90.551	137.480
`;

describe('parseMeasuredTrace', () => {
  it('reads a REW export past its header block', () => {
    const trace = parseMeasuredTrace(REW_EXPORT, 'tritonia_v03_1m.frd');
    expect(trace.label).toBe('tritonia_v03_1m');
    expect(trace.points).toHaveLength(5);
    expect(trace.points[0]).toEqual({ frequencyHz: 200, splDb: 86.418, phaseDeg: -141.203 });
    expect(trace.points.at(-1)).toEqual({ frequencyHz: 500, splDb: 90.551, phaseDeg: 137.48 });
  });

  it('accepts every comment prefix the FRD dialects use', () => {
    const trace = parseMeasuredTrace(
      '* star\n; semicolon\n# hash\n// slashes\n\n100 90\n200 91\n',
      'mixed.txt',
    );
    expect(trace.points.map(({ frequencyHz }) => frequencyHz)).toEqual([100, 200]);
  });

  it('leaves phase null for a two-column file', () => {
    const trace = parseMeasuredTrace('100 90\n200 91\n', 'two-column.frd');
    expect(trace.points.every(({ phaseDeg }) => phaseDeg === null)).toBe(true);
  });

  it('reads whitespace, tab and comma delimited rows', () => {
    const trace = parseMeasuredTrace('100  90.5\n200\t91.5\n400,92.5\n', 'delimiters.csv');
    expect(trace.points.map(({ splDb }) => splDb)).toEqual([90.5, 91.5, 92.5]);
  });

  it('reads exponent notation and a leading BOM', () => {
    const trace = parseMeasuredTrace('\ufeff1e2 9.05e1\n2E2 -1.5\n', 'exponent.frd');
    expect(trace.points.map(({ frequencyHz, splDb }) => [frequencyHz, splDb])).toEqual([[100, 90.5], [200, -1.5]]);
  });

  it('skips rows that are not two or three numbers', () => {
    const trace = parseMeasuredTrace(
      '100 90\nbogus row here\n150 NaN\n200 91 12 34\n400 92\n',
      'noisy.frd',
    );
    expect(trace.points.map(({ frequencyHz }) => frequencyHz)).toEqual([100, 400]);
  });

  it('skips non-positive, repeated and descending frequencies', () => {
    const trace = parseMeasuredTrace('0 90\n-100 90\n200 91\n200 95\n150 94\n400 92\n', 'unsorted.frd');
    expect(trace.points.map(({ frequencyHz, splDb }) => [frequencyHz, splDb])).toEqual([[200, 91], [400, 92]]);
  });

  it('rejects a file with fewer than two usable rows', () => {
    expect(() => parseMeasuredTrace('* header only\n100 90\n', 'thin.frd'))
      .toThrow(/1 usable measurement point/);
    expect(() => parseMeasuredTrace('impulse response, not a response curve\n', 'wrong.txt'))
      .toThrow(/0 usable measurement points and 1 unreadable row/);
  });

  it('rejects a comma-decimal export instead of misreading its digits', () => {
    expect(() => parseMeasuredTrace('100,0 90,5\n200,0 91,5\n', 'de.frd')).toThrow(/unreadable rows/);
  });

  it('carries thousands of rows', () => {
    const rows = Array.from({ length: 8_192 }, (_, index) => `${20 + index * 2.4} ${90 + Math.sin(index) * 3}`);
    const trace = parseMeasuredTrace(`* Freq(Hz) SPL(dB)\n${rows.join('\n')}\n`, 'sweep.frd');
    expect(trace.points).toHaveLength(8_192);
  });
});

describe('measuredTraceLabel', () => {
  it('drops the directory and the extension', () => {
    expect(measuredTraceLabel('C:\\rew\\exports\\horn v3.frd')).toBe('horn v3');
    expect(measuredTraceLabel('/tmp/horn.v3.txt')).toBe('horn.v3');
    expect(measuredTraceLabel('no-extension')).toBe('no-extension');
    expect(measuredTraceLabel('.frd')).toBe('Measured');
  });
});
