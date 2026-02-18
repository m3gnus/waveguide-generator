const POLAR_AXIS_ORDER = ['horizontal', 'vertical', 'diagonal'];
const AXIS_CHECKBOX_IDS = {
  horizontal: 'polar-axis-horizontal',
  vertical: 'polar-axis-vertical',
  diagonal: 'polar-axis-diagonal'
};

const DIAGONAL_ANGLE_INPUT_ID = 'polar-inclination';
const EPSILON = 1e-6;

function getElement(doc, id) {
  return doc && typeof doc.getElementById === 'function' ? doc.getElementById(id) : null;
}

function toFiniteNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function readNumberInput(doc, id, fallback) {
  const element = getElement(doc, id);
  if (!element) return fallback;
  return toFiniteNumber(element.value, fallback);
}

function normalizeAngle360(angleDeg) {
  const normalized = ((angleDeg % 360) + 360) % 360;
  return Math.abs(normalized - 360) < EPSILON ? 0 : normalized;
}

function approxEqual(a, b) {
  return Math.abs(a - b) < EPSILON;
}

function toPolarEntries(blocks) {
  if (!blocks || typeof blocks !== 'object') return [];
  return Object.entries(blocks)
    .filter(([name]) => String(name).startsWith('ABEC.Polars:'))
    .sort((a, b) => String(a[0]).localeCompare(String(b[0]), undefined, { sensitivity: 'base' }));
}

function formatNumeric(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '0';
  if (Number.isInteger(numeric)) return String(numeric);
  return String(numeric);
}

function getEnabledAxesFromDom(doc) {
  const enabledAxes = [];
  POLAR_AXIS_ORDER.forEach((axis) => {
    const checkbox = getElement(doc, AXIS_CHECKBOX_IDS[axis]);
    if (checkbox && checkbox.checked) {
      enabledAxes.push(axis);
    }
  });
  return enabledAxes;
}

export function classifyInclinationAngle(angleDeg) {
  const finiteAngle = toFiniteNumber(angleDeg, 0);
  const normalized = normalizeAngle360(finiteAngle);
  const mod180 = normalized % 180;

  if (approxEqual(mod180, 0)) {
    return 'horizontal';
  }
  if (approxEqual(mod180, 90)) {
    return 'vertical';
  }
  return 'diagonal';
}

export function getPolarBlocksSignature(blocks) {
  const entries = toPolarEntries(blocks).map(([name, block]) => {
    const items = block && typeof block === 'object' && block._items && typeof block._items === 'object'
      ? Object.entries(block._items)
        .sort((a, b) => String(a[0]).localeCompare(String(b[0]), undefined, { sensitivity: 'base' }))
      : [];
    return [name, items];
  });

  return JSON.stringify(entries);
}

export function derivePolarSelectionFromBlocks(blocks) {
  const entries = toPolarEntries(blocks);
  if (entries.length === 0) {
    return {
      hasPolarBlocks: false,
      enabledAxes: [...POLAR_AXIS_ORDER],
      diagonalAngle: 45
    };
  }

  const selected = {
    horizontal: false,
    vertical: false,
    diagonal: false
  };
  let diagonalAngle = 45;

  entries.forEach(([, block]) => {
    const items = block && typeof block === 'object' ? (block._items || {}) : {};
    const inclination = toFiniteNumber(items.Inclination, 0);
    const axis = classifyInclinationAngle(inclination);
    selected[axis] = true;
    if (axis === 'diagonal') {
      diagonalAngle = inclination;
    }
  });

  const enabledAxes = POLAR_AXIS_ORDER.filter((axis) => selected[axis]);

  return {
    hasPolarBlocks: true,
    enabledAxes: enabledAxes.length > 0 ? enabledAxes : [...POLAR_AXIS_ORDER],
    diagonalAngle
  };
}

export function setDiagonalAngleEnabled(enabled, doc = document) {
  const diagonalAngleInput = getElement(doc, DIAGONAL_ANGLE_INPUT_ID);
  if (!diagonalAngleInput) return;
  diagonalAngleInput.disabled = !enabled;
}

