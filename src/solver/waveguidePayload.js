import { toWireFreeform } from '../config/freeformModel.js';

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
  if (Number.isFinite(numeric)) return numeric;
  // A non-numeric string is an azimuth expression, which the mesher evaluates
  // per phi. Both prepared representations reach here: the backend-mesh params
  // keep the raw text, while prepareGeometryParams compiles to a function with
  // _rawExpr. Falling back on either one silently substitutes a default for
  // the user's formula.
  if (typeof value === 'string' && value.trim() !== '') return value.trim();
  return fallback;
}

function toFiniteNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function requireFiniteNumber(name, value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    throw new Error(
      `buildWaveguidePayload requires finite "${name}" from DesignModule backend-mesh params.`
    );
  }
  return numeric;
}

function requireIntegerNumber(name, value) {
  const numeric = requireFiniteNumber(name, value);
  if (!Number.isInteger(numeric)) {
    throw new Error(
      `buildWaveguidePayload requires integer "${name}" from DesignModule backend-mesh params.`
    );
  }
  return numeric;
}

function requireStringValue(name, value) {
  if (value === undefined || value === null) {
    throw new Error(
      `buildWaveguidePayload requires "${name}" from DesignModule backend-mesh params.`
    );
  }
  return String(value);
}

function normalizeSolverMode(value) {
  const raw = String(value ?? 'auto')
    .trim()
    .toLowerCase()
    .replace(/-/g, '_');
  if (raw === 'auto' || raw === 'automatic') {
    return 'auto';
  }
  if (raw === 'full_3d') {
    return 'full_3d';
  }
  if (raw === 'circsym' || raw === 'circ_sym' || raw === 'axisym' || raw === 'axisymmetric') {
    return 'circsym';
  }
  return 'auto';
}

