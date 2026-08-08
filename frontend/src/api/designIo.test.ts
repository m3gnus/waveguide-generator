import { describe, expect, it } from 'vitest';
import { downloadGeometryExport, hydrateDesignDocument } from './designIo';
import { serializeDesign } from '../stores/design';

describe('design hydration', () => {
  it('decodes ATH quadrant digits and derives custom zmap sampling', () => {
    const design = hydrateDesignDocument({
      formula: 'OSSE',
      mesh: { quadrants: 14, sampling_mode: null, z_map_points: '0,.2,1' },
    });
    expect(design.quadrants).toEqual([1, 4]);
    expect(design.mesh.quadrants).toBe(14);
    expect(design.mesh.sampling_mode).toBe('zmap');
  });

  it('uses family defaults for nullable imported numeric fields', () => {
    const design = hydrateDesignDocument({
      formula: 'R-OSSE', R: null,
      source: { radius: null, velocity: null },
      simulation: { f1: null },
    });
    expect(design.R).toBe(140);
    expect(design.source.radius).toBe(-1);
    expect(design.source.velocity).toBe(1);
    expect(design.simulation.f1).toBe(400);
    expect(design._absent).toEqual(expect.arrayContaining(['R', 'source.radius', 'source.velocity', 'simulation.f1']));
  });

  it('keeps an absent ATH key null on the wire while displaying its family default', () => {
    const design = hydrateDesignDocument({ formula: 'R-OSSE', morph: { target_shape: 1, target_width: null } });
    expect(design.morph.target_width).toBe(0);
    expect(design._absent).toContain('morph.target_width');
    expect((serializeDesign(design).morph as Record<string, unknown>).target_width).toBeNull();
    expect(serializeDesign(design)).not.toHaveProperty('_absent');
  });

  it('displays ATH\'s wall default while keeping an absent wall thickness null on the wire', () => {
    const design = hydrateDesignDocument({ formula: 'R-OSSE', mesh: { wall_thickness: null } });
    expect(design.mesh.wall_thickness).toBe(5);
    expect(design._absent).toContain('mesh.wall_thickness');
    expect((serializeDesign(design).mesh as Record<string, unknown>).wall_thickness).toBeNull();
  });

  it('preserves raw and evaluated expression forms through hydrate/serialize', () => {
    const design = hydrateDesignDocument({ formula: 'R-OSSE', R: { value: 280, raw: '140 * 2' }, a: { value: null, raw: 'coverage(p)' } });
    expect(design.R).toBe(280);
    expect(design.a).toBe(25);
    expect(design._expressions).toMatchObject({ R: { value: 280, raw: '140 * 2' }, a: { value: null, raw: 'coverage(p)' } });
    expect(serializeDesign(design)).toMatchObject({ R: { value: 280, raw: '140 * 2' }, a: { value: null, raw: 'coverage(p)' } });
  });
});

describe('geometry export requests', () => {
  function recordingFetcher(): { urls: string[]; fetcher: typeof fetch } {
    const urls: string[] = [];
    const fetcher = (async (url: string) => {
      urls.push(String(url));
      return new Response(new Blob(['ISO-10303-21;']), {
        status: 200,
        headers: { 'Content-Type': 'model/step' },
      });
    }) as unknown as typeof fetch;
    return { urls, fetcher };
  }

  const design = hydrateDesignDocument({ formula: 'OSSE', L: 120, a: 45 });

  it('asks for the manufacturable solid by default', async () => {
    const { urls, fetcher } = recordingFetcher();
    await downloadGeometryExport('step', design, 3, 'horn', undefined, undefined, fetcher);
    expect(urls).toEqual(['/api/export/step?body=solid']);
  });

  it('asks for the inner surface when that body is chosen', async () => {
    const { urls, fetcher } = recordingFetcher();
    await downloadGeometryExport('step', design, 3, 'horn', undefined, 'surface', fetcher);
    expect(urls).toEqual(['/api/export/step?body=surface']);
  });

  it('leaves the other geometry exports unqueried', async () => {
    const { urls, fetcher } = recordingFetcher();
    await downloadGeometryExport('stl', design, 3, 'horn', undefined, undefined, fetcher);
    await downloadGeometryExport('profiles', design, 3, 'horn', 'slices', undefined, fetcher);
    expect(urls).toEqual(['/api/export/stl', '/api/export/profiles?kind=slices']);
  });
});
