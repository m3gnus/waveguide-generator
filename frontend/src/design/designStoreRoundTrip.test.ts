import { beforeEach, describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore, type DesignDocument } from '../stores/design';

describe('full DesignConfig client store', () => {
  beforeEach(() => resetDesignStore());

  it('round-trips a fully populated design without dropping nested or passthrough data', () => {
    const populated: DesignDocument = {
      ...designForFamily('OSSE'),
      scale: 1.25,
      throat_ext_angle: 4,
      throat_ext_length: 12,
      slot_length: 3,
      length_mode: 'total',
      coverage_mode: 'explicit',
      guiding_curve: {
        curve_type: 2, distance: .75, width: 280, aspect_ratio: .6,
        superellipse_n: 3.5, superformula: 1, sf_a: 1.1, sf_b: 1.2,
        sf_m1: 5, sf_m2: 6, sf_n1: .7, sf_n2: .8, sf_n3: .9, rotation: 12,
      },
      morph: {
        target_shape: 1, target_width: 320, target_height: 210,
        corner_radius: 24, rate: 4, fixed_part: .2, allow_shrinkage: 1,
      },
      source: {
        shape: 1, radius: 13, curvature: -1, velocity: .75,
        contours: 'contours/source.txt', velocity_convention: 'axial',
      },
      quadrants: [1, 4],
      enclosure: {
        depth: 400, edge_radius: 22, edge_type: 2,
        space_l: 20, space_t: 21, space_r: 22, space_b: 23,
        front_resolution: 8, back_resolution: 12, baffle_margin: 20,
      },
      mesh: {
        angular_segments: 128, corner_segments: 12, throat_segments: 8,
        length_segments: 96, throat_resolution: 2, mouth_resolution: 2.5,
        throat_slice_density: .35, sampling_mode: 'ath-default-zmap',
        z_map_points: '0,.05,.2,.5,1', vertical_offset: 14, quadrants: 9,
        wall_thickness: 6, rear_resolution: 9, aperture_resolution_scale: 1.2,
        max_triangles: 250_000, allow_large_mesh: 1,
      },
      simulation: {
        f1: 200, f2: 18_000, num_frequencies: 81,
        sim_type: 'infinite-baffle', solver_mode: 'full_3d',
      },
      output: { stl: 1, msh: 1 },
      extra_keys: { 'Vendor.Custom': 'preserved' },
      extra_blocks: {
        'ABEC.Polars:SPL_H': { items: { MapAngleRange: '0,180,5' }, lines: [], comments: ['; keep'] },
        Report: { items: { Title: '"complete"' }, lines: ['free form row'] },
      },
    };
    const wireCopy = JSON.parse(JSON.stringify(populated)) as DesignDocument;
    useDesignStore.getState().loadDesign(wireCopy);
    expect(useDesignStore.getState().design).toEqual(populated);
    expect(useDesignStore.getState().design).not.toBe(wireCopy);
  });

  it('bumps the revision once for a mirrored FREEFORM scalar commit', () => {
    useDesignStore.getState().setFamily('FREEFORM');
    const before = useDesignStore.getState().designRevision;
    useDesignStore.getState().updateValues({ 'profile_h.points.1.z': 180, 'profile_v.points.1.z': 180 });
    expect(useDesignStore.getState().design.profile_h?.points[1].z).toBe(180);
    expect(useDesignStore.getState().design.profile_v?.points[1].z).toBe(180);
    expect(useDesignStore.getState().designRevision).toBe(before + 1);
  });

  it('round-trips the current FREEFORM point and station shapes', () => {
    const design = designForFamily('FREEFORM');
    design.profile_h!.points = [{ z: 0, r: 12.7 }, { z: 60, r: 70, angle_deg: 25 }, { z: 120, r: 140 }];
    design.cross_sections = [{ t: 0, shape: 'ellipse' }, { t: .5, shape: 'superellipse', exponent: 4 }, { t: 1, shape: 'ellipse' }];
    useDesignStore.getState().loadDesign(design);
    const payload = serializeDesign(useDesignStore.getState().design);
    expect(payload.profile_h).toEqual(design.profile_h);
    expect(payload.cross_sections).toEqual(design.cross_sections);
  });

  it('round-trips an absent ATH field as null until the user edits it', () => {
    const hydrated = hydrateDesignDocument({
      formula: 'R-OSSE',
      morph: { target_shape: 1, target_width: null },
    });
    useDesignStore.getState().loadDesign(hydrated);
    expect(useDesignStore.getState().design.morph.target_width).toBe(0);
    expect((serializeDesign(useDesignStore.getState().design).morph as Record<string, unknown>).target_width).toBeNull();

    useDesignStore.getState().updateValue('morph.target_width', 240);
    expect(useDesignStore.getState().design._absent ?? []).not.toContain('morph.target_width');
    expect((serializeDesign(useDesignStore.getState().design).morph as Record<string, unknown>).target_width).toBe(240);
  });
});
