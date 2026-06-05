const SOLVE_DATE_PREFIX_RE = /^[0-9]{6}_/;

function normalizeSolveName(value, fallback = 'simulation') {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function normalizeSolveCounter(value) {
  const counter = Number(value);
  if (Number.isFinite(counter) && counter >= 1) {
    return Math.floor(counter);
  }
  return null;
}

export function formatSolveDatePrefix(timestamp = new Date()) {
  if (timestamp === null || timestamp === '') {
    return null;
  }

  const date = timestamp instanceof Date ? timestamp : new Date(timestamp);
  if (!Number.isFinite(date.getTime())) {
    return null;
  }

  const year = String(date.getFullYear()).slice(-2);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}${month}${day}`;
}

export function hasSolveDatePrefix(value) {
  return SOLVE_DATE_PREFIX_RE.test(String(value ?? '').trim());
}

export function ensureDatedSolveLabel(label, timestamp = new Date()) {
  const normalizedLabel = String(label ?? '').trim();
  if (!normalizedLabel || hasSolveDatePrefix(normalizedLabel)) {
    return normalizedLabel;
  }

  const datePrefix = formatSolveDatePrefix(timestamp);
  return datePrefix ? `${datePrefix}_${normalizedLabel}` : normalizedLabel;
}

export function resolveDatedSolveLabel({ outputName, counter, timestamp = new Date() } = {}) {
  const name = normalizeSolveName(outputName);
  const normalizedCounter = normalizeSolveCounter(counter);
  const baseLabel = normalizedCounter === null ? name : `${name}_${normalizedCounter}`;
  return ensureDatedSolveLabel(baseLabel, timestamp);
}
