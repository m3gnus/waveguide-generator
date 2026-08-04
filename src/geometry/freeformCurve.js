const EPSILON = 1e-12;
const DEFAULT_SAMPLE_COUNT = 96;

function finitePoint(point) {
  return (
    Array.isArray(point) &&
    point.length >= 2 &&
    Number.isFinite(Number(point[0])) &&
    Number.isFinite(Number(point[1]))
  );
}

function pchipEndpointSlope(h0, h1, delta0, delta1) {
  let slope = ((2 * h0 + h1) * delta0 - h0 * delta1) / (h0 + h1);
  if (Math.sign(slope) !== Math.sign(delta0)) return 0;
  if (Math.sign(delta0) !== Math.sign(delta1) && Math.abs(slope) > Math.abs(3 * delta0)) {
    slope = 3 * delta0;
  }
  return slope;
}

// Fritsch-Carlson weighted harmonic slopes for one scalar coordinate.
function pchipSlopes(parameters, values) {
  const count = values.length;
  if (count === 2) {
    const slope = (values[1] - values[0]) / (parameters[1] - parameters[0]);
    return [slope, slope];
  }

  const h = [];
  const delta = [];
  for (let index = 0; index < count - 1; index += 1) {
    h.push(parameters[index + 1] - parameters[index]);
    delta.push((values[index + 1] - values[index]) / h[index]);
  }

  const slopes = new Array(count).fill(0);
  slopes[0] = pchipEndpointSlope(h[0], h[1], delta[0], delta[1]);
  slopes[count - 1] = pchipEndpointSlope(
    h[count - 2],
    h[count - 3],
    delta[count - 2],
    delta[count - 3]
  );
  for (let index = 1; index < count - 1; index += 1) {
    const before = delta[index - 1];
    const after = delta[index];
    if (before === 0 || after === 0 || Math.sign(before) !== Math.sign(after)) {
      slopes[index] = 0;
      continue;
    }
    const beforeWeight = 2 * h[index] + h[index - 1];
    const afterWeight = h[index] + 2 * h[index - 1];
    slopes[index] = (beforeWeight + afterWeight) / (beforeWeight / before + afterWeight / after);
  }
  return slopes;
}

function hermite(value0, value1, slope0, slope1, span, fraction) {
  const u2 = fraction * fraction;
  const u3 = u2 * fraction;
  return (
    (2 * u3 - 3 * u2 + 1) * value0 +
    (u3 - 2 * u2 + fraction) * span * slope0 +
    (-2 * u3 + 3 * u2) * value1 +
    (u3 - u2) * span * slope1
  );
}

function hermiteCoefficients(value0, value1, slope0, slope1, span) {
  return [
    2 * value0 - 2 * value1 + span * (slope0 + slope1),
    -3 * value0 + 3 * value1 - span * (2 * slope0 + slope1),
    span * slope0,
    value0,
  ];
}

function quadraticRoots(a, b, c) {
  if (Math.abs(a) <= 1e-15) {
    if (Math.abs(b) <= 1e-15) return [];
    return [-c / b];
  }
  const discriminant = b * b - 4 * a * c;
  if (discriminant < 0) return [];
  const root = Math.sqrt(Math.max(0, discriminant));
  return [(-b - root) / (2 * a), (-b + root) / (2 * a)];
}

function derivativeRoots(coefficients) {
  return quadraticRoots(3 * coefficients[0], 2 * coefficients[1], coefficients[2]).filter(
    (value) => value >= -1e-12 && value <= 1 + 1e-12
  );
}

function polynomialValue(coefficients, value) {
  return (
    ((coefficients[0] * value + coefficients[1]) * value + coefficients[2]) * value +
    coefficients[3]
  );
}

