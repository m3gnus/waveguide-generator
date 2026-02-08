import { evalParam } from '../../common.js';
import { DEFAULTS } from '../constants.js';

function buildUniformAngles(segmentCount) {
  return Array.from({ length: segmentCount }, (_, i) => (i / segmentCount) * Math.PI * 2);
}

function buildQuadrantAngles(pointsPerQuadrant, halfW, halfH, cornerR, cornerSegments) {
  if (!Number.isFinite(halfW) || !Number.isFinite(halfH) || halfW <= 0 || halfH <= 0) {
    return null;
  }

  const maxCorner = Math.max(0, Math.min(halfW, halfH) - 1e-6);
  const clampedCorner = Math.min(cornerR, maxCorner);

  if (clampedCorner <= 0 || cornerSegments <= 0) {
    return Array.from({ length: pointsPerQuadrant + 1 }, (_, i) => (Math.PI / 2) * (i / pointsPerQuadrant));
  }

  const theta1 = Math.atan2(halfH - clampedCorner, halfW);
  const theta2 = Math.atan2(halfH, halfW - clampedCorner);
  const remainingSegments = Math.max(1, pointsPerQuadrant - cornerSegments);

  const side1Seg = Math.max(1, Math.min(
    remainingSegments - 1,
    Math.round(remainingSegments * theta1 / (theta1 + Math.max(0, (Math.PI / 2) - theta2)))
  ));
  const side2Seg = Math.max(1, remainingSegments - side1Seg);

  const angles = [];
  for (let i = 0; i <= side1Seg; i += 1) angles.push(theta1 * (i / side1Seg));

  const cx = halfW - clampedCorner;
  const cy = halfH - clampedCorner;
  for (let i = 1; i < cornerSegments; i += 1) {
    const phi = (i / (cornerSegments - 1)) * (Math.PI / 2);
    angles.push(Math.atan2(cy + clampedCorner * Math.sin(phi), cx + clampedCorner * Math.cos(phi)));
  }

  for (let i = 1; i <= side2Seg; i += 1) {
    angles.push(theta2 + ((Math.PI / 2) - theta2) * (i / side2Seg));
  }

  return angles;
}

function mirrorQuadrantAngles(quadrantAngles) {
  const full = [...quadrantAngles];

  for (let i = quadrantAngles.length - 2; i >= 0; i -= 1) {
    full.push(Math.PI - quadrantAngles[i]);
  }

  for (let i = 1; i < quadrantAngles.length; i += 1) {
    full.push(Math.PI + quadrantAngles[i]);
  }

  for (let i = quadrantAngles.length - 2; i > 0; i -= 1) {
    full.push((Math.PI * 2) - quadrantAngles[i]);
  }

  return full;
}

export function buildAngleList(params, mouthExtents) {
  const angularSegments = Number(params.angularSegments || DEFAULTS.ANGULAR_SEGMENTS);
  if (!Number.isFinite(angularSegments) || angularSegments < 4) {
    return { fullAngles: [0], pointsPerQuadrant: 0 };
  }

  if (angularSegments % 4 !== 0) {
    return { fullAngles: buildUniformAngles(angularSegments), pointsPerQuadrant: 0 };
  }

  const pointsPerQuadrant = angularSegments / 4;
  const cornerR = Math.max(0, evalParam(params.morphCorner || 0, 0));
  const cornerSegments = Math.max(0, Math.round(params.cornerSegments || 4) - 1);

  const quadrantAngles = buildQuadrantAngles(
    pointsPerQuadrant,
    mouthExtents.halfW,
    mouthExtents.halfH,
    cornerR,
    cornerSegments
  );

  if (!quadrantAngles || quadrantAngles.length !== pointsPerQuadrant + 1) {
    return { fullAngles: buildUniformAngles(angularSegments), pointsPerQuadrant: 0 };
  }

  return {
    fullAngles: mirrorQuadrantAngles(quadrantAngles),
    pointsPerQuadrant
  };
}

export function selectAnglesForQuadrants(fullAngles, quadrants) {
  const q = String(quadrants ?? '1234').trim();
  if (q === '' || q === '1234') return fullAngles;

  const eps = 1e-9;
  const inRange = (a, min, max) => a >= min - eps && a <= max + eps;

  if (q === '14') {
    // Keep angular order continuous from -pi/2..pi/2.
    // This prevents seam-bridging triangles across the x=0 split plane.
    const negative = fullAngles
      .filter((a) => a >= Math.PI * 1.5 - eps)
      .map((a) => a - Math.PI * 2);
    const positive = fullAngles.filter((a) => inRange(a, 0, Math.PI / 2));
    const merged = [...negative, ...positive];
    return merged.filter((a, i) => i === 0 || Math.abs(a - merged[i - 1]) > eps);
  }

  if (q === '12') return fullAngles.filter((a) => inRange(a, 0, Math.PI));
  if (q === '1') return fullAngles.filter((a) => inRange(a, 0, Math.PI / 2));

  return fullAngles;
}
