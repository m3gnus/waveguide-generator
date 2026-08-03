import type { DesignDocument, DesignFamily } from '../stores/design';

export type ParameterSection =
  | 'Profile'
  | 'Source'
  | 'Symmetry / Quadrants'
  | 'Geometry extras'
  | 'Enclosure'
  | 'Mesh & Sampling'
  | 'Simulation'
  | 'Output & Passthrough';

export type ParameterKind = 'number' | 'select' | 'text' | 'toggle' | 'table' | 'indicator';

export interface ParameterOption {
  value: string | number;
  label: string;
}

export interface ParameterDefinition {
  id: string;
  legacyKey: string;
  path?: string;
  mirrorPaths?: string[];
  section: ParameterSection;
  label: string;
  unit?: string;
  symbol?: string;
  kind: ParameterKind;
  families?: DesignFamily[];
  min?: number;
  max?: number;
  step?: number;
  precision?: number;
  options?: ParameterOption[];
  description?: string;
  disabledReason?: string;
  visibleWhen?: (design: DesignDocument) => boolean;
  disabledWhen?: (design: DesignDocument) => string | undefined;
}

const allFamilies: DesignFamily[] = ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM'];
const osseFamilies: DesignFamily[] = ['R-OSSE', 'OSSE'];
const profile = 'Profile' as const;
const geometry = 'Geometry extras' as const;
const mesh = 'Mesh & Sampling' as const;
const simulation = 'Simulation' as const;

const number = (
  id: string,
  legacyKey: string,
  path: string,
  section: ParameterSection,
  label: string,
  options: Partial<ParameterDefinition> = {},
): ParameterDefinition => ({
  id, legacyKey, path, section, label, kind: 'number', step: .1, precision: 2, ...options,
});

const select = (
  id: string,
  legacyKey: string,
  path: string,
  section: ParameterSection,
  label: string,
  options: ParameterOption[],
  rest: Partial<ParameterDefinition> = {},
): ParameterDefinition => ({ id, legacyKey, path, section, label, kind: 'select', options, ...rest });

const yesNo: ParameterOption[] = [{ value: 0, label: 'No' }, { value: 1, label: 'Yes' }];

