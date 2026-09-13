import { describe, expect, it } from 'vitest';
import { convertDesignToFreeform, freeformFromProfileCsv } from '../api/designIo';
import { designForFamily } from '../stores/design';
import { normalizedImportedPoints, parsePointPaste, withStationShape } from './FreeformEditors';

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
    expect(() => parsePointPaste('0 12.7\n50 30\n50 40\n120 140')).toThrow('strictly increasing');
  });

  it('uses a server converter when available and falls back to endpoint-preserving conversion on older servers', async () => {
    const design = designForFamily('OSSE');
    design.simulation.f1 = 250;
    const fallbackFetch = async () => new Response('', { status: 404 });
    const { design: fallback } = await convertDesignToFreeform(design, fallbackFetch as typeof fetch);
    expect(fallback.formula).toBe('FREEFORM');
    expect(fallback.length).toBe(design.L);
    expect(fallback.profile_h!.points).toEqual([{ t: 0, r: design.r0 }, { t: 1, r: 140 }]);
    expect(fallback.simulation.f1).toBe(250);

    const converted = designForFamily('FREEFORM');
    converted.profile_h!.points[1].r = 222;
    const serverFetch = async () => new Response(JSON.stringify({ design: converted }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    expect((await convertDesignToFreeform(design, serverFetch as typeof fetch)).design.profile_h!.points.at(-1)!.r).toBe(222);
  });

  it('converts the existing server profile export into distinct H/V editable meridians', () => {
    const csv = '# x_cm;y_cm;z_cm\n1;0;0\n2;0;5\n3;0;10\n\n0;1;0\n0;1.5;5\n0;2;10\n';
    const { design: converted } = freeformFromProfileCsv(csv, designForFamily('OSSE'));
    expect(converted.length).toBe(100);
    expect(converted.profile_h!.points).toEqual([{ t: 0, r: 10 }, { t: .5, r: 20 }, { t: 1, r: 30 }]);
    expect(converted.profile_v!.points).toEqual([{ t: 0, r: 10 }, { t: .5, r: 15 }, { t: 1, r: 20 }]);
  });

  describe('converting a morphed mouth', () => {
    // An axisymmetric horn 100 mm long flaring from 10 to 80 mm, written the way
    // the profile export writes it: one x;y;z section in cm per meridian.
    const plainRadius = (u: number) => 10 + 70 * u * u;
    const exportCsv = (radius: (phi: number, u: number) => number, meridians: number) => {
      const lines = ['# x_cm;y_cm;z_cm'];
      for (let index = 0; index < meridians; index += 1) {
        const phi = 2 * Math.PI * index / meridians;
        for (let row = 0; row <= 20; row += 1) {
          const u = row / 20;
          const r = radius(phi, u);
          lines.push(`${(r * Math.cos(phi) / 10).toFixed(6)};${(r * Math.sin(phi) / 10).toFixed(6)};${(10 * u).toFixed(6)}`);
        }
        lines.push('');
      }
      return `${lines.join('\r\n')}\r\n`;
    };
    const plainCsv = exportCsv((_phi, u) => plainRadius(u), 40);
    const rectangle = (phi: number) => Math.min(180 / Math.abs(Math.cos(phi)), 110 / Math.abs(Math.sin(phi)));
    const superellipse = (phi: number) => ((Math.abs(Math.cos(phi)) / 180) ** 6 + (Math.abs(Math.sin(phi)) / 110) ** 6) ** (-1 / 6);
    // Fixed part 0 and rate 3, on the export's own corner-aware angle list.
    const morphedCsv = (target: (phi: number) => number) => exportCsv((phi, u) => plainRadius(u) + u ** 3 * (target(phi) - plainRadius(1)), 44);

    const morphed = (targetShape: number, changes: Partial<ReturnType<typeof designForFamily>['morph']> = {}) => {
      const design = designForFamily('R-OSSE');
      design.morph = { ...design.morph, target_shape: targetShape, target_width: 360, target_height: 220, ...changes };
      return design;
    };
    const convert = async (design: ReturnType<typeof designForFamily>, morphedExport: string) => {
      const profileRequests: number[] = [];
      const fetcher = async (url: string, init?: RequestInit) => {
        if (url.startsWith('/api/design/convert')) return new Response('', { status: 405 });
        const target = (JSON.parse(String(init?.body)) as { design: { morph: { target_shape: number } } }).design.morph.target_shape;
        profileRequests.push(target);
        return new Response(target === 0 ? plainCsv : morphedExport, { status: 200 });
      };
      return { converted: (await convertDesignToFreeform(design, fetcher as typeof fetch)).design, profileRequests };
    };

    it('reproduces a Rectangle morph with a rounded-rectangle mouth station and leaves morph off', async () => {
      const { converted, profileRequests } = await convert(morphed(1, { corner_radius: 10 }), morphedCsv(rectangle));
      expect(profileRequests).toEqual([1, 0]);
      expect(converted.morph.target_shape).toBe(0);
      expect(converted.profile_h!.points.at(-1)!.r).toBeCloseTo(180, 3);
      expect(converted.profile_v!.points.at(-1)!.r).toBeCloseTo(110, 3);
      const stations = converted.cross_sections!;
      expect(stations[0]).toEqual({ t: 0, shape: 'ellipse' });
      // The u^3 blend reaches half at t = 0.794, where the smootherstep from
      // the blend-start ellipse to the mouth reaches half too.
      expect(stations[1].shape).toBe('ellipse');
      expect(stations[1].t).toBeCloseTo(.587, 2);
      expect(stations.at(-1)).toEqual({ t: 1, shape: 'rounded_rectangle', corner_radius_mm: 10 });
      // A small corner is reached through a rounder rectangle midway.
      expect(stations[2].shape).toBe('rounded_rectangle');
      expect(stations[2].t).toBeCloseTo((stations[1].t + 1) / 2, 4);
      expect(stations[2].corner_radius_mm).toBeGreaterThan(10);
    });

    it('raises a sharp Rectangle corner to the smallest station corner the mesher accepts', async () => {
      const { converted } = await convert(morphed(1, { corner_radius: 0 }), morphedCsv(rectangle));
      // 2% of the 110 mm half-height.
      expect(converted.cross_sections!.at(-1)).toEqual({ t: 1, shape: 'rounded_rectangle', corner_radius_mm: 2.2 });
    });

    it('takes a large Rectangle corner straight to the mouth', async () => {
      const { converted } = await convert(morphed(1, { corner_radius: 80 }), morphedCsv(rectangle));
      expect(converted.cross_sections!.map((station) => station.shape)).toEqual(['ellipse', 'ellipse', 'rounded_rectangle']);
      expect(converted.cross_sections!.at(-1)!.corner_radius_mm).toBe(80);
    });

    it('carries a Superellipse morph exponent into the mouth station', async () => {
      const design = morphed(3);
      (design.morph as typeof design.morph & { target_exponent?: number }).target_exponent = 6;
      const { converted } = await convert(design, morphedCsv(superellipse));
      expect(converted.morph.target_shape).toBe(0);
      expect(converted.cross_sections!.at(-1)).toEqual({ t: 1, shape: 'superellipse', exponent: 6 });
      expect(converted.cross_sections!.map((station) => station.shape)).toEqual(['ellipse', 'ellipse', 'superellipse']);
    });

    it('converts an inactive or circular morph as before, without a second export', async () => {
      for (const design of [morphed(0), morphed(1, { fixed_part: 1 }), morphed(2)]) {
        const { converted, profileRequests } = await convert(design, plainCsv);
        expect(profileRequests).toHaveLength(1);
        expect(converted.cross_sections).toEqual([{ t: 0, shape: 'ellipse' }, { t: 1, shape: 'ellipse' }]);
        expect(converted.morph.target_shape).toBe(0);
      }
    });

    it('keeps ellipse stations when the morph moves no part of the mouth', async () => {
      const { converted, profileRequests } = await convert(morphed(1, { corner_radius: 10 }), plainCsv);
      expect(profileRequests).toEqual([1, 0]);
      expect(converted.cross_sections).toEqual([{ t: 0, shape: 'ellipse' }, { t: 1, shape: 'ellipse' }]);
    });
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
