import { evalParam } from '../../common.js';
import { DEFAULTS, MORPH_TARGETS } from '../constants.js';

function buildUniformAngles(segmentCount) {
  return Array.from({ length: segmentCount }, (_, i) => (i / segmentCount) * Math.PI * 2);
}

function normalizeAngularSegments(rawCount) {
  const count = Math.max(4, Math.round(Number(rawCount) || 0));
  if (count % 4 === 0) return count;
  // ATH-compatible fallback: snap up to a full 8-way symmetric ring.
  return Math.max(8, Math.ceil(count / 8) * 8);
}

/**
 * First-quadrant azimuth samples for a rounded-rectangle morph target.
 *
 * Ported one-to-one from the canonical mesher
 * (hornlab_mesher.profile_sampling._rounded_rect_quadrant_angles): the corner
 * arc always carries three segments at fixed 30/60/90-degree arc steps and the
 * two wall spans take the remaining (>=2) segments, split proportionally to
 * their angular extents. Mesh.CornerSegments only grows the point budget; it
 * does not change the arc structure.
 */
function buildQuadrantAngles(pointsPerQuadrant, halfW, halfH, cornerR) {
  if (!Number.isFinite(halfW) || !Number.isFinite(halfH) || halfW <= 0 || halfH <= 0) {
    return null;
  }

  const ppq = Math.max(1, Math.round(pointsPerQuadrant));
  const clampedCorner = Math.min(Math.max(cornerR, 0), halfW, halfH);

  if (clampedCorner <= 1e-9) {
    return Array.from({ length: ppq + 1 }, (_, i) => (Math.PI / 2) * (i / ppq));
  }

  const theta1 = Math.atan2(halfH - clampedCorner, halfW);
  const theta2 = Math.atan2(halfH, halfW - clampedCorner);
  const arcSegments = 3;
  const sideSegments = Math.max(2, ppq - arcSegments);
  const span1 = theta1;
  const span2 = Math.PI / 2 - theta2;
  const side1Seg = Math.max(1, Math.round((sideSegments * span1) / Math.max(span1 + span2, 1e-12)));
  const side2Seg = Math.max(1, sideSegments - side1Seg);

  const angles = [];
  for (let i = 0; i <= side1Seg; i += 1) {
    angles.push((theta1 * i) / side1Seg);
  }
  const cx = halfW - clampedCorner;
  const cy = halfH - clampedCorner;
  for (let i = 1; i <= arcSegments; i += 1) {
    const cornerPhi = (i / arcSegments) * (Math.PI / 2);
    angles.push(
      Math.atan2(cy + clampedCorner * Math.sin(cornerPhi), cx + clampedCorner * Math.cos(cornerPhi))
    );
  }
  for (let i = 1; i <= side2Seg; i += 1) {
    angles.push(theta2 + ((Math.PI / 2 - theta2) * i) / side2Seg);
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
    full.push(Math.PI * 2 - quadrantAngles[i]);
  }

  return full;
}

export function buildAngleList(params, mouthExtents) {
  const angularSegments = normalizeAngularSegments(
    Number(params.angularSegments || DEFAULTS.ANGULAR_SEGMENTS)
  );
  if (!Number.isFinite(angularSegments) || angularSegments < 4) {
    return { fullAngles: [0], pointsPerQuadrant: 0 };
  }

  // Only an explicit rounded-rectangle morph (target 1) gets the corner-aware
  // azimuth list, matching the canonical mesher. Circle/none targets keep the
  // uniform ring. The corner-segment budget folds Mesh.CornerSegments into the
  // angular point count and rounds up to a whole number of points per quadrant
  // (ATH: 64+4 -> 17, 16+0 -> 4), then the rounded-rect builder layers its
  // fixed three-segment corner arc on top.
  const morphTarget = Math.round(evalParam(params.morphTarget || 0, 0));
  if (morphTarget === MORPH_TARGETS.RECTANGLE && mouthExtents.halfW > 0 && mouthExtents.halfH > 0) {
    const cornerR = Math.max(0, evalParam(params.morphCorner || 0, 0));
    const cornerSegments = Math.max(0, Math.round(evalParam(params.cornerSegments || 0, 0)));
    const pointsPerQuadrant = Math.max(1, Math.ceil((angularSegments + cornerSegments) / 4));

    const quadrantAngles = buildQuadrantAngles(
      pointsPerQuadrant,
      mouthExtents.halfW,
      mouthExtents.halfH,
      cornerR
    );

    if (quadrantAngles && quadrantAngles.length >= 2) {
      return {
        fullAngles: mirrorQuadrantAngles(quadrantAngles),
        pointsPerQuadrant: quadrantAngles.length - 1,
      };
    }
  }

  return { fullAngles: buildUniformAngles(angularSegments), pointsPerQuadrant: angularSegments / 4 };
}

export function selectAnglesForQuadrants(fullAngles, quadrants) {
  const q = String(quadrants ?? '1234').trim();
  if (q === '' || q === '1234') return fullAngles;

  const eps = 1e-9;
  const inRange = (a, min, max) => a >= min - eps && a <= max + eps;

  if (q === '14') {
    // Keep angular order continuous from -pi/2..pi/2.
    // This prevents seam-bridging triangles across the x=0 split plane.
    const negative = fullAngles.filter((a) => a >= Math.PI * 1.5 - eps).map((a) => a - Math.PI * 2);
    const positive = fullAngles.filter((a) => inRange(a, 0, Math.PI / 2));
    const merged = [...negative, ...positive];
    return merged.filter((a, i) => i === 0 || Math.abs(a - merged[i - 1]) > eps);
  }

  if (q === '12') return fullAngles.filter((a) => inRange(a, 0, Math.PI));
  if (q === '1') return fullAngles.filter((a) => inRange(a, 0, Math.PI / 2));

  return fullAngles;
}