export const PARAMETER_REGISTRY: ParameterDefinition[] = [
  // Complete family profile scalars.
  number('common.scale', 'scale', 'scale', profile, 'Scale', { families: allFamilies, min: .1, max: 2, step: .001, precision: 3 }),
  number('rosse.R', 'R', 'R', profile, 'Mouth radius', { families: ['R-OSSE'], unit: 'mm', min: .1, max: 1_000 }),
  number('rosse.a', 'a', 'a', profile, 'Mouth coverage angle', { families: ['R-OSSE'], unit: '°', min: -180, max: 180 }),
  number('rosse.a0', 'a0', 'a0', profile, 'Throat coverage angle', { families: ['R-OSSE'], unit: '°', min: -90, max: 90 }),
  number('rosse.r0', 'r0', 'r0', profile, 'Throat radius', { families: ['R-OSSE'], unit: 'mm', min: .1, max: 200 }),
  number('rosse.k', 'k', 'k', profile, 'Throat rounding', { families: ['R-OSSE'], min: .1, max: 10 }),
  number('rosse.m', 'm', 'm', profile, 'Apex shift', { families: ['R-OSSE'], min: 0, max: 1, step: .01 }),
  number('rosse.b', 'b', 'b', profile, 'Bending', { families: ['R-OSSE'], min: -10, max: 10 }),
  number('rosse.r', 'r', 'r', profile, 'Apex radius', { families: ['R-OSSE'], min: .01, max: 2, step: .01 }),
  number('rosse.q', 'q', 'q', profile, 'Shape factor', { families: ['R-OSSE'], min: .5, max: 10 }),
  number('rosse.tmax', 'tmax', 'tmax', profile, 'Truncation limit', { families: ['R-OSSE'], min: .5, max: 1, step: .01 }),

  number('osse.L', 'L', 'L', profile, 'Horn length', { families: ['OSSE'], unit: 'mm', min: .1, max: 1_000 }),
  number('osse.a', 'a', 'a', profile, 'Mouth coverage angle', { families: ['OSSE'], unit: '°', min: -180, max: 180 }),
  number('osse.a0', 'a0', 'a0', profile, 'Throat coverage angle', { families: ['OSSE'], unit: '°', min: -90, max: 90 }),
  number('osse.r0', 'r0', 'r0', profile, 'Throat radius', { families: ['OSSE'], unit: 'mm', min: .1, max: 200 }),
  number('osse.k', 'k', 'k', profile, 'Flare constant', { families: ['OSSE'], min: .1, max: 15 }),
  number('osse.s', 's', 's', profile, 'Termination shape', { families: ['OSSE'], min: -10, max: 10 }),
  number('osse.n', 'n', 'n', profile, 'Termination curvature', { families: ['OSSE'], min: 1, max: 10, step: .001, precision: 3 }),
  number('osse.q', 'q', 'q', profile, 'Termination smoothness', { families: ['OSSE'], min: .1, max: 2, step: .001, precision: 3 }),
  number('osse.h', 'h', 'h', profile, 'Shape factor', { families: ['OSSE'], min: 0, max: 10 }),

  number('icw.r0', 'r0', 'r0', profile, 'Throat radius', { families: ['ICW'], unit: 'mm', min: .1, max: 200 }),
  number('icw.a0', 'a0', 'a0', profile, 'Throat half-angle', { families: ['ICW'], unit: '°', min: -90, max: 90 }),
  number('icw.L', 'L', 'L', profile, 'Horn length', { families: ['ICW'], unit: 'mm', min: 1, max: 1_000, visibleWhen: (design) => design.termination !== 'rollback' }),
  number('icw.R', 'R', 'R', profile, 'Mouth radius', { families: ['ICW'], unit: 'mm', min: .1, max: 1_000, disabledWhen: (design) => (design.coverage_angle ?? 0) > 0 ? 'Emergent while coverage hold is enabled.' : undefined }),
  number('icw.coverage_angle', 'coverage_angle', 'coverage_angle', geometry, 'Coverage angle', { families: ['ICW'], unit: '°', min: 0, max: 80, step: 1, precision: 0, disabledWhen: (design) => design.termination === 'rollback' ? 'Coverage hold applies to flat-baffle termination only.' : undefined }),
  number('icw.hold_start', 'hold_start', 'hold_start', geometry, 'Coverage hold start', { families: ['ICW'], unit: 'σ', min: .05, max: .9, step: .01, visibleWhen: (design) => design.termination !== 'rollback' }),
  number('icw.hold_end', 'hold_end', 'hold_end', geometry, 'Coverage hold end', { families: ['ICW'], unit: 'σ', min: .1, max: .95, step: .01, visibleWhen: (design) => design.termination !== 'rollback' }),
  number('icw.n_coeff', 'n_coeff', 'n_coeff', profile, 'Curvature coefficients', { families: ['ICW'], min: 5, max: 24, step: 1, precision: 0 }),
  select('icw.termination', 'termination', 'termination', geometry, 'Termination', [{ value: 'flat_baffle', label: 'Flat baffle' }, { value: 'rollback', label: 'Rollback' }], { families: ['ICW'] }),
  number('icw.theta1_deg', 'theta1_deg', 'theta1_deg', geometry, 'Rollback curl angle', { families: ['ICW'], unit: '°', min: 91, max: 179, step: 1, precision: 0, visibleWhen: (design) => design.termination === 'rollback' }),
  number('icw.depth', 'depth', 'depth', geometry, 'Rollback depth', { families: ['ICW'], unit: 'mm', min: 1, max: 1_000, step: 1, visibleWhen: (design) => design.termination === 'rollback' }),
  // ICW schema additions not present in the v1 centralized inventory.
  number('icw.a', 'a', 'a', profile, 'Legacy coverage coefficient', { families: ['ICW'], unit: '°', min: -180, max: 180 }),
  number('icw.k', 'k', 'k', profile, 'Legacy flare coefficient', { families: ['ICW'], min: -100, max: 100 }),
  number('icw.q', 'q', 'q', profile, 'Legacy smoothness coefficient', { families: ['ICW'], min: -100, max: 100 }),
  number('icw.curl', 'curl', 'curl', profile, 'Curl', { families: ['ICW'], min: -100, max: 100 }),

  number('freeform.length', 'length', 'profile_h.points.$last.z', profile, 'Length', { families: ['FREEFORM'], mirrorPaths: ['profile_v.points.$last.z'], unit: 'mm', min: 20, max: 1_000, step: 1 }),
  number('freeform.throatRadius', 'throatRadius', 'profile_h.points.0.r', profile, 'Throat radius', { families: ['FREEFORM'], mirrorPaths: ['profile_v.points.0.r'], unit: 'mm', min: .1, max: 200 }),
  number('freeform.throatAngle', 'throatAngle', 'profile_h.throat_angle_deg', profile, 'Throat angle', { families: ['FREEFORM'], mirrorPaths: ['profile_v.throat_angle_deg'], unit: '°', min: -90, max: 90 }),
  number('freeform.mouthRadiusH', 'mouthRadiusH', 'profile_h.points.$last.r', profile, 'Horizontal mouth radius', { families: ['FREEFORM'], unit: 'mm', min: .1, max: 1_000, step: 1 }),
  number('freeform.mouthAngleH', 'mouthAngleH', 'profile_h.mouth_angle_deg', profile, 'Horizontal mouth angle', { families: ['FREEFORM'], unit: '°', min: -90, max: 90 }),
  { id: 'freeform.interiorH', legacyKey: 'interiorH', path: 'profile_h.points', section: profile, label: 'Horizontal spline points', kind: 'table', families: ['FREEFORM'] },
  number('freeform.throatTangentScaleH', 'throatTangentScaleH', 'profile_h.throat_tangent_scale', profile, 'Horizontal throat tangent scale', { families: ['FREEFORM'], min: .1, max: 3 }),
  number('freeform.mouthTangentScaleH', 'mouthTangentScaleH', 'profile_h.mouth_tangent_scale', profile, 'Horizontal mouth tangent scale', { families: ['FREEFORM'], min: .1, max: 3 }),
  number('freeform.mouthRadiusV', 'mouthRadiusV', 'profile_v.points.$last.r', profile, 'Vertical mouth radius', { families: ['FREEFORM'], unit: 'mm', min: .1, max: 1_000, step: 1 }),
  number('freeform.mouthAngleV', 'mouthAngleV', 'profile_v.mouth_angle_deg', profile, 'Vertical mouth angle', { families: ['FREEFORM'], unit: '°', min: -90, max: 90 }),
  { id: 'freeform.interiorV', legacyKey: 'interiorV', path: 'profile_v.points', section: profile, label: 'Vertical spline points', kind: 'table', families: ['FREEFORM'] },
  number('freeform.throatTangentScaleV', 'throatTangentScaleV', 'profile_v.throat_tangent_scale', profile, 'Vertical throat tangent scale', { families: ['FREEFORM'], min: .1, max: 3 }),
  number('freeform.mouthTangentScaleV', 'mouthTangentScaleV', 'profile_v.mouth_tangent_scale', profile, 'Vertical mouth tangent scale', { families: ['FREEFORM'], min: .1, max: 3 }),
  { id: 'freeform.crossSections', legacyKey: 'crossSections', path: 'cross_sections', section: profile, label: 'Cross-section stations', kind: 'table', families: ['FREEFORM'] },
  select('freeform.overshootPolicy', 'overshootPolicy', 'overshoot_policy', profile, 'Spline overshoot', [{ value: 'reject', label: 'Reject' }, { value: 'allow', label: 'Allow' }], { families: ['FREEFORM'] }),
  select('freeform.inflectionPolicy', 'inflectionPolicy', 'inflection_policy', profile, 'Curve direction', [{ value: 'warn', label: 'Warn on S-curves' }, { value: 'reject', label: 'Enforce one-way' }], { families: ['FREEFORM'] }),

  // Throat extension, morph, coverage/length modes, and complete OSSE guide controls.
  number('common.throat_ext_angle', 'throatExtAngle', 'throat_ext_angle', geometry, 'Throat extension angle', { families: osseFamilies, unit: '°', min: -90, max: 90 }),
  number('common.throat_ext_length', 'throatExtLength', 'throat_ext_length', geometry, 'Throat extension length', { families: osseFamilies, unit: 'mm', min: 0, max: 1_000 }),
  number('common.slot_length', 'slotLength', 'slot_length', geometry, 'Straight slot length', { families: osseFamilies, unit: 'mm', min: 0, max: 1_000 }),
  select('common.length_mode', 'lengthMode', 'length_mode', geometry, 'Length mode', [{ value: 'profile', label: 'Profile length' }, { value: 'total', label: 'Total length' }], { families: osseFamilies }),
  { id: 'common.coverage_mode', legacyKey: 'coverageMode', path: 'coverage_mode', section: geometry, label: 'Coverage mode', kind: 'text', families: ['ICW'] },
  select('morph.target_shape', 'morphTarget', 'morph.target_shape', geometry, 'Morph target', [{ value: 0, label: 'None' }, { value: 1, label: 'Rectangle' }, { value: 2, label: 'Circle' }], { families: ['R-OSSE', 'OSSE', 'ICW'] }),
  number('morph.target_width', 'morphWidth', 'morph.target_width', geometry, 'Target width', { families: ['R-OSSE', 'OSSE', 'ICW'], unit: 'mm', min: 0, max: 2_000 }),
  number('morph.target_height', 'morphHeight', 'morph.target_height', geometry, 'Target height', { families: ['R-OSSE', 'OSSE', 'ICW'], unit: 'mm', min: 0, max: 2_000 }),
  number('morph.corner_radius', 'morphCorner', 'morph.corner_radius', geometry, 'Corner radius', { families: ['R-OSSE', 'OSSE', 'ICW'], unit: 'mm', min: 0, max: 100, step: 1 }),
  number('morph.rate', 'morphRate', 'morph.rate', geometry, 'Morph rate', { families: ['R-OSSE', 'OSSE', 'ICW'], min: 0, max: 100 }),
  number('morph.fixed_part', 'morphFixed', 'morph.fixed_part', geometry, 'Fixed part', { families: ['R-OSSE', 'OSSE', 'ICW'], min: 0, max: 1, step: .01 }),
  select('morph.allow_shrinkage', 'morphAllowShrinkage', 'morph.allow_shrinkage', geometry, 'Allow shrinkage', yesNo, { families: ['R-OSSE', 'OSSE', 'ICW'] }),
  select('osse.throat_profile', 'throatProfile', 'throat_profile', geometry, 'Throat profile', [{ value: 1, label: 'OS-SE' }, { value: 3, label: 'Circular arc' }], { families: ['OSSE'] }),
  number('osse.rotation', 'rot', 'rotation', geometry, 'Profile rotation', { families: ['OSSE'], unit: '°', min: -360, max: 360 }),
  select('guide.curve_type', 'gcurveType', 'guiding_curve.curve_type', geometry, 'Guiding curve mode', [{ value: 0, label: 'Explicit coverage' }, { value: 1, label: 'Superellipse' }, { value: 2, label: 'Superformula' }], { families: ['OSSE'] }),
  number('guide.distance', 'gcurveDist', 'guiding_curve.distance', geometry, 'Guiding curve distance', { families: ['OSSE'], min: 0, max: 10_000 }),
  number('guide.width', 'gcurveWidth', 'guiding_curve.width', geometry, 'Guiding curve width', { families: ['OSSE'], unit: 'mm', min: 0, max: 2_000 }),
  number('guide.aspect_ratio', 'gcurveAspectRatio', 'guiding_curve.aspect_ratio', geometry, 'Guiding curve aspect ratio', { families: ['OSSE'], min: .01, max: 100 }),
  number('guide.superellipse_n', 'gcurveSeN', 'guiding_curve.superellipse_n', geometry, 'Guiding superellipse exponent', { families: ['OSSE'], min: .01, max: 100, visibleWhen: (design) => design.guiding_curve.curve_type === 1 }),
  number('guide.superformula', 'gcurveSf', 'guiding_curve.superformula', geometry, 'Superformula tuple', { families: ['OSSE'], min: -1_000, max: 1_000, visibleWhen: (design) => design.guiding_curve.curve_type === 2 }),
  ...(['sf_a', 'sf_b', 'sf_m1', 'sf_m2', 'sf_n1', 'sf_n2', 'sf_n3'] as const).map((key) => number(`guide.${key}`, `gcurveSf${key.slice(3).toUpperCase()}`, `guiding_curve.${key}`, geometry, `Superformula ${key.slice(3)}`, { families: ['OSSE'], min: -1_000, max: 1_000, visibleWhen: (design) => design.guiding_curve.curve_type === 2 })),
  number('guide.rotation', 'gcurveRot', 'guiding_curve.rotation', geometry, 'Guiding curve rotation', { families: ['OSSE'], unit: '°', min: -360, max: 360 }),
  number('osse.circ_arc_term_angle', 'circArcTermAngle', 'circ_arc_term_angle', geometry, 'Circular arc terminal angle', { families: ['OSSE'], unit: '°', min: -180, max: 180, visibleWhen: (design) => design.throat_profile === 3 }),
  number('osse.circ_arc_radius', 'circArcRadius', 'circ_arc_radius', geometry, 'Circular arc radius override', { families: ['OSSE'], unit: 'mm', min: 0, max: 10_000, visibleWhen: (design) => design.throat_profile === 3 }),

  // Source including schema-carried contours and explicit velocity convention.
  select('source.shape', 'sourceShape', 'source.shape', 'Source', 'Source surface', [{ value: 1, label: 'Spherical cap' }, { value: 2, label: 'Flat disc' }]),
  number('source.radius', 'sourceRadius', 'source.radius', 'Source', 'Source radius', { unit: 'mm', min: -1, max: 2_000 }),
  select('source.curvature', 'sourceCurv', 'source.curvature', 'Source', 'Source curvature', [{ value: 0, label: 'Auto' }, { value: 1, label: 'Convex' }, { value: -1, label: 'Concave' }]),
  number('source.velocity', 'sourceAmplitude', 'source.velocity', 'Source', 'Source amplitude', { unit: 'm/s', min: 0, max: 100, step: .01, precision: 3 }),
  select('source.velocity_convention', 'sourceVelocity', 'source.velocity_convention', 'Source', 'Velocity convention', [{ value: 'normal', label: 'Normal velocity' }, { value: 'axial', label: 'Axial rigid-piston velocity' }, { value: 'legacy', label: 'Legacy config value' }]),
  { id: 'source.contours', legacyKey: 'sourceContours', path: 'source.contours', section: 'Source', label: 'Source contours', kind: 'text', description: 'File path or inline-script expression; preserved verbatim.' },

  // Symmetry is custom-rendered as quadrants but registered against schema mesh.quadrants.
  { id: 'mesh.quadrants', legacyKey: 'quadrants', path: 'mesh.quadrants', section: 'Symmetry / Quadrants', label: 'Solve/export quadrants', kind: 'select' },

  // Full enclosure schema block.
  number('enclosure.depth', 'encDepth', 'enclosure.depth', 'Enclosure', 'Enclosure depth', { unit: 'mm', min: 0, max: 2_000, step: 1 }),
  number('enclosure.edge_radius', 'encEdge', 'enclosure.edge_radius', 'Enclosure', 'Edge radius', { unit: 'mm', min: 0, max: 1_000 }),
  select('enclosure.edge_type', 'encEdgeType', 'enclosure.edge_type', 'Enclosure', 'Edge finish', [{ value: 1, label: 'Rounded' }, { value: 2, label: 'Chamfered' }]),
  ...([['space_l', 'encSpaceL', 'Left margin'], ['space_t', 'encSpaceT', 'Top margin'], ['space_r', 'encSpaceR', 'Right margin'], ['space_b', 'encSpaceB', 'Bottom margin']] as const).map(([path, key, label]) => number(`enclosure.${path}`, key, `enclosure.${path}`, 'Enclosure', label, { unit: 'mm', min: 0, max: 2_000 })),
  number('enclosure.front_resolution', 'encFrontResolution', 'enclosure.front_resolution', 'Enclosure', 'Front baffle mesh resolution', { unit: 'mm', min: .01, max: 1_000, description: 'One value or a four-value ATH resolution tuple.' }),
  number('enclosure.back_resolution', 'encBackResolution', 'enclosure.back_resolution', 'Enclosure', 'Rear baffle mesh resolution', { unit: 'mm', min: .01, max: 1_000, description: 'One value or a four-value ATH resolution tuple.' }),

  // Viewport sampling and solve/export mesh are deliberately kept together but labelled distinctly.
  number('mesh.angular_segments', 'angularSegments', 'mesh.angular_segments', mesh, 'Surface angular samples', { min: 0, max: 4_096, step: 1, precision: 0 }),
  number('mesh.length_segments', 'lengthSegments', 'mesh.length_segments', mesh, 'Surface length samples', { min: 0, max: 4_096, step: 1, precision: 0 }),
  number('mesh.corner_segments', 'cornerSegments', 'mesh.corner_segments', mesh, 'Surface corner samples', { min: 0, max: 1_024, step: 1, precision: 0 }),
  number('mesh.throat_segments', 'throatSegments', 'mesh.throat_segments', mesh, 'Throat slice samples', { min: 0, max: 4_096, step: 1, precision: 0 }),
  number('mesh.throat_slice_density', 'throatSliceDensity', 'mesh.throat_slice_density', mesh, 'Preview slice bias', { min: .01, max: .99, step: .01 }),
  select('mesh.sampling_mode', 'samplingMode', 'mesh.sampling_mode', mesh, 'Z-map sampling mode', [{ value: 'uniform', label: 'Uniform' }, { value: 'ath-default-zmap', label: 'ATH default Z-map' }, { value: 'zmap', label: 'Custom Z-map points' }]),
  { id: 'mesh.z_map_points', legacyKey: 'zMapPoints', path: 'mesh.z_map_points', section: mesh, label: 'Z-map points', kind: 'text', visibleWhen: (design) => design.mesh.sampling_mode === 'zmap', description: 'ATH point expression, preserved verbatim.' },
  number('mesh.throat_resolution', 'throatResolution', 'mesh.throat_resolution', mesh, 'Throat mesh resolution', { unit: 'mm', min: .01, max: 1_000 }),
  number('mesh.mouth_resolution', 'mouthResolution', 'mesh.mouth_resolution', mesh, 'Mouth mesh resolution', { unit: 'mm', min: .01, max: 1_000, description: 'For 20 kHz, λ/6 is approximately 2.86 mm.' }),
  { id: 'schema-gap.max_edge', legacyKey: 'maxEdge', section: mesh, label: 'Maximum edge guard', kind: 'number', unit: 'mm', disabledReason: 'Not present in server DesignConfig; use the regional resolution controls until the server schema adds it.' },
  number('mesh.rear_resolution', 'rearResolution', 'mesh.rear_resolution', mesh, 'Rear mesh resolution', { unit: 'mm', min: .01, max: 1_000 }),
  number('mesh.aperture_resolution_scale', 'apertureResolutionScale', 'mesh.aperture_resolution_scale', mesh, 'Aperture mesh scale', { min: .01, max: 100 }),
  number('mesh.max_triangles', 'maxTriangles', 'mesh.max_triangles', mesh, 'Hard triangle limit', { min: 1, max: 10_000_000, step: 1_000, precision: 0 }),
  select('mesh.allow_large_mesh', 'allowLargeMesh', 'mesh.allow_large_mesh', mesh, 'Large mesh approval', [{ value: 0, label: 'Block over budget' }, { value: 1, label: 'Approve over budget' }]),
  number('mesh.vertical_offset', 'verticalOffset', 'mesh.vertical_offset', mesh, 'Export vertical offset', { unit: 'mm', min: -10_000, max: 10_000 }),
  number('mesh.wall_thickness', 'wallThickness', 'mesh.wall_thickness', mesh, 'Wall thickness', { unit: 'mm', min: 0, max: 1_000 }),

  number('simulation.f1', 'freqStart', 'simulation.f1', simulation, 'Sweep start', { unit: 'Hz', min: 20, max: 20_000, step: 10, precision: 0 }),
  number('simulation.f2', 'freqEnd', 'simulation.f2', simulation, 'Sweep end', { unit: 'Hz', min: 20, max: 20_000, step: 10, precision: 0 }),
  number('simulation.num_frequencies', 'numFreqs', 'simulation.num_frequencies', simulation, 'Frequency samples', { min: 10, max: 200, step: 1, precision: 0 }),
  select('simulation.sim_type', 'simType', 'simulation.sim_type', simulation, 'Simulation type', [{ value: 'freestanding', label: 'Free-standing' }, { value: 'infinite-baffle', label: 'Infinite baffle' }]),
  select('simulation.solver_mode', 'solverMode', 'simulation.solver_mode', simulation, 'Solver mode', [{ value: 'auto', label: 'Auto' }, { value: 'full_3d', label: 'Full 3D' }, { value: 'circsym', label: 'CircSym' }]),

  select('output.stl', 'outputSTL', 'output.stl', 'Output & Passthrough', 'STL output flag', yesNo),
  select('output.msh', 'outputMSH', 'output.msh', 'Output & Passthrough', 'MSH output flag', yesNo),
  { id: 'passthrough.abec', legacyKey: 'ABEC', section: 'Output & Passthrough', label: 'ABEC passthrough blocks', kind: 'indicator' },
  { id: 'passthrough.report', legacyKey: 'Report', section: 'Output & Passthrough', label: 'Report passthrough block', kind: 'indicator' },
  { id: 'passthrough.extra', legacyKey: 'extraBlocks', section: 'Output & Passthrough', label: 'Other passthrough blocks', kind: 'indicator' },
  { id: 'passthrough.keys', legacyKey: 'extraKeys', section: 'Output & Passthrough', label: 'Unknown flat keys', kind: 'indicator' },
];

