import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import {
  PARAMETER_REGISTRY,
  TRACEABILITY_PARAMETER_INVENTORY,
  fieldIsVisible,
  traceEntryIsRegistered,
} from './parameterRegistry';

// Authoritative parameterInventory.js list, intentionally duplicated here so
// additions/removals in the production registry cannot silently weaken this test.
const TRACEABILITY_KEYS = {
  'R-OSSE': ['scale', 'R', 'a', 'a0', 'r0', 'k', 'm', 'b', 'r', 'q', 'tmax'],
  OSSE: ['scale', 'L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h'],
  ICW: ['scale', 'r0', 'a0', 'L', 'R', 'coverage_angle', 'hold_start', 'hold_end', 'n_coeff', 'termination', 'theta1_deg', 'depth'],
  FREEFORM: ['scale', 'length', 'throatRadius', 'throatAngle', 'mouthRadiusH', 'mouthAngleH', 'interiorH', 'throatTangentScaleH', 'mouthTangentScaleH', 'mouthRadiusV', 'mouthAngleV', 'interiorV', 'throatTangentScaleV', 'mouthTangentScaleV', 'crossSections', 'overshootPolicy', 'inflectionPolicy'],
  COMMON: [
    'throatExtAngle', 'throatExtLength', 'slotLength',
    'morphTarget', 'morphWidth', 'morphHeight', 'morphCorner', 'morphRate', 'morphFixed', 'morphAllowShrinkage',
    'wallThickness', 'encDepth', 'encEdge', 'encEdgeType', 'encSpaceL', 'encSpaceT', 'encSpaceR', 'encSpaceB',
    'throatProfile', 'rot', 'gcurveType', 'gcurveDist', 'gcurveWidth', 'gcurveAspectRatio', 'gcurveSeN', 'gcurveSf',
    'gcurveSfA', 'gcurveSfB', 'gcurveSfM1', 'gcurveSfM2', 'gcurveSfN1', 'gcurveSfN2', 'gcurveSfN3', 'gcurveRot', 'circArcTermAngle', 'circArcRadius',
    'angularSegments', 'lengthSegments', 'cornerSegments', 'throatSegments', 'throatSliceDensity',
    'freqStart', 'freqEnd', 'numFreqs', 'sourceShape', 'sourceRadius', 'sourceCurv', 'sourceVelocity',
    'simType', 'solverMode', 'throatResolution', 'mouthResolution', 'rearResolution', 'apertureResolutionScale',
    'maxTriangles', 'allowLargeMesh', 'verticalOffset', 'quadrants', 'encFrontResolution', 'encBackResolution',
  ],
} as const;

describe('complete parameter registry', () => {
  it('covers every one of the 110 family-qualified traceability entries with no deferrals', () => {
    const encodedCount = Object.values(TRACEABILITY_KEYS).reduce<number>((count, keys) => count + keys.length, 0);
    expect(encodedCount).toBe(110);
    expect(TRACEABILITY_PARAMETER_INVENTORY).toHaveLength(110);
    expect(TRACEABILITY_PARAMETER_INVENTORY.every(traceEntryIsRegistered)).toBe(true);

    for (const [family, keys] of Object.entries(TRACEABILITY_KEYS)) {
      for (const key of keys) {
        expect(PARAMETER_REGISTRY.some((field) => field.legacyKey === key && (
          family === 'COMMON' || !field.families || field.families.includes(family as 'R-OSSE' | 'OSSE' | 'ICW' | 'FREEFORM')
        )), `${family}.${key}`).toBe(true);
      }
    }
  });

  it('applies profile and guiding controls to the correct family', () => {
    const visibleIds = (family: 'R-OSSE' | 'OSSE' | 'ICW' | 'FREEFORM') => PARAMETER_REGISTRY
      .filter((field) => fieldIsVisible(field, designForFamily(family)))
      .map((field) => field.id);

    expect(visibleIds('R-OSSE')).toContain('rosse.R');
    expect(visibleIds('R-OSSE')).not.toContain('osse.L');
    expect(visibleIds('OSSE')).toContain('guide.curve_type');
    expect(visibleIds('ICW')).toContain('icw.termination');
    expect(visibleIds('ICW')).not.toContain('icw.theta1_deg');
    expect(visibleIds('FREEFORM')).toContain('freeform.crossSections');
    expect(visibleIds('FREEFORM')).not.toContain('morph.target_shape');
  });

  it('mirrors ICW rollback, coverage, OSSE guide, and Z-map mode visibility', () => {
    const icw = designForFamily('ICW');
    const visible = (id: string, design = icw) => fieldIsVisible(PARAMETER_REGISTRY.find((field) => field.id === id)!, design);
    expect(visible('icw.hold_start')).toBe(false);
    icw.coverage_angle = 40;
    expect(visible('icw.hold_start')).toBe(true);
    icw.termination = 'rollback';
    expect(visible('icw.hold_start')).toBe(false);
    expect(visible('icw.theta1_deg')).toBe(true);

    const osse = designForFamily('OSSE');
    expect(visible('guide.superellipse_n', osse)).toBe(false);
    osse.guiding_curve.curve_type = 1;
    expect(visible('guide.superellipse_n', osse)).toBe(true);
    osse.mesh.sampling_mode = 'zmap';
    expect(visible('mesh.z_map_points', osse)).toBe(true);
  });

  it('offers only supported sampling modes and maps legacy Source.Velocity to the numeric field', () => {
    const sampling = PARAMETER_REGISTRY.find((field) => field.id === 'mesh.sampling_mode')!;
    expect(sampling.options?.map((option) => option.value)).toEqual(['uniform', 'ath-default-zmap', 'zmap']);
    expect(PARAMETER_REGISTRY.find((field) => field.legacyKey === 'sourceVelocity')?.id).toBe('source.velocity');
  });
});
