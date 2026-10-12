import {
  LEGACY_PARAMETER_REGISTRY,
  fieldAcceptsExpression,
  type ParameterDefinition,
} from './parameterRegistry';
import { NATIVE_FAMILIES, designForFamily, type DesignDocument, type DesignFamily } from '../stores/design';

type Condition = {
  path?: string;
  operator: 'equals' | 'not_equals' | 'greater_than' | 'less_than_or_equal' | 'never' | 'all' | 'any' | 'present' | 'absent';
  value?: unknown;
  conditions?: Condition[];
};

type FieldConditions = {
  visible_when?: Condition;
  disabled_when?: Condition;
  disabled_reason?: string;
};

const ALL_FAMILIES = ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP', ...NATIVE_FAMILIES] as const;

function valueAtPath(design: DesignDocument, path: string | undefined): unknown {
  if (!path) return undefined;
  return path.split('.').reduce<unknown>((value, part) => {
    if (value === null || typeof value !== 'object') return undefined;
    return (value as Record<string, unknown>)[part];
  }, design);
}

function defaultValues(field: ParameterDefinition): Partial<Record<DesignFamily, unknown>> {
  const families = field.families ?? [...ALL_FAMILIES];
  return Object.fromEntries(families.flatMap((family) => {
    const value = valueAtPath(designForFamily(family), field.path);
    return value === undefined ? [] : [[family, value]];
  }));
}

const CROSS_SECTION_CONDITIONS: FieldConditions = {
  disabled_when: {
    operator: 'all',
    conditions: [
      { path: 'formula', operator: 'equals', value: 'OSSE' },
      { path: 'guiding_curve.curve_type', operator: 'not_equals', value: 0 },
      { path: 'guiding_curve.width', operator: 'not_equals', value: 0 },
    ],
  },
  disabled_reason: 'Active guiding curves require cross-section exponent 2 and aspect ratio 1.',
};