const familyTraceKeys: Record<DesignFamily, readonly string[]> = {
  'R-OSSE': ['scale', 'R', 'a', 'a0', 'r0', 'k', 'm', 'b', 'r', 'q', 'tmax'],
  OSSE: ['scale', 'L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h'],
  ICW: ['scale', 'r0', 'a0', 'L', 'R', 'coverage_angle', 'hold_start', 'hold_end', 'n_coeff', 'termination', 'theta1_deg', 'depth'],
  FREEFORM: ['scale', 'length', 'throatRadius', 'throatAngle', 'mouthRadiusH', 'mouthAngleH', 'interiorH', 'throatTangentScaleH', 'mouthTangentScaleH', 'mouthRadiusV', 'mouthAngleV', 'interiorV', 'throatTangentScaleV', 'mouthTangentScaleV', 'crossSections', 'overshootPolicy', 'inflectionPolicy'],
};

const commonTraceKeys = [
  'throatExtAngle', 'throatExtLength', 'slotLength',
  'morphTarget', 'morphWidth', 'morphHeight', 'morphCorner', 'morphRate', 'morphFixed', 'morphAllowShrinkage',
  'wallThickness', 'encDepth', 'encEdge', 'encEdgeType', 'encSpaceL', 'encSpaceT', 'encSpaceR', 'encSpaceB',
  'throatProfile', 'rot', 'gcurveType', 'gcurveDist', 'gcurveWidth', 'gcurveAspectRatio', 'gcurveSeN', 'gcurveSf',
  'gcurveSfA', 'gcurveSfB', 'gcurveSfM1', 'gcurveSfM2', 'gcurveSfN1', 'gcurveSfN2', 'gcurveSfN3', 'gcurveRot', 'circArcTermAngle', 'circArcRadius',
  'angularSegments', 'lengthSegments', 'cornerSegments', 'throatSegments', 'throatSliceDensity',
  'freqStart', 'freqEnd', 'numFreqs', 'sourceShape', 'sourceRadius', 'sourceCurv', 'sourceVelocity',
  'simType', 'solverMode', 'throatResolution', 'mouthResolution', 'rearResolution', 'apertureResolutionScale',
  'maxTriangles', 'allowLargeMesh', 'verticalOffset', 'quadrants', 'encFrontResolution', 'encBackResolution',
] as const;