function segmentIsFeasible(anchors, parameters, zSlopes, radiusSlopes, segment) {
  const span = parameters[segment + 1] - parameters[segment];
  const zCoefficients = hermiteCoefficients(
    anchors[segment][0],
    anchors[segment + 1][0],
    zSlopes[segment],
    zSlopes[segment + 1],
    span
  );
  const radiusCoefficients = hermiteCoefficients(
    anchors[segment][1],
    anchors[segment + 1][1],
    radiusSlopes[segment],
    radiusSlopes[segment + 1],
    span
  );

  const derivativeCandidates = [0, 1];
  if (Math.abs(zCoefficients[0]) > 1e-15) {
    const vertex = -zCoefficients[1] / (3 * zCoefficients[0]);
    if (vertex > 0 && vertex < 1) derivativeCandidates.push(vertex);
  }
  const derivativeTolerance =
    1e-11 * Math.max(1, Math.abs(zCoefficients[2]), Math.abs(zCoefficients[1]));
  if (
    Math.min(
      ...derivativeCandidates.map(
        (value) => (3 * zCoefficients[0] * value + 2 * zCoefficients[1]) * value + zCoefficients[2]
      )
    ) < -derivativeTolerance
  ) {
    return false;
  }
  const lastSegment = anchors.length - 2;
  if (
    derivativeRoots(zCoefficients).some(
      (root) =>
        !((segment === 0 && root <= 1e-10) || (segment === lastSegment && 1 - root <= 1e-10))
    )
  ) {
    return false;
  }

  const radiusCandidates = [0, 1, ...derivativeRoots(radiusCoefficients)];
  const radii = radiusCandidates.map((value) => polynomialValue(radiusCoefficients, value));
  if (radii.some((value) => !Number.isFinite(value) || value <= 0)) return false;
  const radius0 = anchors[segment][1];
  const radius1 = anchors[segment + 1][1];
  const lower = Math.min(radius0, radius1);
  const upper = Math.max(radius0, radius1);
  const tolerance = Math.max(0.05, 1e-3 * Math.max(Math.abs(lower), Math.abs(upper)));
  return Math.max(lower - Math.min(...radii), Math.max(...radii) - upper, 0) <= tolerance;
}

function sanitizePoints(points) {
  const valid = (Array.isArray(points) ? points : []).filter(finitePoint).map((point) => {
    const row = [Number(point[0]), Number(point[1])];
    const angleDeg = Number(point[2]);
    if (point.length >= 3 && Number.isFinite(angleDeg)) row.push(angleDeg);
    return row;
  });
  if (valid.length < 2) return [];

  const unique = [];
  for (const point of valid) {
    const previous = unique.at(-1);
    if (!previous || Math.hypot(point[0] - previous[0], point[1] - previous[1]) > EPSILON) {
      unique.push(point);
    }
  }
  return unique.length >= 2 ? unique : [];
}

/**
 * Build a display-only mirror of the FREEFORM mesher's chord-parameterized
 * PCHIP/Hermite meridian. Per-anchor directions replace the automatic PCHIP
 * directions while retaining their automatic speeds. The mesher may reduce a
 * tangent speed to preserve monotonicity; authoritative server samples replace
 * this immediate display curve as soon as they arrive.
 * Endpoint rows take precedence over the block-level endpoint controls.
 */
