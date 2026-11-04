import { describe, expect, it } from 'vitest';
import { normalizedImportedPoints, parseLookupPaste, parsePointPaste, withStationShape } from './FreeformEditors';

describe('FREEFORM editor workflows', () => {
  it('keeps a station parameter the new shape still takes and drops one it does not', () => {
    expect(withStationShape({ t: .5, shape: 'superellipse', exponent: 6 }, 'superellipse')).toEqual({ t: .5, shape: 'superellipse', exponent: 6 });
    expect(withStationShape({ t: 1, shape: 'rounded_rectangle', corner_radius_mm: 25 }, 'rounded_rectangle')).toEqual({ t: 1, shape: 'rounded_rectangle', corner_radius_mm: 25 });
    expect(withStationShape({ t: 1, shape: 'rounded_rectangle', corner_radius_mm: 25 }, 'superellipse')).toEqual({ t: 1, shape: 'superellipse', exponent: 4 });
    expect(withStationShape({ t: .5, shape: 'superellipse', exponent: 6 }, 'rounded_rectangle')).toEqual({ t: .5, shape: 'rounded_rectangle', corner_radius_mm: 10 });
    expect(withStationShape({ t: .5, shape: 'superellipse', exponent: 6 }, 'ellipse')).toEqual({ t: .5, shape: 'ellipse' });
  });

  it('parses H/V compact CSV and two- or three-column point rows with ranges', () => {
    const compact = parsePointPaste('# z_cm;r_h_cm;r_v_cm\n0;1.27;1.27\n12;14;10');
    expect(compact.importedLength).toBe(120);
    expect(compact.pointsByPlane?.H.at(-1)).toEqual({ t: 1, r: 140 });
    expect(compact.pointsByPlane?.V.at(-1)).toEqual({ t: 1, r: 100 });
    expect(parsePointPaste('0 12.7\n120 140').points.at(-1)).toEqual({ t: 1, r: 140 });
    expect(parsePointPaste('0 12.7 15\n120 140 60').points.at(-1)).toEqual({ t: 1, r: 140, angle_deg: 60 });
    expect(() => parsePointPaste('10 20 95')).toThrow('angle must be between');
    expect(() => parsePointPaste('10 20 20 1')).toThrow('strength was removed');
    expect(() => parsePointPaste('0 12.7\n50 30\n50 40\n120 140')).toThrow('larger than the one before it');
  });

  it('keeps every imported point when the current design is shorter than the imported span', () => {
    const imported = parsePointPaste('0 12.7\n25 20\n70 35\n120 60').points;
    const current = [{ t: 0, r: 12.7 }, { t: 1, r: 50 }];
    expect(normalizedImportedPoints(imported, current)).toEqual([
      { t: 0, r: 12.7 },
      { t: 25 / 120, r: 20 },
      { t: 70 / 120, r: 35 },
      { t: 1, r: 60 },
    ]);
  });
});

it.each(['0;1,27;1,27\n12,5;14,2;10,2', '0\t12,7\n125,5\t140,2'])('reads comma decimals in point cells with explicit separators', (text) => {
  const parsed = parsePointPaste(text);
  expect(parsed.points[0].r).toBe(12.7);
  expect(parsed.points.at(-1)!.r).toBeCloseTo(text.includes(';') ? 142 : 140.2);
  expect(parsed.importedLength).toBe(text.includes(';') ? 125 : 125.5);
});
it.each(['0;1,000;2\n12;14;10', '0\t1.234,5\n120\t140', '0 12,7\n120 140'])('refuses ambiguous or grouped point cells', (text) => {
  expect(() => parsePointPaste(text)).toThrow();
});

describe('legacy LOOKUP and Custom profile paste limits', () => {
  it('keeps LOOKUP paste unlimited while a Custom profile paste stops at 64 anchors with a reason', () => {
    const rows = Array.from({ length: 200 }, (_value, index) => `${index},${12.7 + index / 2}`).join('\n');
    expect(parseLookupPaste(rows)).toHaveLength(200);
    expect(() => parsePointPaste(rows)).toThrow('at most 64 anchors including the throat and mouth. Larger straight-line tables stay in legacy LOOKUP designs.');
    const lookupStyle = parsePointPaste('z (mm), r (mm)\n0,12.7\n60,40\n120,140');
    expect(lookupStyle.lookupStyle).toBe(true);
    expect(lookupStyle.points).toHaveLength(3);
    expect(parsePointPaste('0 12.7 15\n120 140').lookupStyle).toBe(false);
  });
});