/** Exact 110-entry list from parameterInventory.js, with family duplicates qualified. */
export const TRACEABILITY_PARAMETER_INVENTORY = [
  ...Object.entries(familyTraceKeys).flatMap(([family, keys]) => keys.map((key) => ({ key, family: family as DesignFamily }))),
  ...commonTraceKeys.map((key) => ({ key, family: undefined })),
];

export const PARAMETER_SECTIONS: ParameterSection[] = [
  'Profile', 'Source', 'Symmetry / Quadrants', 'Geometry extras', 'Enclosure',
  'Mesh & Sampling', 'Simulation', 'Output & Passthrough',
];

/** The 43 daggered v1 fields, plus the two legacy four-value baffle tuples. */
export const EXPRESSION_PARAMETER_IDS = new Set([
  'rosse.R', 'rosse.a', 'rosse.a0', 'rosse.r0', 'rosse.k', 'rosse.m', 'rosse.b', 'rosse.r', 'rosse.q', 'rosse.tmax',
  'osse.L', 'osse.a', 'osse.a0', 'osse.r0', 'osse.k', 'osse.s', 'osse.n', 'osse.q', 'osse.h',
  'common.throat_ext_angle', 'common.throat_ext_length', 'common.slot_length',
  'morph.target_width', 'morph.target_height', 'morph.corner_radius', 'morph.rate', 'morph.fixed_part',
  'osse.rotation', 'guide.distance', 'guide.width', 'guide.aspect_ratio', 'guide.superellipse_n', 'guide.superformula',
  'guide.sf_a', 'guide.sf_b', 'guide.sf_m1', 'guide.sf_m2', 'guide.sf_n1', 'guide.sf_n2', 'guide.sf_n3',
  'guide.rotation', 'osse.circ_arc_term_angle', 'osse.circ_arc_radius',
  'enclosure.front_resolution', 'enclosure.back_resolution',
]);

export function fieldAcceptsExpression(field: ParameterDefinition): boolean {
  return EXPRESSION_PARAMETER_IDS.has(field.id);
}

export function fieldAppliesToFamily(field: ParameterDefinition, family: DesignFamily): boolean {
  return !field.families || field.families.includes(family);
}

export function fieldIsVisible(field: ParameterDefinition, design: DesignDocument): boolean {
  return fieldAppliesToFamily(field, design.formula) && (!field.visibleWhen || field.visibleWhen(design));
}

export function fieldMatchesQuery(field: ParameterDefinition, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return true;
  return [field.label, field.legacyKey, field.path, field.id, field.description]
    .some((value) => value?.toLocaleLowerCase().includes(normalized));
}

export function traceEntryIsRegistered(entry: { key: string; family?: DesignFamily }): boolean {
  return PARAMETER_REGISTRY.some((field) => field.legacyKey === entry.key && (!entry.family || fieldAppliesToFamily(field, entry.family)));
}
