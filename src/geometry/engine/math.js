export const lerp = (a, b, t) => a + (b - a) * t;

export const safeDiv = (num, den, fallback = 0) => (
  Math.abs(den) > 1e-12 ? num / den : fallback
);
