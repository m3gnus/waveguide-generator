function toExprString(value) {
  if (value == null) return undefined;
  if (typeof value === 'function') {
    return value._rawExpr != null ? String(value._rawExpr) : undefined;
  }
  return String(value);
}

function toNumberOrExpr(value, fallback) {
  if (value == null || value === '') return fallback;
  if (typeof value === 'function') {
    const expr = toExprString(value);
    return expr !== undefined ? expr : fallback;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function toFiniteNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function requireFiniteNumber(name, value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    throw new Error(
      `buildWaveguidePayload requires finite "${name}" from DesignModule OCC-normalized params.`
    );
  }
  return numeric;
}

function requireIntegerNumber(name, value) {
  const numeric = requireFiniteNumber(name, value);
  if (!Number.isInteger(numeric)) {
    throw new Error(
      `buildWaveguidePayload requires integer "${name}" from DesignModule OCC-normalized params.`
    );
  }
  return numeric;
}

function requireStringValue(name, value) {
  if (value === undefined || value === null) {
    throw new Error(
      `buildWaveguidePayload requires "${name}" from DesignModule OCC-normalized params.`
    );
  }
  return String(value);
}

export function buildWaveguidePayload(preparedParams, mshVersion = '2.2') {
  const type = preparedParams.type || 'R-OSSE';
  return {
    formula_type: type,

    // R-OSSE formula
    R: toExprString(preparedParams.R),
    r: toNumberOrExpr(preparedParams.r, 0.4),
    b: toNumberOrExpr(preparedParams.b, 0.2),
    m: toNumberOrExpr(preparedParams.m, 0.85),
    tmax: toNumberOrExpr(preparedParams.tmax, 1.0),

    // OSSE formula
    L: toExprString(preparedParams.L),
    s: toExprString(preparedParams.s),
    n: toNumberOrExpr(preparedParams.n, 4.158),
    h: toNumberOrExpr(preparedParams.h, 0.0),

    // Shared formula
    a: toExprString(preparedParams.a),
    r0: toNumberOrExpr(preparedParams.r0, 12.7),
    a0: toNumberOrExpr(preparedParams.a0, 15.5),
    k: toNumberOrExpr(preparedParams.k, 2.0),
    q: toNumberOrExpr(preparedParams.q, 3.4),

    // Throat geometry
    throat_profile: toFiniteNumber(preparedParams.throatProfile, 1),
    throat_ext_angle: toFiniteNumber(preparedParams.throatExtAngle, 0),
    throat_ext_length: toFiniteNumber(preparedParams.throatExtLength, 0),
    slot_length: toFiniteNumber(preparedParams.slotLength, 0),
    rot: toFiniteNumber(preparedParams.rot, 0),

    // Circular arc
    circ_arc_term_angle: toFiniteNumber(preparedParams.circArcTermAngle, 1),
    circ_arc_radius: toFiniteNumber(preparedParams.circArcRadius, 0),

    // Guiding curve
    gcurve_type: toFiniteNumber(preparedParams.gcurveType, 0),
    gcurve_dist: toFiniteNumber(preparedParams.gcurveDist, 0.5),
    gcurve_width: toFiniteNumber(preparedParams.gcurveWidth, 0),
    gcurve_aspect_ratio: toFiniteNumber(preparedParams.gcurveAspectRatio, 1),
    gcurve_se_n: toFiniteNumber(preparedParams.gcurveSeN, 3),
    gcurve_sf: preparedParams.gcurveSf != null ? String(preparedParams.gcurveSf) : undefined,
    gcurve_sf_a: preparedParams.gcurveSfA != null ? String(preparedParams.gcurveSfA) : undefined,
    gcurve_sf_b: preparedParams.gcurveSfB != null ? String(preparedParams.gcurveSfB) : undefined,
    gcurve_sf_m1: preparedParams.gcurveSfM1 != null ? String(preparedParams.gcurveSfM1) : undefined,
    gcurve_sf_m2: preparedParams.gcurveSfM2 != null ? String(preparedParams.gcurveSfM2) : undefined,
    gcurve_sf_n1: preparedParams.gcurveSfN1 != null ? String(preparedParams.gcurveSfN1) : undefined,
    gcurve_sf_n2: preparedParams.gcurveSfN2 != null ? String(preparedParams.gcurveSfN2) : undefined,
    gcurve_sf_n3: preparedParams.gcurveSfN3 != null ? String(preparedParams.gcurveSfN3) : undefined,
    gcurve_rot: toFiniteNumber(preparedParams.gcurveRot, 0),

    // Morph
    morph_target: toFiniteNumber(preparedParams.morphTarget, 0),
    morph_width: toFiniteNumber(preparedParams.morphWidth, 0),
    morph_height: toFiniteNumber(preparedParams.morphHeight, 0),
    morph_corner: toFiniteNumber(preparedParams.morphCorner, 0),
    morph_rate: toFiniteNumber(preparedParams.morphRate, 3.0),
    morph_fixed: toFiniteNumber(preparedParams.morphFixed, 0),
    morph_allow_shrinkage: toFiniteNumber(preparedParams.morphAllowShrinkage, 0),

    // Geometry grid
    n_angular: requireIntegerNumber('angularSegments', preparedParams.angularSegments),
    n_length: requireIntegerNumber('lengthSegments', preparedParams.lengthSegments),

    // BEM mesh element sizes
    throat_res: requireFiniteNumber('throatResolution', preparedParams.throatResolution),
    mouth_res: requireFiniteNumber('mouthResolution', preparedParams.mouthResolution),
    rear_res: requireFiniteNumber('rearResolution', preparedParams.rearResolution),
    wall_thickness: requireFiniteNumber('wallThickness', preparedParams.wallThickness),

    // Enclosure
    enc_depth: toFiniteNumber(preparedParams.encDepth, 0),
    enc_space_l: toFiniteNumber(preparedParams.encSpaceL, 25),
    enc_space_t: toFiniteNumber(preparedParams.encSpaceT, 25),
    enc_space_r: toFiniteNumber(preparedParams.encSpaceR, 25),
    enc_space_b: toFiniteNumber(preparedParams.encSpaceB, 25),
    enc_edge: toFiniteNumber(preparedParams.encEdge, 18),
    enc_edge_type: toFiniteNumber(preparedParams.encEdgeType, 1),
    corner_segments: toFiniteNumber(preparedParams.cornerSegments, 4),
    enc_front_resolution: requireStringValue('encFrontResolution', preparedParams.encFrontResolution),
    enc_back_resolution: requireStringValue('encBackResolution', preparedParams.encBackResolution),

    // Source definition
    source_shape: toFiniteNumber(preparedParams.sourceShape, 2),
    source_radius: toFiniteNumber(preparedParams.sourceRadius, -1),
    source_curv: toFiniteNumber(preparedParams.sourceCurv, 0),
    source_velocity: toFiniteNumber(preparedParams.sourceVelocity, 1),
    source_contours: preparedParams.sourceContours != null && String(preparedParams.sourceContours).trim()
      ? String(preparedParams.sourceContours)
      : undefined,

    // Export coordinate offset
    vertical_offset: toFiniteNumber(preparedParams.verticalOffset, 0),

    // Simulation / output
    sim_type: 2,
    msh_version: mshVersion
  };
}
