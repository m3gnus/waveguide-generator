import { evalParam, toRad } from '../../common.js';

function toFinite(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function resolveCapHeight(vertices, ringCount, params) {
  const sourceShape = Math.round(toFinite(params?.sourceShape, 2));
  if (sourceShape !== 1) return 0;

  let centerX = 0;
  let centerZ = 0;
  for (let i = 0; i < ringCount; i += 1) {
    centerX += vertices[i * 3];
    centerZ += vertices[i * 3 + 2];
  }
  centerX /= ringCount;
  centerZ /= ringCount;

  let maxRadius = 0;
  for (let i = 0; i < ringCount; i += 1) {
    const idx = i * 3;
    maxRadius = Math.max(
      maxRadius,
      Math.hypot(vertices[idx] - centerX, vertices[idx + 2] - centerZ)
    );
  }

  const sourceRadius = toFinite(params?.sourceRadius, -1);
  let height;
  if (sourceRadius > maxRadius) {
    height = sourceRadius - Math.sqrt(Math.max(0, sourceRadius ** 2 - maxRadius ** 2));
  } else {
    const r0 = evalParam(params?.r0 ?? maxRadius, 0);
    const a0 = toRad(evalParam(params?.a0 ?? 0, 0));
    const baseRadius = Number.isFinite(r0) && r0 > 0 ? r0 : maxRadius;
    const capScale = String(params?.type) === 'R-OSSE' ? 0.5 : 1;
    height = baseRadius * Math.tan(a0) * capScale;
  }

  if (!Number.isFinite(height) || height < 0) return 0;
  const sourceCurv = Math.round(toFinite(params?.sourceCurv, 0));
  return sourceCurv === -1 ? -height : height;
}

export function generateThroatSource(vertices, ringCount, fullCircle, params = {}) {
  if (!Number.isFinite(ringCount) || ringCount < 2) {
    return { center: null, edges: [] };
  }

  let centerX = 0;
  let centerY = 0;
  let centerZ = 0;

  for (let i = 0; i < ringCount; i += 1) {
    centerX += vertices[i * 3];
    centerY += vertices[i * 3 + 1];
    centerZ += vertices[i * 3 + 2];
  }

  centerX /= ringCount;
  centerY /= ringCount;
  centerZ /= ringCount;
  centerY += resolveCapHeight(vertices, ringCount, params);

  const segmentCount = fullCircle ? ringCount : Math.max(0, ringCount - 1);
  const edges = [];

  for (let i = 0; i < segmentCount; i += 1) {
    const a = i;
    const b = fullCircle ? (i + 1) % ringCount : i + 1;
    edges.push([b, a]);
  }

  return { center: [centerX, centerY, centerZ], edges };
}
