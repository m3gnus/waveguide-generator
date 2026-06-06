import { MORPH_TARGETS } from './constants.js';
import { evalParam } from '../common.js';

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
    return Math.max(halfWidth, halfHeight);
  }
  if (targetShape !== MORPH_TARGETS.RECTANGLE) {
    throw new Error(`Unsupported morph target ${targetShape}`);
  }
  return getRoundedRectRadius(p, halfWidth, halfHeight, cornerRadius);
}

function evalNumber(value, p, fallback = 0) {
  const n = Number(evalParam(value, p));
  return Number.isFinite(n) ? n : fallback;
}

export function isMorphActive(params, p = 0) {
  const targetShape = Math.round(evalNumber(params.morphTarget, p, MORPH_TARGETS.NONE));
  return targetShape === MORPH_TARGETS.RECTANGLE || targetShape === MORPH_TARGETS.CIRCLE;
}

export function hasConfiguredMorphDimension(params, key, p = 0) {
  return evalNumber(params[key], p, 0) > 0;
}

export function applyMorphing(currentR, mouthR, t, p, params, morphTargetInfo = null) {
  const targetShape = Math.round(evalNumber(params.morphTarget, p, MORPH_TARGETS.NONE));
  if (targetShape === MORPH_TARGETS.NONE) return currentR;

  const morphStart = evalNumber(params.morphFixed, p, 0);
  if (t <= morphStart) return currentR;

  const rate = evalNumber(params.morphRate, p, 3);
  const morphFactor =
    Math.min(1, Math.max(0, (t - morphStart) / Math.max(1e-9, 1 - morphStart))) ** rate;

  const morphWidth = evalNumber(params.morphWidth, p, 0);
  const morphHeight = evalNumber(params.morphHeight, p, 0);
  const hasExplicit = morphWidth > 0 || morphHeight > 0;
  const halfWidth = morphWidth > 0 ? morphWidth / 2 : (morphTargetInfo?.halfW ?? mouthR);
  const halfHeight = morphHeight > 0 ? morphHeight / 2 : (morphTargetInfo?.halfH ?? mouthR);

  if (!hasExplicit && !morphTargetInfo) return currentR;

  const targetR = getMorphTargetRadius(
    p,
    targetShape,
    halfWidth,
    halfHeight,
    evalNumber(params.morphCorner, p, 0)
  );
  const allowShrinkage = params.morphAllowShrinkage === 1 || params.morphAllowShrinkage === true;
  const safeTarget = allowShrinkage ? targetR : Math.max(mouthR, targetR);

  return currentR + (safeTarget - mouthR) * morphFactor;
}
