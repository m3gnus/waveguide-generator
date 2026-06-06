const SMOOTH_LENGTH_MIN = 60;
const SMOOTH_LENGTH_MAX = 160;
const SMOOTH_LENGTH_MULTIPLIER = 3;
const SMOOTH_ANGULAR_MIN = 100;
const SMOOTH_ANGULAR_MAX = 240;
const SMOOTH_ANGULAR_MULTIPLIER = 2;
const SMOOTH_CORNER_MIN = 4;
const SMOOTH_CORNER_MAX = 12;
const SMOOTH_CORNER_MULTIPLIER = 2;

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