export function buildWaveguidePayload(preparedParams, mshVersion = '2.2') {
  const type = preparedParams.type || 'R-OSSE';
  const lengthMode =
    preparedParams._athLengthMode ?? preparedParams.athLengthMode ?? preparedParams.lengthMode;
  const payload = {
    formula_type: type,
    length_mode:
      lengthMode != null && String(lengthMode).trim() ? String(lengthMode).trim() : undefined,

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

    // ICW formula (intrinsic-curvature waveguide; solved server-side by the mesher)
    termination:
      preparedParams.termination != null && String(preparedParams.termination).trim()
        ? String(preparedParams.termination).trim()
        : undefined,
    n_coeff: toFiniteNumber(preparedParams.n_coeff, undefined),
    theta1_deg: toNumberOrExpr(preparedParams.theta1_deg, undefined),
    depth: toFiniteNumber(preparedParams.depth, undefined),
    coverage_angle: toFiniteNumber(preparedParams.coverage_angle, undefined),
    hold_start: toFiniteNumber(preparedParams.hold_start, undefined),
    hold_end: toFiniteNumber(preparedParams.hold_end, undefined),

    // Shared formula
    a: toExprString(preparedParams.a),
    r0: toNumberOrExpr(preparedParams.r0, 12.7),
    a0: toNumberOrExpr(preparedParams.a0, 15.5),
    k: toNumberOrExpr(preparedParams.k, 2.0),
    q: toNumberOrExpr(preparedParams.q, 3.4),

    // Throat geometry
    throat_profile: toFiniteNumber(preparedParams.throatProfile, 1),
    throat_ext_angle: toNumberOrExpr(preparedParams.throatExtAngle, 0),
    throat_ext_length: toNumberOrExpr(preparedParams.throatExtLength, 0),
    slot_length: toNumberOrExpr(preparedParams.slotLength, 0),
    rot: toNumberOrExpr(preparedParams.rot, 0),

    // Circular arc
    // Every field below that FORMULA_FIELD_ALLOWLIST marks as formula-capable
    // must go through toNumberOrExpr/toExprString, never toFiniteNumber: a
    // prepared expression is a compiled function, so Number(fn) is NaN and the
    // fallback would silently replace the user's formula (and String(fn) would
    // ship the closure source to the mesher). The payload is the only crossing
    // between the JS viewport and the Python mesher, so a value dropped here
    // makes the solved mesh disagree with what is on screen.
    circ_arc_term_angle: toNumberOrExpr(preparedParams.circArcTermAngle, 1),
    circ_arc_radius: toNumberOrExpr(preparedParams.circArcRadius, 0),

    // Guiding curve
    gcurve_type: toFiniteNumber(preparedParams.gcurveType, 0),
    // Unset means "at the mouth" (1.0) in both engines; see the JS default in
    // profiles/osse.js and hornlab_mesher.profile_morph.
    gcurve_dist: toNumberOrExpr(preparedParams.gcurveDist, 1),
    gcurve_width: toNumberOrExpr(preparedParams.gcurveWidth, 0),
    gcurve_aspect_ratio: toNumberOrExpr(preparedParams.gcurveAspectRatio, 1),
    gcurve_se_n: toNumberOrExpr(preparedParams.gcurveSeN, 3),
    gcurve_sf: toExprString(preparedParams.gcurveSf),
    gcurve_sf_a: toExprString(preparedParams.gcurveSfA),
    gcurve_sf_b: toExprString(preparedParams.gcurveSfB),
    gcurve_sf_m1: toExprString(preparedParams.gcurveSfM1),
    gcurve_sf_m2: toExprString(preparedParams.gcurveSfM2),
    gcurve_sf_n1: toExprString(preparedParams.gcurveSfN1),
    gcurve_sf_n2: toExprString(preparedParams.gcurveSfN2),
    gcurve_sf_n3: toExprString(preparedParams.gcurveSfN3),
    gcurve_rot: toNumberOrExpr(preparedParams.gcurveRot, 0),

    // Morph
    morph_target: toFiniteNumber(preparedParams.morphTarget, 0),
    morph_width: toNumberOrExpr(preparedParams.morphWidth, 0),
    morph_height: toNumberOrExpr(preparedParams.morphHeight, 0),
    morph_corner: toNumberOrExpr(preparedParams.morphCorner, 0),
    morph_rate: toNumberOrExpr(preparedParams.morphRate, 3.0),
    morph_fixed: toNumberOrExpr(preparedParams.morphFixed, 0),
    morph_allow_shrinkage: toFiniteNumber(preparedParams.morphAllowShrinkage, 0),

    // Geometry grid
    n_angular: requireIntegerNumber('angularSegments', preparedParams.angularSegments),
    n_length: requireIntegerNumber('lengthSegments', preparedParams.lengthSegments),
    quadrants: requireIntegerNumber('quadrants', preparedParams.quadrants),
    sampling_mode:
      preparedParams.samplingMode != null && String(preparedParams.samplingMode).trim()
        ? String(preparedParams.samplingMode)
        : undefined,
    z_map_points:
      preparedParams.zMapPoints != null && String(preparedParams.zMapPoints).trim()
        ? String(preparedParams.zMapPoints)
        : undefined,

    // BEM mesh element sizes
    throat_res: requireFiniteNumber('throatResolution', preparedParams.throatResolution),
    mouth_res: requireFiniteNumber('mouthResolution', preparedParams.mouthResolution),
    rear_res: requireFiniteNumber('rearResolution', preparedParams.rearResolution),
    aperture_resolution_scale: requireFiniteNumber(
      'apertureResolutionScale',
      preparedParams.apertureResolutionScale
    ),
    max_triangles: toFiniteNumber(preparedParams.maxTriangles, 50000),
    allow_large_mesh: preparedParams.allowLargeMesh === true,
    wall_thickness: requireFiniteNumber('wallThickness', preparedParams.wallThickness),

    // Enclosure
    enc_depth: toFiniteNumber(preparedParams.encDepth, 0),
    enc_space_l: toFiniteNumber(preparedParams.encSpaceL, 25),
    enc_space_t: toFiniteNumber(preparedParams.encSpaceT, 25),
    enc_space_r: toFiniteNumber(preparedParams.encSpaceR, 25),
    enc_space_b: toFiniteNumber(preparedParams.encSpaceB, 25),
    enc_edge: toFiniteNumber(preparedParams.encEdge, 18),
    enc_edge_type: toFiniteNumber(preparedParams.encEdgeType, 1),
    corner_segments: requireIntegerNumber('cornerSegments', preparedParams.cornerSegments),
    enc_front_resolution: requireStringValue(
      'encFrontResolution',
      preparedParams.encFrontResolution
    ),
    enc_back_resolution: requireStringValue('encBackResolution', preparedParams.encBackResolution),

    // Source definition
    source_shape: toFiniteNumber(preparedParams.sourceShape, 2),
    source_radius: toFiniteNumber(preparedParams.sourceRadius, -1),
    source_curv: toFiniteNumber(preparedParams.sourceCurv, 0),
    source_velocity: toFiniteNumber(preparedParams.sourceVelocity, 1),
    source_contours:
      preparedParams.sourceContours != null && String(preparedParams.sourceContours).trim()
        ? String(preparedParams.sourceContours)
        : undefined,

    // Export coordinate offset
    vertical_offset: toFiniteNumber(preparedParams.verticalOffset, 0),

    // Simulation / output
    sim_type: toFiniteNumber(preparedParams.simType, 2),
    solver_mode: normalizeSolverMode(preparedParams.solverMode),
    msh_version: mshVersion,
  };

  if (type === 'FREEFORM') {
    Object.assign(payload, toWireFreeform(preparedParams));
    for (const key of [
      'R',
      'r',
      'b',
      'm',
      'tmax',
      'L',
      's',
      'n',
      'h',
      'termination',
      'n_coeff',
      'theta1_deg',
      'depth',
      'coverage_angle',
      'hold_start',
      'hold_end',
      'a',
      'a0',
      'r0',
      'k',
      'q',
    ]) {
      delete payload[key];
    }
  }

  return payload;
}
