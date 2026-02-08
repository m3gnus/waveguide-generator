export const HORN_PROFILES = Object.freeze({
  STANDARD: 1,
  CIRCULAR_ARC: 3
});

export const GUIDING_CURVES = Object.freeze({
  NONE: 0,
  SUPERELLIPSE: 1,
  SUPERFORMULA: 2
});

export const MORPH_TARGETS = Object.freeze({
  NONE: 0,
  RECTANGLE: 1,
  CIRCLE: 2
});

export const DEFAULTS = Object.freeze({
  ANGULAR_SEGMENTS: 80,
  LENGTH_SEGMENTS: 40,
  K: 1,
  N: 4,
  Q: 1,
  M: 0.85,
  R: 0.4,
  B: 0.2,
  TMAX: 1.0,
  EPS: 1e-9
});
