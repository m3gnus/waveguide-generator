const SMOOTH_LENGTH_MIN = 60;
const SMOOTH_LENGTH_MAX = 160;
const SMOOTH_LENGTH_MULTIPLIER = 3;
const SMOOTH_ANGULAR_MIN = 100;
const SMOOTH_ANGULAR_MAX = 240;
const SMOOTH_ANGULAR_MULTIPLIER = 2;
const SMOOTH_CORNER_MIN = 4;
const SMOOTH_CORNER_MAX = 12;
const SMOOTH_CORNER_MULTIPLIER = 2;

const VIEWPORT_GRID_LENGTH_SEGMENTS = 48;
const VIEWPORT_GRID_ANGULAR_SEGMENTS = 96;
const VIEWPORT_GRID_CORNER_SEGMENTS = 6;
const VIEWPORT_SMOOTH_LENGTH_SEGMENTS = 80;
const VIEWPORT_SMOOTH_ANGULAR_SEGMENTS = 160;
const VIEWPORT_SMOOTH_CORNER_SEGMENTS = 8;

function clampInt(value, min, max) {
  const n = Math.round(Number(value) || 0);
  if (n < min) return min;
  if (n > max) return max;
  return n;
}

function snapMultipleOfFour(value) {
  return Math.max(4, Math.round(value / 4) * 4);
}

export function densifyForSmoothTessellation(preparedParams) {
  const userLength = Number(preparedParams.lengthSegments) || 0;
  const userAngular = Number(preparedParams.angularSegments) || 0;
  const userCorner = Number(preparedParams.cornerSegments) || 0;

  const lengthSegments = clampInt(
    Math.max(userLength * SMOOTH_LENGTH_MULTIPLIER, SMOOTH_LENGTH_MIN),
    SMOOTH_LENGTH_MIN,
    SMOOTH_LENGTH_MAX
  );
  const angularRaw = Math.max(userAngular * SMOOTH_ANGULAR_MULTIPLIER, SMOOTH_ANGULAR_MIN);
  const angularSegments = clampInt(
    snapMultipleOfFour(angularRaw),
    SMOOTH_ANGULAR_MIN,
    SMOOTH_ANGULAR_MAX
  );
  const cornerSegments = clampInt(
    Math.max(userCorner * SMOOTH_CORNER_MULTIPLIER, SMOOTH_CORNER_MIN),
    SMOOTH_CORNER_MIN,
    SMOOTH_CORNER_MAX
  );

  return {
    ...preparedParams,
    lengthSegments,
    angularSegments,
    cornerSegments,
  };
}

function withViewportSampling(preparedParams, sampling) {
  return {
    ...preparedParams,
    ...sampling,
    // Mesh resolution fields are element-size controls for solve/export.
    // Keep viewport slicing render-only unless the explicit preview bias is set.
    throatResolution:
      preparedParams.throatSliceDensity == null ? 1 : preparedParams.throatResolution,
    mouthResolution: preparedParams.throatSliceDensity == null ? 1 : preparedParams.mouthResolution,
  };
}

export function prepareViewportTessellationParams(preparedParams, { variant = 'grid' } = {}) {
  if (variant === 'smooth') {
    return withViewportSampling(preparedParams, {
      lengthSegments: VIEWPORT_SMOOTH_LENGTH_SEGMENTS,
      angularSegments: VIEWPORT_SMOOTH_ANGULAR_SEGMENTS,
      cornerSegments: VIEWPORT_SMOOTH_CORNER_SEGMENTS,
    });
  }

  return withViewportSampling(preparedParams, {
    lengthSegments: VIEWPORT_GRID_LENGTH_SEGMENTS,
    angularSegments: VIEWPORT_GRID_ANGULAR_SEGMENTS,
    cornerSegments: VIEWPORT_GRID_CORNER_SEGMENTS,
  });
}