export function buildFreeformDisplayCurve({
  points,
  throatAngleDeg = 0,
  mouthAngleDeg = 0,
  sampleCount = DEFAULT_SAMPLE_COUNT,
} = {}) {
  const anchors = sanitizePoints(points);
  if (anchors.length < 2) return anchors;

  const parameters = [0];
  for (let index = 1; index < anchors.length; index += 1) {
    parameters.push(
      parameters[index - 1] +
        Math.max(
          EPSILON,
          Math.hypot(
            anchors[index][0] - anchors[index - 1][0],
            anchors[index][1] - anchors[index - 1][1]
          )
        )
    );
  }

  const automaticZSlopes = pchipSlopes(
    parameters,
    anchors.map((point) => point[0])
  );
  const automaticRadiusSlopes = pchipSlopes(
    parameters,
    anchors.map((point) => point[1])
  );
  const automaticSpeeds = automaticZSlopes.map((slope, index) =>
    Math.hypot(slope, automaticRadiusSlopes[index])
  );
  const directions = automaticSpeeds.map((speed, index) =>
    speed > 0 ? [automaticZSlopes[index] / speed, automaticRadiusSlopes[index] / speed] : [1, 0]
  );
  const overrideDirection = (index, angleDeg) => {
    const radians = (Number(angleDeg) * Math.PI) / 180;
    directions[index] = [Math.cos(radians), Math.sin(radians)];
  };
  if (anchors[0].length < 3) overrideDirection(0, throatAngleDeg);
  if (anchors.at(-1).length < 3) {
    overrideDirection(anchors.length - 1, mouthAngleDeg);
  }
  anchors.forEach((anchor, index) => {
    if (anchor.length < 3) return;
    overrideDirection(index, anchor[2]);
  });

  const slopesFor = (factors) => ({
    z: factors.map((factor, index) => automaticSpeeds[index] * factor * directions[index][0]),
    radius: factors.map((factor, index) => automaticSpeeds[index] * factor * directions[index][1]),
  });
  const violatingSegments = (factors, only = null) => {
    const slopes = slopesFor(factors);
    const segments = only ?? Array.from({ length: anchors.length - 1 }, (_value, index) => index);
    return segments.filter(
      (segment) => !segmentIsFeasible(anchors, parameters, slopes.z, slopes.radius, segment)
    );
  };

  const factors = new Array(anchors.length).fill(1);
  let violations = violatingSegments(factors);
  for (let iteration = 0; violations.length && iteration < 4 * anchors.length + 8; iteration += 1) {
    for (const segment of [...new Set(violations)].sort((left, right) => left - right)) {
      if (violatingSegments(factors, [segment]).length === 0) continue;
      let feasibleScale = null;
      let trialScale = 0.5;
      for (let search = 0; search < 48; search += 1) {
        const trial = [...factors];
        trial[segment] *= trialScale;
        trial[segment + 1] *= trialScale;
        if (violatingSegments(trial, [segment]).length === 0) {
          feasibleScale = trialScale;
          break;
        }
        trialScale *= 0.5;
      }
      if (feasibleScale === null) continue;
      let lower = feasibleScale;
      let upper = Math.min(1, 2 * feasibleScale);
      for (let count = 0; count < 24; count += 1) {
        const midpoint = 0.5 * (lower + upper);
        const trial = [...factors];
        trial[segment] *= midpoint;
        trial[segment + 1] *= midpoint;
        if (violatingSegments(trial, [segment]).length) upper = midpoint;
        else lower = midpoint;
      }
      factors[segment] *= lower;
      factors[segment + 1] *= lower;
    }
    violations = violatingSegments(factors);
  }

  for (let index = 0; index < factors.length; index += 1) {
    const trial = [...factors];
    trial[index] = 1;
    if (violatingSegments(trial).length === 0) {
      factors[index] = 1;
      continue;
    }
    let lower = factors[index];
    let upper = 1;
    for (let count = 0; count < 24; count += 1) {
      const midpoint = 0.5 * (lower + upper);
      trial[index] = midpoint;
      if (violatingSegments(trial).length) upper = midpoint;
      else lower = midpoint;
    }
    factors[index] = lower;
  }
  const { z: zSlopes, radius: radiusSlopes } = slopesFor(factors);

  const totalLength = parameters.at(-1);
  const targetSamples = Math.max(
    anchors.length,
    Math.round(Number(sampleCount)) || DEFAULT_SAMPLE_COUNT
  );
  const samples = [];
  for (let segment = 0; segment < anchors.length - 1; segment += 1) {
    const span = parameters[segment + 1] - parameters[segment];
    const segmentSamples = Math.max(1, Math.round(((targetSamples - 1) * span) / totalLength));
    for (let step = 0; step < segmentSamples; step += 1) {
      const fraction = step / segmentSamples;
      samples.push([
        hermite(
          anchors[segment][0],
          anchors[segment + 1][0],
          zSlopes[segment],
          zSlopes[segment + 1],
          span,
          fraction
        ),
        hermite(
          anchors[segment][1],
          anchors[segment + 1][1],
          radiusSlopes[segment],
          radiusSlopes[segment + 1],
          span,
          fraction
        ),
      ]);
    }
  }
  samples.push(anchors.at(-1).slice(0, 2));
  return samples;
}