export function applyPolarSelectionToDom(selection, doc = document) {
  if (!selection || typeof selection !== 'object') return;

  const enabled = new Set(Array.isArray(selection.enabledAxes) ? selection.enabledAxes : []);
  POLAR_AXIS_ORDER.forEach((axis) => {
    const checkbox = getElement(doc, AXIS_CHECKBOX_IDS[axis]);
    if (checkbox) {
      checkbox.checked = enabled.has(axis);
    }
  });

  const diagonalAngleInput = getElement(doc, DIAGONAL_ANGLE_INPUT_ID);
  if (diagonalAngleInput) {
    const diagonalAngle = toFiniteNumber(selection.diagonalAngle, 45);
    diagonalAngleInput.value = formatNumeric(diagonalAngle);
  }

  setDiagonalAngleEnabled(enabled.has('diagonal'), doc);
}

export function syncPolarControlsFromBlocks(blocks, doc = document) {
  const selection = derivePolarSelectionFromBlocks(blocks);
  applyPolarSelectionToDom(selection, doc);
  return selection;
}

export function bindPolarUiToggleHandlers(doc = document) {
  const diagonalCheckbox = getElement(doc, AXIS_CHECKBOX_IDS.diagonal);
  if (diagonalCheckbox) {
    diagonalCheckbox.addEventListener('change', () => {
      setDiagonalAngleEnabled(diagonalCheckbox.checked, doc);
    });
    setDiagonalAngleEnabled(diagonalCheckbox.checked, doc);
  }
}

export function readPolarUiSettings(doc = document) {
  const angleStart = readNumberInput(doc, 'polar-angle-start', 0);
  const angleEnd = readNumberInput(doc, 'polar-angle-end', 180);
  const angleStepRaw = readNumberInput(doc, 'polar-angle-step', 5);
  const angleStep = angleStepRaw > 0 ? angleStepRaw : 5;
  const angleCount = Math.max(2, Math.floor((angleEnd - angleStart) / angleStep) + 1);
  const distance = readNumberInput(doc, 'polar-distance', 2);
  const normAngle = readNumberInput(doc, 'polar-norm-angle', 5);
  const diagonalAngle = readNumberInput(doc, DIAGONAL_ANGLE_INPUT_ID, 45);
  const enabledAxes = getEnabledAxesFromDom(doc);

  if (enabledAxes.length === 0) {
    return {
      ok: false,
      validationError: 'Select at least one polar axis (horizontal, vertical, or diagonal).'
    };
  }

  return {
    ok: true,
    angleRangeArray: [angleStart, angleEnd, angleCount],
    polarRange: `${angleStart},${angleEnd},${angleCount}`,
    distance,
    normAngle,
    diagonalAngle,
    enabledAxes
  };
}

export function buildCanonicalPolarBlocks(settings) {
  if (!settings || typeof settings !== 'object') return {};

  const enabledAxes = Array.isArray(settings.enabledAxes) ? settings.enabledAxes : [];
  const enabled = new Set(enabledAxes);
  const angleRange = settings.polarRange || '0,180,37';
  const distance = formatNumeric(settings.distance ?? 2);
  const normAngle = formatNumeric(settings.normAngle ?? 5);
  const diagonalAngle = formatNumeric(settings.diagonalAngle ?? 45);

  const makeItems = (inclination = null) => {
    const items = {
      MapAngleRange: angleRange,
      NormAngle: normAngle,
      Distance: distance
    };
    if (inclination !== null) {
      items.Inclination = formatNumeric(inclination);
    }
    return { _items: items, _lines: [] };
  };

  const blocks = {};
  if (enabled.has('horizontal')) {
    blocks['ABEC.Polars:SPL_H'] = makeItems(null);
  }
  if (enabled.has('vertical')) {
    blocks['ABEC.Polars:SPL_V'] = makeItems(90);
  }
  if (enabled.has('diagonal')) {
    blocks['ABEC.Polars:SPL_D'] = makeItems(diagonalAngle);
  }
  return blocks;
}
