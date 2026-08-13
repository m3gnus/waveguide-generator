import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import {
  PARAMETER_REGISTRY,
  PARAMETER_SECTION_DEFINITIONS,
  TRACEABILITY_PARAMETER_INVENTORY,
  EXPRESSION_PARAMETER_IDS,
  fieldIsVisible,
  fieldMatchesQuery,
  parameterSectionIsVisible,
  traceEntryIsRegistered,
} from './parameterRegistry';

// Authoritative parameterInventory.js list, intentionally duplicated here so
// additions/removals in the production registry cannot silently weaken this test.
const TRACEABILITY_KEYS = {
  'R-OSSE': ['scale', 'R', 'a', 'a0', 'r0', 'k', 'm', 'b', 'r', 'q', 'tmax'],
  OSSE: ['scale', 'L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h'],
  ICW: ['scale', 'r0', 'a0', 'L', 'R', 'coverage_angle', 'hold_start', 'hold_end', 'n_coeff', 'termination', 'theta1_deg', 'depth'],
  FREEFORM: ['scale', 'length', 'throatRadius', 'throatAngle', 'mouthRadiusH', 'mouthAngleH', 'interiorH', 'mouthRadiusV', 'mouthAngleV', 'interiorV', 'crossSections', 'inflectionPolicy'],
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
  it('covers every current family-qualified traceability entry with no deferrals', () => {
    const encodedCount = Object.values(TRACEABILITY_KEYS).reduce<number>((count, keys) => count + keys.length, 0);
    expect(encodedCount).toBe(105);
    expect(TRACEABILITY_PARAMETER_INVENTORY).toHaveLength(105);
    expect(TRACEABILITY_PARAMETER_INVENTORY.every(traceEntryIsRegistered)).toBe(true);

    for (const [family, keys] of Object.entries(TRACEABILITY_KEYS)) {
      for (const key of keys) {
        expect(PARAMETER_REGISTRY.some((field) => field.legacyKey === key && (
          family === 'COMMON' || !field.families || field.families.includes(family as 'R-OSSE' | 'OSSE' | 'ICW' | 'FREEFORM')
        )), `${family}.${key}`).toBe(true);
      }
    }
  });

  it('assigns every registry field to exactly one Geometry or Simulation tab', () => {
    expect(new Set(PARAMETER_SECTION_DEFINITIONS.map(({ title }) => title)).size).toBe(PARAMETER_SECTION_DEFINITIONS.length);
    for (const field of PARAMETER_REGISTRY) {
      const owners = PARAMETER_SECTION_DEFINITIONS.filter(({ title }) => title === field.section);
      expect(owners, field.id).toHaveLength(1);
      expect(['geometry', 'simulation']).toContain(owners[0].tab);
    }

    const sectionFor = (id: string) => PARAMETER_REGISTRY.find((field) => field.id === id)?.section;
    // The segment counts size the exported and solved mesh; the viewport
    // overrides them with its own adaptive sampling, so they must not sit in a
    // section that promises live preview control.
    expect(sectionFor('mesh.angular_segments')).toBe('Surface sampling');
    expect(sectionFor('mesh.length_segments')).toBe('Surface sampling');
    expect(sectionFor('mesh.corner_segments')).toBe('Surface sampling');
    // Inert ATH keys: the mesher rejects Mesh.ThroatSegments and never reads a
    // slice density, so they belong with the other round-trip-only values.
    expect(sectionFor('mesh.throat_segments')).toBe('Output & Passthrough');
    expect(sectionFor('mesh.throat_slice_density')).toBe('Output & Passthrough');
    expect(sectionFor('mesh.throat_resolution')).toBe('Solve & export mesh');
    const triangleWarning = PARAMETER_REGISTRY.find((field) => field.id === 'mesh.max_triangles')!;
    expect(triangleWarning.label).toBe('Triangle warning threshold');
    const legacyApproval = PARAMETER_REGISTRY.find((field) => field.id === 'mesh.allow_large_mesh')!;
    expect(fieldIsVisible(legacyApproval, designForFamily('OSSE'))).toBe(false);
    expect(sectionFor('mesh.wall_thickness')).toBe('Wall & Enclosure');
    expect(sectionFor('mesh.quadrants')).toBe('Solve & export mesh');
    expect(sectionFor('source.shape')).toBe('Source Definition');
  });

  it('uses a mode-and-design predicate for section visibility', () => {
    const design = designForFamily('OSSE');
    const visible = (title: string, mode: 'parametric' | 'cad') => parameterSectionIsVisible(
      PARAMETER_SECTION_DEFINITIONS.find((section) => section.title === title)!, mode, design,
    );
    expect(visible('Profile Dimensions', 'cad')).toBe(true);
    expect(visible('Surface sampling', 'parametric')).toBe(true);
    expect(visible('Surface sampling', 'cad')).toBe(false);
    expect(visible('Source Definition', 'cad')).toBe(false);
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
    expect(visibleIds('FREEFORM')).toContain('freeform.inflectionPolicy');
    expect(visibleIds('FREEFORM').some((id) => id.includes('TangentScale') || id.includes('overshoot'))).toBe(false);
    expect(visibleIds('FREEFORM')).not.toContain('morph.target_shape');
    const length = PARAMETER_REGISTRY.find((field) => field.id === 'freeform.length')!;
    expect(length.path).toBe('length');
    expect(length.mirrorPaths).toBeUndefined();
  });

  it('mirrors ICW rollback, coverage, OSSE guide, and Z-map mode visibility', () => {
    const icw = designForFamily('ICW');
    const visible = (id: string, design = icw) => fieldIsVisible(PARAMETER_REGISTRY.find((field) => field.id === id)!, design);
    expect(visible('icw.hold_start')).toBe(true);
    icw.coverage_angle = 40;
    expect(visible('icw.hold_start')).toBe(true);
    icw.termination = 'rollback';
    expect(visible('icw.hold_start')).toBe(false);
    expect(visible('icw.L')).toBe(false);
    expect(visible('icw.theta1_deg')).toBe(true);

    const osse = designForFamily('OSSE');
    expect(visible('guide.superellipse_n', osse)).toBe(false);
    osse.guiding_curve.curve_type = 1;
    expect(visible('guide.superellipse_n', osse)).toBe(true);
    osse.mesh.sampling_mode = 'zmap';
    expect(visible('mesh.z_map_points', osse)).toBe(true);
  });

  it('offers only supported sampling modes and maps legacy Source.Velocity to its enum', () => {
    const sampling = PARAMETER_REGISTRY.find((field) => field.id === 'mesh.sampling_mode')!;
    expect(sampling.options?.map((option) => option.value)).toEqual(['uniform', 'ath-default-zmap', 'zmap']);
    expect(PARAMETER_REGISTRY.find((field) => field.legacyKey === 'sourceVelocity')?.id).toBe('source.velocity_convention');
  });

  it('marks all 43 daggered rows expression-capable, with tuple support for both enclosure resolutions', () => {
    const tupleIds = new Set(['enclosure.front_resolution', 'enclosure.back_resolution']);
    expect([...EXPRESSION_PARAMETER_IDS].filter((id) => !tupleIds.has(id))).toHaveLength(43);
    expect([...tupleIds].every((id) => EXPRESSION_PARAMETER_IDS.has(id))).toBe(true);
  });

  it('keeps solve-mesh preconfiguration visible while outer-body fields follow the explicit mode', () => {
    const osse = designForFamily('OSSE');
    osse.guiding_curve.curve_type = 1;
    osse.morph.target_shape = 0;
    osse.enclosure.depth = 0;
    osse.mesh.wall_thickness = 0;
    for (const id of ['osse.a', 'morph.target_width', 'morph.target_height', 'morph.corner_radius', 'morph.rate', 'morph.fixed_part', 'enclosure.front_resolution', 'mesh.rear_resolution']) {
      const field = PARAMETER_REGISTRY.find((item) => item.id === id)!;
      expect(fieldIsVisible(field, osse), id).toBe(true);
      expect(field.disabledWhen?.(osse), id).toBeUndefined();
    }
    expect(fieldIsVisible(PARAMETER_REGISTRY.find((item) => item.id === 'enclosure.space_l')!, osse)).toBe(false);
    expect(fieldIsVisible(PARAMETER_REGISTRY.find((item) => item.id === 'mesh.wall_thickness')!, osse)).toBe(false);
  });

  it('labels every OSSE and R-OSSE profile scalar with its ATH formula symbol', () => {
    // An ATH user reads the formula, not our prose, so each profile scalar has
    // to carry the letter the formula uses -- which is exactly its legacy key.
    for (const family of ['R-OSSE', 'OSSE'] as const) {
      const scalars = PARAMETER_REGISTRY
        .filter((item) => item.section === 'Profile Dimensions' && item.families?.includes(family) && item.id !== 'common.scale');
      expect(scalars.map((item) => item.symbol)).toEqual(TRACEABILITY_KEYS[family].filter((key) => key !== 'scale'));
      expect(scalars.every((item) => item.symbol === item.legacyKey), family).toBe(true);
    }
  });

  // Every row in the panel is hover-documented. A parameter with no description
  // renders a bare label the user has no way to interpret, so this is a gate
  // rather than a nice-to-have.
  it('gives every parameter hover help that says more than its own label', () => {
    const undocumented = PARAMETER_REGISTRY.filter((item) => !item.description?.trim());
    expect(undocumented.map((item) => item.id)).toEqual([]);

    // "Superformula a parameter" under a label reading "Superformula a" is the
    // failure mode worth catching: text that only restates the label.
    const hollow = PARAMETER_REGISTRY.filter((item) => {
      const description = item.description!.toLocaleLowerCase().replace(/[^a-z0-9 ]/g, '');
      return description.replace(item.label.toLocaleLowerCase(), '').trim().split(/\s+/).length < 5;
    });
    expect(hollow.map((item) => item.id)).toEqual([]);
  });

  it('finds a profile scalar by its ATH symbol alone', () => {
    expect(PARAMETER_REGISTRY.filter((item) => fieldMatchesQuery(item, 'tmax')).map((item) => item.id)).toEqual(['rosse.tmax']);
  });

  it('documents ATH\'s omitted wall-thickness default', () => {
    expect(PARAMETER_REGISTRY.find((item) => item.id === 'mesh.wall_thickness')?.description).toContain('5 mm');
  });

  it('explains how the throat extension relates to each formula\'s a0 control', () => {
    const description = (id: string) => PARAMETER_REGISTRY.find((item) => item.id === id)?.description;

    expect(description('osse.a0')).toContain('meet tangentially');
    expect(description('rosse.a0')).toContain('does not guarantee a tangent join');
    expect(description('common.throat_ext_angle')).toContain('independent of a0');
    expect(description('common.throat_ext_length')).toContain('does not change the computed OS-SE or R-OSSE flare');
  });
});
