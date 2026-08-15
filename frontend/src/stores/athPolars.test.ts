import { describe, expect, it } from 'vitest';
import {
  designWireWithAthPolars,
  polarUiFromAthBlocks,
  replaceAthPolarBlocks,
} from './athPolars';

describe('ATH directivity config compatibility', () => {
  it('reads old ATH H/V blocks, including the historical 270 degree vertical plane', () => {
    const polar = polarUiFromAthBlocks({
      'ABEC.Polars:SPL_H': {
        items: { MapAngleRange: '0,180,37', Distance: '2', NormAngle: '10' },
        lines: [],
      },
      'ABEC.Polars:SPL_V': {
        items: { MapAngleRange: '0,180,37', Distance: '2', Inclination: '270', NormAngle: '10' },
        lines: [],
      },
    });

    expect(polar).toEqual({
      angleStart: 0,
      angleEnd: 180,
      angleStep: 5,
      distance: 2,
      normAngle: 10,
      diagonalAngle: 45,
      enabledAxes: ['horizontal', 'vertical'],
      observationOrigin: 'mouth',
      sphericalSampling: false,
    });
  });

  it('writes the v1 canonical blocks while preserving unrelated ATH passthrough', () => {
    const blocks = replaceAthPolarBlocks({
      'ABEC.Polars:OLD': { items: { Future: 'discard managed block' }, lines: [] },
      'ABEC.Mesh': { items: { MeshFrequency: '1000' }, lines: [] },
      Report: { items: { Title: '"Tritonia"', PolarData: 'SPL_H' }, lines: [] },
    }, {
      angle_range: [-30, 90, 13],
      distance: 3,
      norm_angle: 7,
      inclination: 30,
      enabled_axes: ['horizontal', 'diagonal'],
    });

    expect(blocks).toEqual({
      'ABEC.Mesh': { items: { MeshFrequency: '1000' }, lines: [] },
      Report: { items: { Title: '"Tritonia"', PolarData: 'SPL_H' }, lines: [] },
      'ABEC.Polars:SPL_H': {
        items: { MapAngleRange: '-30,90,13', NormAngle: '7', Distance: '3' },
        lines: [], comments: [], entries: [],
      },
      'ABEC.Polars:SPL_D': {
        items: { MapAngleRange: '-30,90,13', NormAngle: '7', Distance: '3', Inclination: '30' },
        lines: [], comments: [], entries: [],
      },
    });
  });

  it('keeps equivalent imported polar blocks byte-oriented instead of canonicalizing them', () => {
    const imported = {
      'ABEC.Polars:SPL_H': {
        items: { Distance: '2', MapAngleRange: '0,180,37', NormAngle: '10', AthExtension: 'kept' },
        lines: [], entries: ['Distance = 2', '; old comment', 'MapAngleRange = 0,180,37', 'NormAngle = 10', 'AthExtension = kept'],
      },
      'ABEC.Polars:SPL_V': {
        items: { Distance: '2', Inclination: '270', MapAngleRange: '0,180,37', NormAngle: '10' },
        lines: [],
      },
    };
    expect(replaceAthPolarBlocks(imported, {
      angle_range: [0, 180, 37], distance: 2, norm_angle: 10,
      inclination: 45, enabled_axes: ['horizontal', 'vertical'],
    })).toBe(imported);
  });

  it('overlays a run snapshot without changing it when stored polar data is unusable', () => {
    const design = { formula: 'OSSE', extra_blocks: { Report: { items: {}, lines: [] } } };
    expect(designWireWithAthPolars(design, null)).toBe(design);
    expect(designWireWithAthPolars(design, { angle_range: [0, 180, 37] })).toBe(design);
  });
});