const FIELD_CONDITIONS: Record<string, FieldConditions> = {
  'cross_section.exponent': CROSS_SECTION_CONDITIONS,
  'cross_section.aspect_ratio': CROSS_SECTION_CONDITIONS,
  'enclosure.front_resolution': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'enclosure.back_resolution': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'icw.L': { visible_when: { path: 'termination', operator: 'not_equals', value: 'rollback' } },
  'icw.R': {
    disabled_when: { operator: 'all', conditions: [
      { path: 'termination', operator: 'not_equals', value: 'rollback' },
      { path: 'coverage_angle', operator: 'greater_than', value: 0 },
      { path: 'pin_mouth_radius', operator: 'not_equals', value: true },
    ] },
    disabled_reason: 'Emergent while coverage hold is enabled; enable Pin mouth radius to target R.',
  },
  'icw.coverage_angle': {
    disabled_when: { path: 'termination', operator: 'equals', value: 'rollback' },
    disabled_reason: 'Coverage hold applies to flat-baffle termination only.',
  },
  'icw.hold_start': { visible_when: { path: 'termination', operator: 'not_equals', value: 'rollback' } },
  'icw.hold_end': { visible_when: { path: 'termination', operator: 'not_equals', value: 'rollback' } },
  'icw.theta1_deg': { visible_when: { path: 'termination', operator: 'equals', value: 'rollback' } },
  'icw.depth': { visible_when: { path: 'termination', operator: 'equals', value: 'rollback' } },
  'morph.target_width': { visible_when: { path: 'morph.target_shape', operator: 'not_equals', value: 0 } },
  'morph.target_height': { visible_when: { path: 'morph.target_shape', operator: 'not_equals', value: 0 } },
  'morph.rate': { visible_when: { path: 'morph.target_shape', operator: 'not_equals', value: 0 } },
  'morph.fixed_part': { visible_when: { path: 'morph.target_shape', operator: 'not_equals', value: 0 } },
  'morph.allow_shrinkage': { visible_when: { path: 'morph.target_shape', operator: 'not_equals', value: 0 } },
  'morph.corner_radius': { visible_when: { path: 'morph.target_shape', operator: 'equals', value: 1 } },
  'morph.target_exponent': { visible_when: { path: 'morph.target_shape', operator: 'equals', value: 3 } },
  'source.velocity': { visible_when: { path: 'source.velocity_convention', operator: 'equals', value: 'legacy' } },
  'guide.superellipse_n': { visible_when: { path: 'guiding_curve.curve_type', operator: 'equals', value: 1 } },
  'guide.superformula': { visible_when: { path: 'guiding_curve.curve_type', operator: 'equals', value: 2 } },
  'guide.sf_a': { visible_when: { path: 'guiding_curve.curve_type', operator: 'equals', value: 2 } },
  'guide.sf_b': { visible_when: { path: 'guiding_curve.curve_type', operator: 'equals', value: 2 } },
  'guide.sf_m1': { visible_when: { path: 'guiding_curve.curve_type', operator: 'equals', value: 2 } },
  'guide.sf_m2': { visible_when: { path: 'guiding_curve.curve_type', operator: 'equals', value: 2 } },
  'guide.sf_n1': { visible_when: { path: 'guiding_curve.curve_type', operator: 'equals', value: 2 } },
  'guide.sf_n2': { visible_when: { path: 'guiding_curve.curve_type', operator: 'equals', value: 2 } },
  'guide.sf_n3': { visible_when: { path: 'guiding_curve.curve_type', operator: 'equals', value: 2 } },
  'osse.circ_arc_term_angle': { visible_when: { path: 'throat_profile', operator: 'equals', value: 3 } },
  'osse.circ_arc_radius': { visible_when: { path: 'throat_profile', operator: 'equals', value: 3 } },
  'enclosure.depth': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'enclosure.depth_margin_mm': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'enclosure.edge_radius': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'enclosure.edge_type': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'enclosure.space_l': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'enclosure.space_t': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'enclosure.space_r': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'enclosure.space_b': { visible_when: { path: 'enclosure.depth', operator: 'greater_than', value: 0 } },
  'mesh.z_map_points': { visible_when: { path: 'mesh.sampling_mode', operator: 'equals', value: 'zmap' } },
  'mesh.aperture_resolution_scale': { visible_when: { path: 'simulation.sim_type', operator: 'equals', value: 'infinite-baffle' } },
  'mesh.allow_large_mesh': { visible_when: { operator: 'never' } },
  'mesh.wall_thickness': {
    visible_when: {
      operator: 'all',
      conditions: [
        { path: 'enclosure.depth', operator: 'less_than_or_equal', value: 0 },
        { path: 'mesh.wall_thickness', operator: 'greater_than', value: 0 },
      ],
    },
  },
};

