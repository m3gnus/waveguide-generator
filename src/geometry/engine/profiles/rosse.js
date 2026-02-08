import { evalParam, toRad } from '../../common.js';
import { DEFAULTS } from '../constants.js';
import { safeDiv } from '../math.js';
import { validateParameters } from './validation.js';

function calculateRossConstants(p, params) {
  const a = toRad(evalParam(params.a, p));
  const a0 = toRad(evalParam(params.a0, p));
  const k = evalParam(params.k, p);
  const r0 = evalParam(params.r0, p);

  return {
    c1: (k * r0) ** 2,
    c2: 2 * k * r0 * Math.tan(a0),
    c3: Math.tan(a) ** 2
  };
}

function calculateRossLength(constants, R, r0, k) {
  const { c1, c2, c3 } = constants;
  const termInside = c2 ** 2 - 4 * c3 * (c1 - (R + r0 * (k - 1)) ** 2);
  return safeDiv(Math.sqrt(Math.max(0, termInside)) - c2, 2 * c3, 0);
}

export function calculateROSSE(t, p, params) {
  const validation = validateParameters(params, 'ROSSE');
  if (!validation.valid) {
    console.error('Validation failed:', validation.errors);
    return { x: NaN, y: NaN };
  }

  const R = evalParam(params.R, p);
  const r0 = evalParam(params.r0, p);
  const k = evalParam(params.k, p);
  const q = evalParam(params.q, p);
  const m = params.m === undefined ? DEFAULTS.M : evalParam(params.m, p);
  const r = params.r === undefined ? DEFAULTS.R : evalParam(params.r, p);
  const b = params.b === undefined ? DEFAULTS.B : evalParam(params.b, p);

  const constants = calculateRossConstants(p, params);
  const L = calculateRossLength(constants, R, r0, k);
  const { c1, c2, c3 } = constants;

  const x = L * (Math.sqrt(r ** 2 + m ** 2) - Math.sqrt(r ** 2 + (t - m) ** 2))
    + b * L * (Math.sqrt(r ** 2 + (1 - m) ** 2) - Math.sqrt(r ** 2 + m ** 2)) * (t ** 2);

  const tPowQ = t ** q;
  const throatR = Math.sqrt(c1 + c2 * L * t + c3 * (L * t) ** 2) + r0 * (1 - k);
  const mouthR = R + L * (1 - Math.sqrt(1 + c3 * (t - 1) ** 2));

  const y = (1 - tPowQ) * throatR + tPowQ * mouthR;

  return { x, y };
}
