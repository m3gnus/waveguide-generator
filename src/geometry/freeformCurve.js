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

function sanitizePoints(points) {
  const valid = (Array.isArray(points) ? points : []).filter(finitePoint).map((point) => {
    const row = [Number(point[0]), Number(point[1])];
    const angleDeg = Number(point[2]);
    if (point.length >= 3 && Number.isFinite(angleDeg)) row.push(angleDeg);
    const strength = Number(point[3]);
    if (row.length === 3 && point.length >= 4 && Number.isFinite(strength)) row.push(strength);
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
 * directions while retaining their automatic speeds and applying strength.
 * Endpoint rows take precedence over the block-level endpoint controls.
 */
export function buildFreeformDisplayCurve({
  points,
  throatAngleDeg = 0,
  mouthAngleDeg = 0,
  throatTangentScale = 1,
  mouthTangentScale = 1,
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

  const zSlopes = pchipSlopes(
    parameters,
    anchors.map((point) => point[0])
  );
  const radiusSlopes = pchipSlopes(
    parameters,
    anchors.map((point) => point[1])
  );
  const overrideEndpoint = (index, angleDeg, scale) => {
    const automaticSpeed = Math.hypot(zSlopes[index], radiusSlopes[index]);
    const safeScale = Number.isFinite(Number(scale)) ? Number(scale) : 1;
    const radians = (Number(angleDeg) * Math.PI) / 180;
    const speed = automaticSpeed * safeScale;
    zSlopes[index] = speed * Math.cos(radians);
    radiusSlopes[index] = speed * Math.sin(radians);
  };
  if (anchors[0].length < 3) overrideEndpoint(0, throatAngleDeg, throatTangentScale);
  if (anchors.at(-1).length < 3) {
    overrideEndpoint(anchors.length - 1, mouthAngleDeg, mouthTangentScale);
  }
  anchors.forEach((anchor, index) => {
    if (anchor.length < 3) return;
    const automaticSpeed = Math.hypot(zSlopes[index], radiusSlopes[index]);
    const strength = anchor.length >= 4 ? anchor[3] : 1;
    const radians = (anchor[2] * Math.PI) / 180;
    zSlopes[index] = automaticSpeed * strength * Math.cos(radians);
    radiusSlopes[index] = automaticSpeed * strength * Math.sin(radians);
  });

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

function finiteCurveSamples(curveSamples) {
  return (Array.isArray(curveSamples) ? curveSamples : [])
    .filter(finitePoint)
    .map((point) => [Number(point[0]), Number(point[1])]);
}

function finiteDifferenceDerivatives(points) {
  const count = points.length;
  const parameters = [0];
  for (let index = 1; index < count; index += 1) {
    parameters.push(
      parameters[index - 1] +
        Math.max(
          EPSILON,
          Math.hypot(
            points[index][0] - points[index - 1][0],
            points[index][1] - points[index - 1][1]
          )
        )
    );
  }

  const derivatives = [];
  for (let index = 0; index < count; index += 1) {
    const center = index === 0 ? 1 : index === count - 1 ? count - 2 : index;
    const before = center - 1;
    const after = center + 1;
    const hBefore = parameters[center] - parameters[before];
    const hAfter = parameters[after] - parameters[center];
    const first = [0, 0];
    const second = [0, 0];

    for (let axis = 0; axis < 2; axis += 1) {
      const beforeValue = points[before][axis];
      const centerValue = points[center][axis];
      const afterValue = points[after][axis];
      const centerFirst =
        (-hAfter * beforeValue) / (hBefore * (hBefore + hAfter)) +
        ((hAfter - hBefore) * centerValue) / (hBefore * hAfter) +
        (hBefore * afterValue) / (hAfter * (hBefore + hAfter));
      const centerSecond =
        2 *
        (beforeValue / (hBefore * (hBefore + hAfter)) -
          centerValue / (hBefore * hAfter) +
          afterValue / (hAfter * (hBefore + hAfter)));

      if (index === 0) {
        first[axis] = centerFirst - hBefore * centerSecond;
      } else if (index === count - 1) {
        first[axis] = centerFirst + hAfter * centerSecond;
      } else {
        first[axis] = centerFirst;
      }
      second[axis] = centerSecond;
    }
    derivatives.push({ first, second });
  }
  return derivatives;
}

/**
 * Find visible S-shaped portions of a sampled meridian. This mirrors the
 * mesher's one-way-curvature diagnostic using finite differences: negative
 * z' r'' - r' z'' samples are grouped, and sub-degree tangent dips are ignored.
 */
export function computeInflectionSpans(curveSamples) {
  const points = finiteCurveSamples(curveSamples);
  if (points.length < 3) return [];

  const derivatives = finiteDifferenceDerivatives(points);
  const negative = derivatives.map(
    ({ first, second }) => first[0] * second[1] - first[1] * second[0] < 0
  );
  const spans = [];
  let start = null;
  for (let index = 0; index <= negative.length; index += 1) {
    if (negative[index] && start === null) {
      start = index;
      continue;
    }
    if ((index === negative.length || !negative[index]) && start !== null) {
      const end = index - 1;
      const startAngle = Math.atan2(derivatives[start].first[1], derivatives[start].first[0]);
      const endAngle = Math.atan2(derivatives[end].first[1], derivatives[end].first[0]);
      const tangentDropDeg = ((startAngle - endAngle) * 180) / Math.PI;
      if (tangentDropDeg > 1) {
        spans.push({
          zStartMm: points[start][0],
          zEndMm: points[end][0],
          tangentDropDeg,
        });
      }
      start = null;
    }
  }
  return spans;
}
