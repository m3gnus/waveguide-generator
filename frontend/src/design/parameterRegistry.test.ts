import { describe, expect, it } from 'vitest';
import { NATIVE_FAMILIES, designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import {
  LEGACY_PARAMETER_REGISTRY,
  PARAMETER_REGISTRY,
  PARAMETER_SECTION_DEFINITIONS,
  TRACEABILITY_PARAMETER_INVENTORY,
  EXPRESSION_PARAMETER_IDS,
  fieldAppliesToFamily,
  fieldIsVisible,
  fieldOptionsForBackend,
  fieldMatchesQuery,
  parameterSectionIsVisible,
  orderSectionFields,
  traceEntryIsRegistered,
} from './parameterRegistry';

// Authoritative parameterInventory.js list, intentionally duplicated here so
// additions/removals in the production registry cannot silently weaken this test.
const TRACEABILITY_KEYS = {
  'R-OSSE': ['scale', 'R', 'a', 'a0', 'r0', 'k', 'm', 'b', 'r', 'q', 'tmax'],
  OSSE: ['scale', 'L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h'],
  ICW: ['scale', 'r0', 'a0', 'L', 'R', 'coverage_angle', 'hold_start', 'hold_end', 'n_coeff', 'termination', 'theta1_deg', 'depth', 'r_aperture', 'x_aperture', 'x_setback', 'kappa0', 'kappa_abs_max', 'dkappa_ds_abs_max', 'theta_max_deg', 'pin_mouth_radius', 'icw_S', 'icw_seed', 'icw_coeffs'],
  LOOKUP: ['scale', 'lookupProfile', 'lookupSourceAngle'],
  FREEFORM: ['scale', 'length', 'throatRadius', 'throatAngle', 'mouthRadiusH', 'mouthAngleH', 'interiorH', 'mouthRadiusV', 'mouthAngleV', 'interiorV', 'crossSections', 'inflectionPolicy'],
  COMMON: [
    'throatExtAngle', 'throatExtLength', 'slotLength',
    'morphTarget', 'morphWidth', 'morphHeight', 'morphExponent', 'morphCorner', 'morphRate', 'morphFixed', 'morphAllowShrinkage',
    'wallThickness', 'encDepth', 'encEdge', 'encEdgeType', 'encSpaceL', 'encSpaceT', 'encSpaceR', 'encSpaceB',
    'throatProfile', 'rot', 'gcurveType', 'gcurveDist', 'gcurveWidth', 'gcurveAspectRatio', 'gcurveSeN', 'gcurveSf',
    'gcurveSfA', 'gcurveSfB', 'gcurveSfM1', 'gcurveSfM2', 'gcurveSfN1', 'gcurveSfN2', 'gcurveSfN3', 'gcurveRot', 'circArcTermAngle', 'circArcRadius',
    'angularSegments', 'lengthSegments', 'cornerSegments', 'throatSegments', 'throatSliceDensity',
    'freqStart', 'freqEnd', 'numFreqs', 'sourceShape', 'sourceRadius', 'sourceCurv', 'sourceVelocity',
    'simType', 'throatResolution', 'mouthResolution', 'rearResolution', 'apertureResolutionScale',
    'maxTriangles', 'allowLargeMesh', 'verticalOffset', 'quadrants', 'encFrontResolution', 'encBackResolution',
  ],
} as const;

describe('complete parameter registry', () => {
  it.each(NATIVE_FAMILIES)('excludes morph, enclosure and simulation type from %s, including search', (family) => {
    const fields = PARAMETER_REGISTRY.filter((field) => field.id.startsWith('morph.')
      || field.id.startsWith('enclosure.') || field.id === 'simulation.sim_type');
    expect(fields.length).toBeGreaterThan(0);
    const design = designForFamily(family);
    design.morph.target_shape = 3;
    design.enclosure.depth = 280;
    design.simulation.sim_type = 'infinite-baffle';
    for (const field of fields) {
      expect(fieldAppliesToFamily(field, family), field.id).toBe(false);
      expect(fieldIsVisible(field, design), field.id).toBe(false);
      for (const legacy of ['OSSE', 'R-OSSE', 'ICW', 'FREEFORM'] as const) {
        expect(fieldAppliesToFamily(field, legacy), `${legacy}: ${field.id}`).toBe(true);
      }
    }
  });

  it('declares every Mesh field and refuses an undeclared field', () => {
    const section = PARAMETER_SECTION_DEFINITIONS.find((item) => item.title === 'Mesh')!;
    const fields = PARAMETER_REGISTRY.filter((field) => field.section === 'Mesh');
    expect(section.fieldOrder).toBeDefined();
    expect(new Set(section.fieldOrder).size).toBe(section.fieldOrder!.length);
    expect(section.fieldOrder!.filter((id) => !id.startsWith('control.'))).toEqual(fields.map((field) => field.id));
    expect(orderSectionFields([...fields].reverse(), section)).toEqual(fields);
    const undeclared = { ...fields[0], id: 'mesh.new_field' };
    expect(() => orderSectionFields([...fields, undeclared], section)).toThrow('missing presentation order for mesh.new_field');
    const omitted = { ...section, fieldOrder: section.fieldOrder!.filter((id) => id !== fields[0].id) };
    expect(() => orderSectionFields(fields, omitted)).toThrow(`missing presentation order for ${fields[0].id}`);
  });

  it('uses Frequency sweep help in parametric mode and preserves legacy catalog help', () => {
    for (const id of ['simulation.f1', 'simulation.f2', 'simulation.num_frequencies']) {
      const editor = PARAMETER_REGISTRY.find((field) => field.id === id)!;
      const legacy = LEGACY_PARAMETER_REGISTRY.find((field) => field.id === id)!;
      expect(editor.description).toContain('Frequency sweep');
      expect(editor.description).not.toContain('Solve options');
      expect(legacy.description).toContain('Solve options');
    }
    expect(PARAMETER_REGISTRY).toHaveLength(LEGACY_PARAMETER_REGISTRY.length);
    expect(new Set(PARAMETER_REGISTRY.map((field) => field.id))).toEqual(new Set(LEGACY_PARAMETER_REGISTRY.map((field) => field.id)));
  });

  it('covers every current family-qualified traceability entry with no deferrals', () => {
    const encodedCount = Object.values(TRACEABILITY_KEYS).reduce<number>((count, keys) => count + keys.length, 0);
    expect(encodedCount).toBe(119);
    expect(TRACEABILITY_PARAMETER_INVENTORY).toHaveLength(119);
    expect(TRACEABILITY_PARAMETER_INVENTORY.every(traceEntryIsRegistered)).toBe(true);

    for (const [family, keys] of Object.entries(TRACEABILITY_KEYS)) {
      for (const key of keys) {
        expect(PARAMETER_REGISTRY.some((field) => field.legacyKey === key && (
          family === 'COMMON' || !field.families || field.families.includes(family as 'R-OSSE' | 'OSSE' | 'ICW' | 'FREEFORM' | 'LOOKUP')
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
    expect(sectionFor('mesh.angular_segments')).toBe('Mesh');
    expect(sectionFor('mesh.length_segments')).toBe('Mesh');
    expect(sectionFor('mesh.corner_segments')).toBe('Mesh');
    // Inert ATH keys: the mesher rejects Mesh.ThroatSegments and never reads a
    // slice density, so they belong with the other round-trip-only values.
    expect(sectionFor('mesh.throat_segments')).toBe('Export');
    expect(sectionFor('mesh.throat_slice_density')).toBe('Export');
    expect(sectionFor('mesh.throat_resolution')).toBe('Mesh');
    const triangleWarning = PARAMETER_REGISTRY.find((field) => field.id === 'mesh.max_triangles')!;
    expect(triangleWarning.label).toBe('Triangle warning threshold');
    const legacyApproval = PARAMETER_REGISTRY.find((field) => field.id === 'mesh.allow_large_mesh')!;
    expect(fieldIsVisible(legacyApproval, designForFamily('OSSE'))).toBe(false);
    expect(sectionFor('mesh.wall_thickness')).toBe('Mounting');
    const storedQuadrants = PARAMETER_REGISTRY.find((field) => field.id === 'mesh.quadrants')!;
    expect(storedQuadrants.section).toBe('Export');
    expect(storedQuadrants.kind).toBe('indicator');
    expect(storedQuadrants.label).toBe('Stored ATH Mesh.Quadrants');
    expect(storedQuadrants.description).toContain('overwrites this stored value from Solve domain');
    expect(storedQuadrants.description).toContain('ignores it for geometry export');
    expect(sectionFor('source.shape')).toBe('Throat');
  });

  it('preserves all document fields, paths, bounds, defaults and family gates across the presentation moves', () => {
    expect(PARAMETER_REGISTRY.map((field) => field.id).sort()).toEqual(LEGACY_PARAMETER_REGISTRY.map((field) => field.id).sort());
    for (const original of LEGACY_PARAMETER_REGISTRY) {
      const moved = PARAMETER_REGISTRY.find((field) => field.id === original.id)!;
      const { section: _oldSection, label: _oldLabel, description: oldDescription, visibleWhen: oldVisibility, ...oldSemantics } = original;
      const { section: _section, label: _label, description, visibleWhen: visibility, ...semantics } = moved;
      expect(semantics, original.id).toEqual(oldSemantics);
      if (original.id === 'simulation.sim_type') {
        // The Mounting select names its own choices instead of the old Free-standing option.
        expect(description).toContain('Infinite baffle');
        expect(description).not.toContain('Free-standing');
      } else if (original.id === 'osse.throat_profile') {
        expect(description).toContain('Circular arc profiles are retained for editing existing designs.');
        expect(description).not.toContain('OSSE-ARC');
        expect(oldDescription).toContain('For a terminating arc on a new design, select OSSE features → Mouth end → Terminating arc.');
      } else {
        expect(description, original.id).toBe(original.section === 'Frequency Sweep' ? oldDescription?.replaceAll('Solve options', 'Frequency sweep') : oldDescription);
      }
      if (moved.section !== 'Imported ATH values') expect(visibility, original.id).toBe(oldVisibility);
    }
  });

  it('shows legacy round-trip fields only for imports or non-default values without changing their paths', () => {
    for (const id of ['icw.a', 'icw.k', 'icw.q', 'icw.curl', 'common.coverage_mode', 'source.contours']) {
      const field = PARAMETER_REGISTRY.find((item) => item.id === id)!;
      const design = designForFamily(id === 'source.contours' ? 'OSSE' : 'ICW');
      expect(field.section, id).toBe('Imported ATH values');
      expect(fieldIsVisible(field, design), id).toBe(false);
      design.text_import_version = 'ath-2026-08c-v1';
      expect(fieldIsVisible(field, design), id).toBe(true);
      delete design.text_import_version;
      const keys = field.path!.split('.');
      const parent = keys.slice(0, -1).reduce<Record<string, unknown>>((value, key) => value[key] as Record<string, unknown>, design as unknown as Record<string, unknown>);
      parent[keys.at(-1)!] = field.kind === 'text' ? 'imported legacy text' : 12.5;
      expect(fieldIsVisible(field, design), id).toBe(true);
      expect(field.disabledReason !== undefined, id).toBe(id === 'icw.curl');
    }
  });

  it('uses a mode-and-design predicate for section visibility', () => {
    const design = designForFamily('OSSE');
    const visible = (title: string, mode: 'parametric' | 'cad', waveguideLinked = true) =>
      parameterSectionIsVisible(
        PARAMETER_SECTION_DEFINITIONS.find((section) => section.title === title)!,
        { mode, design, waveguideLinked },
      );
    expect(visible('Profile', 'cad')).toBe(true);
    expect(visible('Mesh', 'parametric')).toBe(true);
    expect(visible('Mesh', 'cad')).toBe(false);
    expect(visible('Source excitation', 'cad')).toBe(false);
  });

  it('hides the waveguide definition when no WG design stands behind the project', () => {
    const design = designForFamily('OSSE');
    const visible = (title: string, mode: 'parametric' | 'cad', waveguideLinked: boolean) =>
      parameterSectionIsVisible(
        PARAMETER_SECTION_DEFINITIONS.find((section) => section.title === title)!,
        { mode, design, waveguideLinked },
      );
    const waveguideSections = [
      'Profile', 'Throat', 'Mouth', 'Cross-section', 'Mounting',
    ];
    // Geometry authored in CAD has no horn of WG's, so every field in these
    // sections would be an input that cannot move the model.
    waveguideSections.forEach((title) => expect(visible(title, 'cad', false)).toBe(false));
    // A WG-originated project keeps them: they are what you edit before
    // sending the design back to CAD.
    waveguideSections.forEach((title) => expect(visible(title, 'cad', true)).toBe(true));
    // The parametric workspace never depends on a CAD link at all.
    waveguideSections.forEach((title) => expect(visible(title, 'parametric', false)).toBe(true));
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
    expect(visibleIds('FREEFORM')).toContain('morph.target_shape');
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
    expect(visible('morph.target_exponent', osse)).toBe(false);
    osse.morph.target_shape = 3;
    expect(visible('morph.target_exponent', osse)).toBe(true);
    osse.mesh.sampling_mode = 'zmap';
    expect(visible('mesh.z_map_points', osse)).toBe(true);
  });

  it.each(['R-OSSE', 'OSSE', 'ICW', 'FREEFORM'] as const)('shows morph controls only for applicable targets in %s', (family) => {
    const design = designForFamily(family);
    const visible = (id: string) => fieldIsVisible(PARAMETER_REGISTRY.find((field) => field.id === id)!, design);
    for (const shape of [0, 1, 2, 3]) {
      design.morph.target_shape = shape;
      for (const name of ['target_width', 'target_height', 'rate', 'fixed_part', 'allow_shrinkage']) {
        expect(visible(`morph.${name}`), `${shape}: ${name}`).toBe(shape !== 0);
      }
      expect(visible('morph.corner_radius')).toBe(shape === 1);
      expect(visible('morph.target_exponent')).toBe(shape === 3);
    }
  });

  it('preserves Legacy only when held and shows amplitude only for Legacy', () => {
    const field = PARAMETER_REGISTRY.find((entry) => entry.id === 'source.velocity_convention')!;
    const amplitude = PARAMETER_REGISTRY.find((entry) => entry.id === 'source.velocity')!;
    const design = designForFamily('OSSE');
    for (const convention of ['normal', 'axial', 'legacy'] as const) {
      design.source.velocity_convention = convention;
      expect(fieldOptionsForBackend(field, convention, null).map((option) => option.value)).toEqual(
        convention === 'legacy' ? ['normal', 'axial', 'legacy'] : ['normal', 'axial'],
      );
      expect(fieldIsVisible(amplitude, design)).toBe(convention === 'legacy');
    }
  });

  it('offers the legacy circular throat arc only while held, with its controls visible only then', () => {
    const field = PARAMETER_REGISTRY.find((entry) => entry.id === 'osse.throat_profile')!;
    expect(field.description).not.toContain('OSSE-ARC');
    const design = designForFamily('OSSE');
    for (const profile of [1, 3]) {
      design.throat_profile = profile;
      expect(fieldOptionsForBackend(field, profile, null).map((option) => [option.value, option.label])).toEqual(
        profile === 3 ? [[1, 'OS-SE'], [3, 'Circular arc (legacy)']] : [[1, 'OS-SE']],
      );
      for (const id of ['osse.circ_arc_radius', 'osse.circ_arc_term_angle']) {
        expect(fieldIsVisible(PARAMETER_REGISTRY.find((entry) => entry.id === id)!, design)).toBe(profile === 3);
      }
    }
  });

  it('leaves dimensionless throat stretch controls without units', () => {
    for (const id of ['common.s1', 'common.s2']) expect(PARAMETER_REGISTRY.find((field) => field.id === id)?.unit).toBeUndefined();
  });

  it('offers only supported sampling modes and maps legacy Source.Velocity to its enum', () => {
    const sampling = PARAMETER_REGISTRY.find((field) => field.id === 'mesh.sampling_mode')!;
    expect(sampling.options?.map((option) => option.value)).toEqual(['uniform', 'ath-default-zmap', 'zmap']);
    expect(PARAMETER_REGISTRY.find((field) => field.legacyKey === 'sourceVelocity')?.id).toBe('source.velocity_convention');
  });

  it('marks the daggered rows expression-capable and gives enclosure resolutions a numeric list editor', () => {
    const tupleIds = new Set(['enclosure.front_resolution', 'enclosure.back_resolution']);
    expect([...EXPRESSION_PARAMETER_IDS].filter((id) => !tupleIds.has(id))).toHaveLength(44);
    expect(EXPRESSION_PARAMETER_IDS.has('morph.target_exponent')).toBe(true);
    expect([...tupleIds].every((id) => !EXPRESSION_PARAMETER_IDS.has(id))).toBe(true);
    expect(PARAMETER_REGISTRY.filter((field) => tupleIds.has(field.id)).every((field) => field.kind === 'text')).toBe(true);
  });

  it('registers the Superellipse morph target and its conditional exponent control', () => {
    const target = PARAMETER_REGISTRY.find((field) => field.id === 'morph.target_shape')!;
    expect(target.options).toEqual([
      { value: 0, label: 'None' },
      { value: 1, label: 'Rectangle' },
      { value: 2, label: 'Circle' },
      { value: 3, label: 'Superellipse' },
    ]);

    const exponent = PARAMETER_REGISTRY.find((field) => field.id === 'morph.target_exponent')!;
    expect(exponent).toMatchObject({
      legacyKey: 'morphExponent',
      path: 'morph.target_exponent',
      section: 'Mouth',
      kind: 'number',
      min: 2,
      max: 16,
      families: ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'],
    });
  });

  it('keeps solve-mesh preconfiguration visible while outer-body fields follow the explicit mode', () => {
    const osse = designForFamily('OSSE');
    osse.guiding_curve.curve_type = 1;
    osse.morph.target_shape = 0;
    osse.enclosure.depth = 0;
    osse.mesh.wall_thickness = 0;
    for (const id of ['osse.a', 'mesh.rear_resolution']) {
      const field = PARAMETER_REGISTRY.find((item) => item.id === id)!;
      expect(fieldIsVisible(field, osse), id).toBe(true);
      expect(field.disabledWhen?.(osse), id).toBeUndefined();
    }
    expect(fieldIsVisible(PARAMETER_REGISTRY.find((item) => item.id === 'enclosure.space_l')!, osse)).toBe(false);
    for (const id of ['enclosure.front_resolution', 'enclosure.back_resolution']) {
      const field = PARAMETER_REGISTRY.find((item) => item.id === id)!;
      expect(fieldIsVisible(field, osse)).toBe(false);
      expect(fieldIsVisible(field, { ...osse, enclosure: { ...osse.enclosure, depth: 90 } })).toBe(true);
    }
    expect(fieldIsVisible(PARAMETER_REGISTRY.find((item) => item.id === 'mesh.wall_thickness')!, osse)).toBe(false);
  });

  it('labels every OSSE and R-OSSE profile scalar with its ATH formula symbol', () => {
    // An ATH user reads the formula, not our prose, so each profile scalar has
    // to carry the letter the formula uses -- which is exactly its legacy key.
    for (const family of ['R-OSSE', 'OSSE'] as const) {
      const scalars = PARAMETER_REGISTRY
        .filter((item) => (item.section === 'Profile' || ['common.s1', 'common.s2'].includes(item.id)) && item.families?.includes(family) && item.id !== 'common.scale' && !item.id.startsWith('cross_section.'));
      expect(scalars.map((item) => item.symbol)).toEqual([...TRACEABILITY_KEYS[family].filter((key) => key !== 'scale'), 's1', 's2']);
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

  it('explains legacy source-contour limitations without advertising the hidden editor', () => {
    const text = PARAMETER_REGISTRY.find((item) => item.id === 'source.contours')!.description!;
    expect(text).toContain('Legacy ATH Source.Contours');
    expect(text).toContain('round-trip only');
    expect(text).toContain('stops waveguide preview and solve meshing');
    expect(text).not.toContain('Edit source contour');
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

  describe('aperture mesh scale', () => {
    const field = () => PARAMETER_REGISTRY.find((item) => item.id === 'mesh.aperture_resolution_scale')!;

    it('defaults to 1, the cap using Mouth mesh resolution', () => {
      expect(designForFamily('OSSE').mesh.aperture_resolution_scale).toBe(1);
      resetDesignStore();
      expect(useDesignStore.getState().design.mesh.aperture_resolution_scale).toBe(1);
    });

    it('cannot be set below 1, and says a value below 1 is refused rather than clamped', () => {
      expect(field().min).toBe(1);
      const text = field().description ?? '';
      expect(text).not.toMatch(/clamped/i);
      expect(text).toContain('Values below 1 are refused');
      expect(text).toMatch(/6.8 kHz/);
    });

    it('keeps a saved 1.5 as its own setting', () => {
      resetDesignStore();
      const saved = JSON.parse(JSON.stringify(useDesignStore.getState().design));
      saved.mesh.aperture_resolution_scale = 1.5;
      useDesignStore.getState().replaceDocument(saved);
      expect(useDesignStore.getState().design.mesh.aperture_resolution_scale).toBe(1.5);
    });
  });
});

describe('advanced ICW registry', () => {
  it('places optional controls at the end of Geometry Profile for ICW only', () => {
    const advanced = PARAMETER_REGISTRY.filter((field) => field.group === 'Advanced ICW');
    expect(advanced).toHaveLength(11);
    expect(PARAMETER_REGISTRY.filter((field) => field.section === 'Profile').slice(-11)).toEqual(advanced);
    for (const field of advanced) {
      expect(field.section).toBe('Profile');
      expect(field.families).toEqual(['ICW']);
      for (const family of ['OSSE', 'R-OSSE', 'FREEFORM', ...NATIVE_FAMILIES] as const) {
        expect(fieldIsVisible(field, designForFamily(family))).toBe(false);
      }
    }
  });

  it('honours R precedence, pinning and seed/direct modes without dropping stored values', () => {
    const field = (id: string) => PARAMETER_REGISTRY.find((item) => item.id === id)!;
    const design = designForFamily('ICW');
    design.termination = 'rollback';
    expect(field('icw.r_aperture').disabledWhen!(design)).toContain('R takes precedence');
    delete design.R;
    expect(field('icw.r_aperture').disabledWhen!(design)).toBeUndefined();
    design.termination = 'flat_baffle';
    design.coverage_angle = 45;
    expect(field('icw.R').disabledWhen!(design)).toContain('Emergent');
    design.pin_mouth_radius = true;
    expect(field('icw.R').disabledWhen!(design)).toBeUndefined();
    expect(field('icw.icw_S').disabledWhen!(design)).toContain('only with imported');
    design.icw_coeffs = [0, 0, 0, 0];
    expect(field('icw.icw_S').disabledWhen!(design)).toBeUndefined();
    expect(field('icw.kappa0').disabledWhen!(design)).toContain('directly');
    design.icw_seed = { type: 'OSSE' };
    expect(field('icw.icw_S').disabledWhen!(design)).toContain('Seed mode');
    expect(fieldIsVisible(field('icw.icw_seed'), design)).toBe(true);
    expect(fieldIsVisible(field('icw.icw_coeffs'), design)).toBe(true);
    expect(field('icw.icw_seed').kind).toBe('indicator');
    expect(field('icw.icw_coeffs').kind).toBe('indicator');
  });
});
