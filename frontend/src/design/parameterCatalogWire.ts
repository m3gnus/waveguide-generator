import {
  PARAMETER_REGISTRY,
  fieldAcceptsExpression,
  type ParameterDefinition,
} from './parameterRegistry';

type Condition = {
  path?: string;
  operator: 'equals' | 'not_equals' | 'greater_than' | 'less_than_or_equal' | 'never' | 'all';
  value?: unknown;
  conditions?: Condition[];
};

type FieldConditions = {
  visible_when?: Condition;
  disabled_when?: Condition;
  disabled_reason?: string;
};

const ALL_FAMILIES = ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM'] as const;

const FIELD_CONDITIONS: Record<string, FieldConditions> = {
  'icw.L': { visible_when: { path: 'termination', operator: 'not_equals', value: 'rollback' } },
  'icw.R': {
    disabled_when: { path: 'coverage_angle', operator: 'greater_than', value: 0 },
    disabled_reason: 'Emergent while coverage hold is enabled.',
  },
  'icw.coverage_angle': {
    disabled_when: { path: 'termination', operator: 'equals', value: 'rollback' },
    disabled_reason: 'Coverage hold applies to flat-baffle termination only.',
  },
  'icw.hold_start': { visible_when: { path: 'termination', operator: 'not_equals', value: 'rollback' } },
  'icw.hold_end': { visible_when: { path: 'termination', operator: 'not_equals', value: 'rollback' } },
  'icw.theta1_deg': { visible_when: { path: 'termination', operator: 'equals', value: 'rollback' } },
  'icw.depth': { visible_when: { path: 'termination', operator: 'equals', value: 'rollback' } },
  'morph.target_exponent': { visible_when: { path: 'morph.target_shape', operator: 'equals', value: 3 } },
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
    editor_bounds: field.min === undefined && field.max === undefined
      ? null
      : { minimum: field.min ?? null, maximum: field.max ?? null },
    step: field.step ?? null,
    precision: field.precision ?? null,
    options: (field.options ?? []).map((option) => ({
      value: option.value,
      label: option.label,
      requires_feature: option.requiresFeature ?? null,
      degraded_without: option.degradedWithout ?? null,
      degraded_label: option.degradedLabel ?? null,
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
    parameters: PARAMETER_REGISTRY.map(serializeField),
  };
}

export const PARAMETER_CATALOG_CONDITIONS = FIELD_CONDITIONS;
