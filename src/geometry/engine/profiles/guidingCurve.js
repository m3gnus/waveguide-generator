import { evalParam, toRad, parseNumberList } from '../../common.js';
import { GUIDING_CURVES } from '../constants.js';

function calculateSuperellipseRadius(cosP, sinP, width, aspect, exponent) {
  const a = width / 2;
  const b = a * aspect;
  const term = Math.pow(Math.abs(cosP / a), exponent) + Math.pow(Math.abs(sinP / b), exponent);
  if (term <= 0) return null;
  return Math.pow(term, -1 / exponent);
}

function paramOrDefault(value, fallback) {
  return value === undefined || value === null || value === '' ? fallback : value;
}

function evalParamOrDefault(value, p, fallback) {
  return evalParam(paramOrDefault(value, fallback), p);
}

function parseSuperformulaParams(params, p) {
  let a = evalParamOrDefault(params.gcurveSfA, p, 1);
  let b = evalParamOrDefault(params.gcurveSfB, p, 1);
  let m1 = evalParamOrDefault(params.gcurveSfM1, p, 4);
  let m2 =
    params.gcurveSfM2 === undefined || params.gcurveSfM2 === null || params.gcurveSfM2 === ''
      ? m1
      : evalParam(params.gcurveSfM2, p);
  let n1 = evalParamOrDefault(params.gcurveSfN1, p, 2);
  let n2 = evalParamOrDefault(params.gcurveSfN2, p, 2);
  let n3 = evalParamOrDefault(params.gcurveSfN3, p, 2);

  const list = parseNumberList(params.gcurveSf);
  if (list?.length >= 6) {
    [a, b, m1, n1, n2, n3] = list;
    m2 = m1;
  }

  return {
    a: Math.max(Math.abs(a), 1e-12),
    b: Math.max(Math.abs(b), 1e-12),
    m1,
    m2,
    n1: Math.max(Math.abs(n1), 1e-12),
    n2,
    n3,
  };
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
  const type = Number(evalParamOrDefault(params.gcurveType, p, GUIDING_CURVES.NONE));
  if (type === GUIDING_CURVES.NONE) return null;

  const width = evalParamOrDefault(params.gcurveWidth, p, 0);
  if (!Number.isFinite(width) || width <= 0) return null;

  const aspect = evalParamOrDefault(params.gcurveAspectRatio, p, 1);
  const rotation = toRad(evalParamOrDefault(params.gcurveRot, p, 0));
  const pr = p - rotation;
  const cosP = Math.cos(pr);
  const sinP = Math.sin(pr);

  if (type === GUIDING_CURVES.SUPERELLIPSE) {
    const n = Math.max(2, evalParamOrDefault(params.gcurveSeN, p, 3));
    return calculateSuperellipseRadius(cosP, sinP, width, aspect, n);
  }

  if (type === GUIDING_CURVES.SUPERFORMULA) {
    const sfParams = parseSuperformulaParams(params, p);
    return calculateSuperformulaRadius(pr, cosP, sinP, width, aspect, sfParams);
  }

  return null;
}
