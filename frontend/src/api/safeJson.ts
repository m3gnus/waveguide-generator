const FORBIDDEN_JSON_KEYS = new Set(['__proto__', 'prototype', 'constructor']);

/** Reject non-JSON values and keys that are hazardous when records are merged. */
export function isSafeJson(value: unknown, depth = 0): boolean {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (typeof value === 'number' && Number.isFinite(value)) return true;
  if (depth >= 40) return false;
  if (Array.isArray(value)) return value.every((item) => isSafeJson(item, depth + 1));
  if (value === null || typeof value !== 'object') return false;
  return Object.entries(value).every(([key, item]) => (
    !FORBIDDEN_JSON_KEYS.has(key) && isSafeJson(item, depth + 1)
  ));
}
