import { evalParam, toRad } from '../../common.js';
import { debugError } from '../../../logging/debug.js';
import { DEFAULTS } from '../constants.js';
import { validateParameters } from './validation.js';

function paramOrDefault(value, fallback) {
  return value === undefined || value === null || value === '' ? fallback : value;
}

function calculateRossConstants(p, params) {
  const a = toRad(evalParam(params.a, p));
  const a0 = toRad(evalParam(params.a0, p));
  const k = evalParam(params.k, p);
  const r0 = evalParam(params.r0, p);

  return {
    c1: (k * r0) ** 2,
    c2: 2 * k * r0 * Math.tan(a0),
    c3: Math.tan(a) ** 2,
  };
}

function calculateRossLength(constants, R, r0, k) {
  const { c1, c2, c3 } = constants;
  const target = R + r0 * (k - 1);
  const discriminant = c2 ** 2 - 4 * c3 * (c1 - target ** 2);

  if (Math.abs(c3) < 1e-12) {
    if (Math.abs(c2) < 1e-12) return 0;
    return (target ** 2 - c1) / c2;
  }

  // The canonical mesher rejects these parameters ("R is unreachable from
  // r0"); clamping to 0 here rendered a plausible horn the solve/export
  // would then refuse. NaN keeps the two engines in agreement (validation
  // surfaces the message).
  if (discriminant < 0) return NaN;

  return (Math.sqrt(discriminant) - c2) / (2 * c3);
}

function calculateROSSEMain(t, p, params) {
  const validation = validateParameters(params, 'ROSSE');
  if (!validation.valid) {
    debugError('Validation failed:', validation.errors);
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

  const x =
    L * (Math.sqrt(r ** 2 + m ** 2) - Math.sqrt(r ** 2 + (t - m) ** 2)) +
    b * L * (Math.sqrt(r ** 2 + (1 - m) ** 2) - Math.sqrt(r ** 2 + m ** 2)) * t ** 2;

  const tPowQ = t ** q;
  const throatR = Math.sqrt(c1 + c2 * L * t + c3 * (L * t) ** 2) + r0 * (1 - k);
  // max(0, ...) matches the canonical mesher's clamp for pathological params.
  const mouthR = Math.max(0, R + L * (1 - Math.sqrt(1 + c3 * (t - 1) ** 2)));

  const y = (1 - tPowQ) * throatR + tPowQ * mouthR;

  return { x, y };
}

export function calculateROSSE(t, p, params) {
  const extLen = Math.max(0, evalParam(paramOrDefault(params.throatExtLength, 0), p));
  const slotLen = Math.max(0, evalParam(paramOrDefault(params.slotLength, 0), p));
  const r0Base = evalParam(params.r0, p);
  const extAngleRad = toRad(evalParam(paramOrDefault(params.throatExtAngle, 0), p));
  // ATH anchors r0 at the MAIN throat and tapers the extension back to the
  // driver end, matching hornlab_mesher.profile_formulas.calculate_rosse.
  const r0Main = r0Base;
  const r0Throat = Math.max(0, r0Base - extLen * Math.tan(extAngleRad));
  const mainParams = { ...params, r0: r0Main };

  if (extLen <= 0 && slotLen <= 0) {
    return calculateROSSEMain(t, p, mainParams);
  }

  const mainLength = calculateRossLength(
    calculateRossConstants(p, mainParams),
    evalParam(mainParams.R, p),
    r0Main,
    evalParam(mainParams.k, p)
  );
  if (!Number.isFinite(mainLength)) {
    return { x: NaN, y: NaN };
  }
  const fullLength = extLen + slotLen + mainLength;
  if (fullLength <= 1e-12) {
    return { x: 0, y: r0Base };
  }

  const axial = Math.max(0, t) * fullLength;
  if (axial <= extLen) {
    return { x: axial, y: r0Throat + axial * Math.tan(extAngleRad) };
  }
  if (axial <= extLen + slotLen) {
    return { x: axial, y: r0Main };
  }
  if (!Number.isFinite(mainLength) || mainLength <= 1e-12) {
    return { x: extLen + slotLen, y: r0Main };
  }

  const mainT = (axial - extLen - slotLen) / mainLength;
  const main = calculateROSSEMain(mainT, p, mainParams);
  return { x: main.x + extLen + slotLen, y: main.y };
}
