const SOLVE_DATE_PREFIX_RE = /^[0-9]{6}_/;
const TRAILING_COUNTER_RE = /_(\d+)$/;

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

function stripSolveDatePrefix(value) {
  return String(value ?? '')
    .trim()
    .replace(SOLVE_DATE_PREFIX_RE, '');
}

function collectExistingSolveCounters(outputName, existingJobs = []) {
  const name = normalizeSolveName(outputName);
  const counters = new Set();

  for (const job of existingJobs || []) {
    const script = job?.script || job?.scriptSnapshot || job?.script_snapshot || null;
    if (script && normalizeSolveName(script.outputName) === name) {
      const scriptCounter = normalizeSolveCounter(script.counter);
      if (scriptCounter !== null) {
        counters.add(scriptCounter);
      }
    }

    const labelBody = stripSolveDatePrefix(job?.label);
    if (!labelBody.startsWith(`${name}_`)) {
      continue;
    }
    const match = labelBody.match(TRAILING_COUNTER_RE);
    if (!match) {
      continue;
    }
    const labelName = labelBody.slice(0, -match[0].length);
    if (labelName === name) {
      counters.add(Number(match[1]));
    }
  }

  return counters;
}

export function resolveAvailableSolveCounter({ outputName, counter, existingJobs = [] } = {}) {
  let nextCounter = normalizeSolveCounter(counter) ?? 1;
  const existingCounters = collectExistingSolveCounters(outputName, existingJobs);
  while (existingCounters.has(nextCounter)) {
    nextCounter += 1;
  }
  return nextCounter;
}
