import { evalParam } from '../../common.js';

const VALIDATION_RULES = {
  a0: { min: 0, max: 90, message: 'a0 must be between 0 and 90 degrees' },
  r0: { min: 0, exclusive: true, message: 'r0 must be positive' },
  k: { min: 0, exclusive: true, message: 'k must be greater than 0' },
  tmax: { min: 0, max: 1, message: 'tmax must be between 0 and 1' }
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

export function validateParameters(params, _modelType) {
  const sampleP = 0;
  const errors = [];

  for (const [name, rule] of Object.entries(VALIDATION_RULES)) {
    const value = params[name] !== undefined ? evalParam(params[name], sampleP) : undefined;
    const error = validateRule(name, value, rule);
    if (error) errors.push(error);
  }

  return { valid: errors.length === 0, errors };
}
