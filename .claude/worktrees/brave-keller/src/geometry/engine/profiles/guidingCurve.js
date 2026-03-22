import { evalParam, toRad, parseNumberList } from '../../common.js';
import { GUIDING_CURVES } from '../constants.js';

function calculateSuperellipseRadius(cosP, sinP, width, aspect, exponent) {
  const a = width / 2;
  const b = a * aspect;
  const term = Math.pow(Math.abs(cosP / a), exponent) + Math.pow(Math.abs(sinP / b), exponent);
  if (term <= 0) return null;
  return Math.pow(term, -1 / exponent);
}

function parseSuperformulaParams(params, p) {
  let a = evalParam(params.gcurveSfA || 1, p);
  let b = evalParam(params.gcurveSfB || 1, p);
  let m1 = evalParam(params.gcurveSfM1 || 0, p);
  let m2 = evalParam(params.gcurveSfM2 || 0, p);
  let n1 = evalParam(params.gcurveSfN1 || 1, p);
  let n2 = evalParam(params.gcurveSfN2 || 1, p);
  let n3 = evalParam(params.gcurveSfN3 || 1, p);

  const list = parseNumberList(params.gcurveSf);
  if (list?.length >= 6) {
    [a, b, m1, n1, n2, n3] = list;
    m2 = m1;
  }

  return { a, b, m1, m2, n1, n2, n3 };
}

function calculateSuperformulaRadius(pr, cosP, sinP, width, aspect, sfParams) {
  const { a, b, m1, m2, n1, n2, n3 } = sfParams;
  const t1 = Math.pow(Math.abs(Math.cos((m1 * pr) / 4) / a), n2);
  const t2 = Math.pow(Math.abs(Math.sin((m2 * pr) / 4) / b), n3);
  const rNorm = Math.pow(t1 + t2, -1 / n1);
  if (!Number.isFinite(rNorm)) return null;

  const sx = width / 2;
  const sy = (width / 2) * aspect;
  const x = rNorm * cosP * sx;
  const y = rNorm * sinP * sy;

  return Math.hypot(x, y);
}

export function getGuidingCurveRadius(p, params) {
  const type = Number(params.gcurveType || GUIDING_CURVES.NONE);
  if (type === GUIDING_CURVES.NONE) return null;

  const width = evalParam(params.gcurveWidth || 0, p);
  if (!Number.isFinite(width) || width <= 0) return null;

  const aspect = evalParam(params.gcurveAspectRatio || 1, p);
  const rotation = toRad(evalParam(params.gcurveRot || 0, p));
  const pr = p - rotation;
  const cosP = Math.cos(pr);
  const sinP = Math.sin(pr);

  if (type === GUIDING_CURVES.SUPERELLIPSE) {
    const n = Math.max(2, evalParam(params.gcurveSeN || 3, p));
    return calculateSuperellipseRadius(cosP, sinP, width, aspect, n);
  }

  if (type === GUIDING_CURVES.SUPERFORMULA) {
    const sfParams = parseSuperformulaParams(params, p);
    return calculateSuperformulaRadius(pr, cosP, sinP, width, aspect, sfParams);
  }

  return null;
}
