import { describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from './designIo';
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
  });

  it('preserves raw and evaluated expression forms through hydrate/serialize', () => {
    const design = hydrateDesignDocument({ formula: 'R-OSSE', R: { value: 280, raw: '140 * 2' }, a: { value: null, raw: 'coverage(p)' } });
    expect(design.R).toBe(280);
    expect(design.a).toBe(25);
    expect(design._expressions).toMatchObject({ R: { value: 280, raw: '140 * 2' }, a: { value: null, raw: 'coverage(p)' } });
    expect(serializeDesign(design)).toMatchObject({ R: { value: 280, raw: '140 * 2' }, a: { value: null, raw: 'coverage(p)' } });
  });
});
