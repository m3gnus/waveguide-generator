export function normalizeParamInput(rawValue) {
  const value = String(rawValue ?? '').trim();
  if (value === '') return value;

  const numeric = Number(value);
  if (Number.isFinite(numeric)) {
    return numeric;
  }

  return value;
}