const ICW_IMPORTED_INPUT: Condition = { operator: 'any', conditions: [
  { path: 'icw_seed', operator: 'present' }, { path: 'icw_coeffs', operator: 'present' },
] };
for (const id of ['icw.r0', 'icw.a0']) {
  FIELD_CONDITIONS[id] = { disabled_when: { path: 'icw_seed', operator: 'present' } };
}
FIELD_CONDITIONS['icw.n_coeff'] = { disabled_when: { operator: 'all', conditions: [
  { path: 'icw_seed', operator: 'absent' }, { path: 'icw_coeffs', operator: 'present' },
] } };
const ICW_ROLLBACK_ONLY: Condition = { path: 'termination', operator: 'not_equals', value: 'rollback' };
for (const id of ['icw.L', 'icw.R', 'icw.coverage_angle', 'icw.hold_start', 'icw.hold_end', 'icw.termination', 'icw.theta1_deg', 'icw.depth']) {
  const prior = FIELD_CONDITIONS[id] ?? {};
  FIELD_CONDITIONS[id] = { ...prior, disabled_when: { operator: 'any', conditions: [ICW_IMPORTED_INPUT, ...(prior.disabled_when ? [prior.disabled_when] : [])] } };
}
for (const id of ['icw.kappa0', 'icw.kappa_abs_max', 'icw.dkappa_ds_abs_max']) {
  FIELD_CONDITIONS[id] = { disabled_when: ICW_IMPORTED_INPUT };
}
for (const id of ['icw.x_aperture', 'icw.x_setback', 'icw.theta_max_deg', 'icw.r_aperture']) {
  FIELD_CONDITIONS[id] = { disabled_when: { operator: 'any', conditions: [ICW_IMPORTED_INPUT, ICW_ROLLBACK_ONLY,
    ...(id === 'icw.r_aperture' ? [{ path: 'R', operator: 'present' } as Condition] : []),
  ] } };
}
// A value left over from an earlier coverage setting stays editable so it can be reset.
FIELD_CONDITIONS['icw.pin_mouth_radius'] = { disabled_when: { operator: 'any', conditions: [
  ICW_IMPORTED_INPUT,
  { operator: 'all', conditions: [
    { path: 'pin_mouth_radius', operator: 'absent' },
    { operator: 'any', conditions: [
      { path: 'termination', operator: 'equals', value: 'rollback' },
      { path: 'coverage_angle', operator: 'less_than_or_equal', value: 0 },
      { path: 'coverage_angle', operator: 'absent' },
    ] },
  ] },
] } };
FIELD_CONDITIONS['icw.icw_S'] = { disabled_when: { operator: 'any', conditions: [
  { path: 'icw_seed', operator: 'present' }, { path: 'icw_coeffs', operator: 'absent' },
] } };
FIELD_CONDITIONS['icw.icw_seed'] = { visible_when: { path: 'icw_seed', operator: 'present' } };
FIELD_CONDITIONS['icw.icw_coeffs'] = { visible_when: { path: 'icw_coeffs', operator: 'present' } };

function serializeField(field: ParameterDefinition) {
  const conditions = FIELD_CONDITIONS[field.id] ?? {};
  if ((field.visibleWhen && !conditions.visible_when)
    || (field.disabledWhen && !conditions.disabled_when)) {
    throw new Error(`Parameter ${field.id} has an executable condition without a declarative contract rule.`);
  }
  return {
    id: field.id,
    legacy_key: field.legacyKey,
    path: field.path ?? null,
    mirror_paths: field.mirrorPaths ?? [],
    label: field.label,
    section: field.section,
    symbol: field.symbol ?? null,
    kind: field.kind,
    unit: field.unit ?? null,
    families: field.families ?? [...ALL_FAMILIES],
    accepts_expression: fieldAcceptsExpression(field),
    writable: field.kind !== 'indicator',
    default_by_family: defaultValues(field),
    editor_bounds: field.min === undefined || field.max === undefined
      ? null
      : { minimum: field.min, maximum: field.max, ...(field.exclusiveMin ? { exclusive_minimum: true } : {}) },
    step: field.step ?? null,
    precision: field.precision ?? null,
    options: (field.options ?? []).map((option) => ({
      value: option.value,
      label: option.label,
      requires_feature: option.requiresFeature ?? null,
      degraded_without: option.degradedWithout ?? null,
      degraded_label: option.degradedLabel ?? null,
      current_only: option.currentOnly ?? false,
    })),
    description: field.description ?? '',
    ...conditions,
  };
}

export function buildParameterCatalog() {
  return {
    schema_version: 1,
    catalog_version: 1,
    design_families: [...ALL_FAMILIES],
    validation_authority: {
      request_schema: '/openapi.json#/components/schemas/SolveRequest',
      design_schema: '/api/integration/v1/design-schema',
      validate_cli: 'wg validate --request REQUEST.json',
      note: 'editor_bounds describe the WG editor, not hard solver validity or recommended search ranges',
    },
    parameters: LEGACY_PARAMETER_REGISTRY.map(serializeField),
  };
}
