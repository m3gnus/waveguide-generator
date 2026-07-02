import { evalParam, toRad } from '../../common.js';

const VALIDATION_RULES = {
  a0: { min: 0, max: 90, message: 'a0 must be between 0 and 90 degrees' },
  r0: { min: 0, exclusive: true, message: 'r0 must be positive' },
  k: { min: 0, exclusive: true, message: 'k must be greater than 0' },
  tmax: { min: 0, max: 1, message: 'tmax must be between 0 and 1' },
};

function validateRule(name, value, rule) {
  if (value === undefined) return null;
  if (!Number.isFinite(value)) {
    return `${name} must be a finite number`;
  }
  if (rule.min !== undefined && value < rule.min) return rule.message;
  if (rule.max !== undefined && value > rule.max) return rule.message;
  if (rule.exclusive && value <= 0) return rule.message;
  return null;
}

function validateRosseReachability(params, p) {
  // Mirrors hornlab_mesher.profile_formulas._rosse_length: a negative
  // discriminant means the requested R cannot be reached from r0, which the
  // canonical mesher rejects with this same message. The engine returns NaN
  // for it; this check gives the user the reason.
  if (params.R === undefined || params.r0 === undefined || params.a === undefined) {
    return null;
  }
  const R = evalParam(params.R, p);
  const r0 = evalParam(params.r0, p);
  const k = params.k === undefined ? 1 : evalParam(params.k, p);
  const a = toRad(evalParam(params.a, p));
  const a0 = params.a0 === undefined ? 0 : toRad(evalParam(params.a0, p));
  if (![R, r0, k, a, a0].every(Number.isFinite)) return null;
  const c3 = Math.tan(a) ** 2;
  if (Math.abs(c3) < 1e-12) return null;
  const c1 = (k * r0) ** 2;
  const c2 = 2 * k * r0 * Math.tan(a0);
  const target = R + r0 * (k - 1);
  const discriminant = c2 ** 2 - 4 * c3 * (c1 - target ** 2);
  if (discriminant < 0) {
    return 'R is unreachable from r0 with these R-OSSE parameters';
  }
  return null;
}

export function validateParameters(params, modelType) {
  const sampleP = 0;
  const errors = [];

  for (const [name, rule] of Object.entries(VALIDATION_RULES)) {
    const value = params[name] !== undefined ? evalParam(params[name], sampleP) : undefined;
    const error = validateRule(name, value, rule);
    if (error) errors.push(error);
  }

  if (modelType === 'ROSSE') {
    const error = validateRosseReachability(params, sampleP);
    if (error) errors.push(error);
  }

  return { valid: errors.length === 0, errors };
}
