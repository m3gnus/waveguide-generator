import { describe, expect, it } from 'vitest';
import { convertDesignToFreeform, freeformFromProfileCsv } from '../api/designIo';
import { designForFamily } from '../stores/design';
import { normalizedImportedPoints, parsePointPaste } from './FreeformEditors';

describe('FREEFORM editor workflows', () => {
  it('parses H/V compact CSV and two- or three-column point rows with ranges', () => {
    const compact = parsePointPaste('# z_cm;r_h_cm;r_v_cm\n0;1.27;1.27\n12;14;10');
    expect(compact.importedLength).toBe(120);
    expect(compact.pointsByPlane?.H.at(-1)).toEqual({ t: 1, r: 140 });
    expect(compact.pointsByPlane?.V.at(-1)).toEqual({ t: 1, r: 100 });
    expect(parsePointPaste('0 12.7\n120 140').points.at(-1)).toEqual({ t: 1, r: 140 });
    expect(parsePointPaste('0 12.7 15\n120 140 60').points.at(-1)).toEqual({ t: 1, r: 140, angle_deg: 60 });
    expect(() => parsePointPaste('10 20 95')).toThrow('angle must be between');
    expect(() => parsePointPaste('10 20 20 1')).toThrow('strength was removed');
    expect(() => parsePointPaste('0 12.7\n50 30\n50 40\n120 140')).toThrow('strictly increasing');
  });

  it('uses a server converter when available and falls back to endpoint-preserving conversion on older servers', async () => {
    const design = designForFamily('OSSE');
    design.simulation.f1 = 250;
    const fallbackFetch = async () => new Response('', { status: 404 });
    const fallback = await convertDesignToFreeform(design, fallbackFetch as typeof fetch);
    expect(fallback.formula).toBe('FREEFORM');
    expect(fallback.length).toBe(design.L);
    expect(fallback.profile_h!.points).toEqual([{ t: 0, r: design.r0 }, { t: 1, r: 140 }]);
    expect(fallback.simulation.f1).toBe(250);

    const converted = designForFamily('FREEFORM');
    converted.profile_h!.points[1].r = 222;
    const serverFetch = async () => new Response(JSON.stringify({ design: converted }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    expect((await convertDesignToFreeform(design, serverFetch as typeof fetch)).profile_h!.points.at(-1)!.r).toBe(222);
  });

  it('converts the existing server profile export into distinct H/V editable meridians', () => {
    const csv = '# x_cm;y_cm;z_cm\n1;0;0\n2;0;5\n3;0;10\n\n0;1;0\n0;1.5;5\n0;2;10\n';
    const converted = freeformFromProfileCsv(csv, designForFamily('OSSE'));
    expect(converted.length).toBe(100);
    expect(converted.profile_h!.points).toEqual([{ t: 0, r: 10 }, { t: .5, r: 20 }, { t: 1, r: 30 }]);
    expect(converted.profile_v!.points).toEqual([{ t: 0, r: 10 }, { t: .5, r: 15 }, { t: 1, r: 20 }]);
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
