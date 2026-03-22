import { MORPH_TARGETS } from './constants.js';
import { lerp } from './math.js';

export function getRoundedRectRadius(p, halfWidth, halfHeight, cornerRadius) {
  const absCos = Math.abs(Math.cos(p));
  const absSin = Math.abs(Math.sin(p));

  if (absCos < 1e-9) return halfHeight;
  if (absSin < 1e-9) return halfWidth;

  const r = Math.max(0, Math.min(cornerRadius, Math.min(halfWidth, halfHeight)));
  if (r <= 1e-9) {
    return Math.min(halfWidth / absCos, halfHeight / absSin);
  }

  const yAtX = (halfWidth * absSin) / absCos;
  if (yAtX <= halfHeight - r + 1e-9) return halfWidth / absCos;

  const xAtY = (halfHeight * absCos) / absSin;
  if (xAtY <= halfWidth - r + 1e-9) return halfHeight / absSin;

  const cx = halfWidth - r;
  const cy = halfHeight - r;
  const A = absCos ** 2 + absSin ** 2;
  const B = -2 * (absCos * cx + absSin * cy);
  const C = cx ** 2 + cy ** 2 - r ** 2;
  const disc = Math.max(0, B ** 2 - 4 * A * C);

  return (-B + Math.sqrt(disc)) / (2 * A);
}

function getMorphTargetRadius(p, targetShape, halfWidth, halfHeight, cornerRadius) {
  if (targetShape === MORPH_TARGETS.CIRCLE) {
    return Math.sqrt(Math.max(0, halfWidth * halfHeight));
  }
  return getRoundedRectRadius(p, halfWidth, halfHeight, cornerRadius);
}

export function applyMorphing(currentR, t, p, params, morphTargetInfo = null) {
  const targetShape = Number(params.morphTarget || MORPH_TARGETS.NONE);
  if (targetShape === MORPH_TARGETS.NONE) return currentR;

  const morphStart = Number(params.morphFixed || 0);
  if (t <= morphStart) return currentR;

  const rate = Number(params.morphRate || 3);
  const morphFactor = Math.pow((t - morphStart) / Math.max(1e-9, 1 - morphStart), rate);

  const hasExplicit = (params.morphWidth > 0) || (params.morphHeight > 0);
  const halfWidth = params.morphWidth > 0 ? params.morphWidth / 2 : (morphTargetInfo?.halfW ?? currentR);
  const halfHeight = params.morphHeight > 0 ? params.morphHeight / 2 : (morphTargetInfo?.halfH ?? currentR);

  if (!hasExplicit && !morphTargetInfo) return currentR;

  const targetR = getMorphTargetRadius(p, targetShape, halfWidth, halfHeight, params.morphCorner || 0);
  const allowShrinkage = params.morphAllowShrinkage === 1 || params.morphAllowShrinkage === true;
  const safeTarget = allowShrinkage ? targetR : Math.max(currentR, targetR);

  return lerp(currentR, safeTarget, morphFactor);
}
