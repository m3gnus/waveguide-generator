import { appendSectionNote, createHelpTrigger, createLabelRow } from '../helpAffordance.js';
import { getParameterSection } from '../parameterInventory.js';

const POLAR_AXIS_ORDER = ['horizontal', 'vertical', 'diagonal'];
const AXIS_CHECKBOX_IDS = {
  horizontal: 'polar-axis-horizontal',
  vertical: 'polar-axis-vertical',
  diagonal: 'polar-axis-diagonal'
};
const POLAR_NUMERIC_FIELDS = Object.freeze([
  {
    id: 'polar-angle-start',
    uiKey: 'angleStart',
    stateKey: 'polarAngleStart',
    fallback: 0,
    label: 'Sweep Start',
    unit: 'deg',
    help: 'Starting angle for the directivity map sweep.',
    min: 0,
    max: 360,
    step: 1
  },
  {
    id: 'polar-angle-end',
    uiKey: 'angleEnd',
    stateKey: 'polarAngleEnd',
    fallback: 180,
    label: 'Sweep End',
    unit: 'deg',
    help: 'Ending angle for the directivity map sweep.',
    min: 0,
    max: 360,
    step: 1
  },
  {
    id: 'polar-angle-step',
    uiKey: 'angleStep',
    stateKey: 'polarAngleStep',
    fallback: 5,
    label: 'Angular Step',
    unit: 'deg',
    help: 'Angular increment between directivity samples. Smaller steps produce more angular samples.',
    min: 1,
    max: 90,
    step: 1
  },
  {
    id: 'polar-distance',
    uiKey: 'distance',
    stateKey: 'polarDistance',
    fallback: 2,
    label: 'Measurement Distance',
    unit: 'm',
    help: 'Evaluation distance used when generating the directivity map.',
    min: 0.1,
    max: 10,
    step: 0.1
  },
  {
    id: 'polar-norm-angle',
    uiKey: 'normAngle',
    stateKey: 'polarNormAngle',
    fallback: 5,
    label: 'Normalization Angle',
    unit: 'deg',
    help: 'Reference angle used to normalize the directivity map output.',
    min: 0,
    max: 90,
    step: 1
  },
  {
    id: 'polar-inclination',
    uiKey: 'diagonalAngle',
    stateKey: 'polarDiagonalAngle',
    fallback: 45,
    label: 'Diagonal Plane Angle',
    unit: 'deg',
    help: 'Inclination used for the diagonal directivity plane when the diagonal axis is enabled.',
    min: 0,
    max: 360,
    step: 1
  }
]);
const DIAGONAL_ANGLE_INPUT_ID = 'polar-inclination';
const POLAR_SETTINGS_CONTAINER_ID = 'polar-settings-container';
const EPSILON = 1e-6;
const DEFAULT_POLAR_UI_STATE = Object.freeze({
  angleStart: 0,
  angleEnd: 180,
  angleStep: 5,
  distance: 2,
  normAngle: 5,
  diagonalAngle: 45,
  enabledAxes: [...POLAR_AXIS_ORDER]
});
const POLAR_SECTION_METADATA = Object.freeze(
  getParameterSection('simulation', 'directivity-map') || {
    title: 'Directivity Map',
    description: 'Polar planes and angular sampling used for directivity exports and plots.'
  }
);
const POLAR_AXIS_METADATA = Object.freeze([
  {
    axis: 'horizontal',
    id: AXIS_CHECKBOX_IDS.horizontal,
    label: 'Horizontal (0 deg)',
    help: 'Generate the horizontal directivity plane.'
  },
  {
    axis: 'vertical',
    id: AXIS_CHECKBOX_IDS.vertical,
    label: 'Vertical (90 deg)',
    help: 'Generate the vertical directivity plane.'
  },
  {
    axis: 'diagonal',
    id: AXIS_CHECKBOX_IDS.diagonal,
    label: 'Diagonal',
    help: 'Generate an additional inclined directivity plane using the diagonal angle below.'
  }
]);

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

function cloneDefaultPolarUiState() {
  return {
    ...DEFAULT_POLAR_UI_STATE,
    enabledAxes: [...DEFAULT_POLAR_UI_STATE.enabledAxes]
  };
}

function isPolarAxisControlId(id) {
  return Object.values(AXIS_CHECKBOX_IDS).includes(id);
}

function getNumericFieldById(id) {
  return POLAR_NUMERIC_FIELDS.find((field) => field.id === id) || null;
}

function normalizeEnabledAxes(enabledAxes) {
  if (!Array.isArray(enabledAxes)) {
    return [...DEFAULT_POLAR_UI_STATE.enabledAxes];
  }
  const normalized = POLAR_AXIS_ORDER.filter((axis) => enabledAxes.includes(axis));
  return normalized;
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

function parsePolarRange(value) {
  if (typeof value !== 'string') return null;
  const parts = value.split(',').map((part) => Number(part.trim()));
  if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) {
    return null;
  }
  const [angleStart, angleEnd, rawAngleCount] = parts;
  const angleCount = Math.max(2, Math.floor(rawAngleCount));
  const angleStep = angleCount > 1 ? (angleEnd - angleStart) / (angleCount - 1) : DEFAULT_POLAR_UI_STATE.angleStep;
  return { angleStart, angleEnd, angleStep };
}

function derivePolarUiStateFromBlocks(blocks) {
  const resolved = cloneDefaultPolarUiState();
  const entries = toPolarEntries(blocks);
  if (entries.length === 0) {
    return resolved;
  }

  const firstBlock = entries[0]?.[1];
  const firstItems = firstBlock && typeof firstBlock === 'object' ? (firstBlock._items || {}) : {};
  const parsedRange = parsePolarRange(firstItems.MapAngleRange);
  if (parsedRange) {
    resolved.angleStart = parsedRange.angleStart;
    resolved.angleEnd = parsedRange.angleEnd;
    resolved.angleStep = parsedRange.angleStep;
  }
  resolved.distance = toFiniteNumber(firstItems.Distance, resolved.distance);
  resolved.normAngle = toFiniteNumber(firstItems.NormAngle, resolved.normAngle);

  const selection = derivePolarSelectionFromBlocks(blocks);
  resolved.enabledAxes = [...selection.enabledAxes];
  resolved.diagonalAngle = selection.diagonalAngle;
  return resolved;
}

function derivePolarUiStateFromConfig(polarConfig) {
  const resolved = cloneDefaultPolarUiState();
  if (!polarConfig || typeof polarConfig !== 'object') {
    return resolved;
  }

  const [angleStart, angleEnd, rawAngleCount] = Array.isArray(polarConfig.angle_range)
    ? polarConfig.angle_range
    : [];
  if (Number.isFinite(Number(angleStart))) {
    resolved.angleStart = Number(angleStart);
  }
  if (Number.isFinite(Number(angleEnd))) {
    resolved.angleEnd = Number(angleEnd);
  }
  if (Number.isFinite(Number(rawAngleCount))) {
    const angleCount = Math.max(2, Math.floor(Number(rawAngleCount)));
    resolved.angleStep = angleCount > 1
      ? (resolved.angleEnd - resolved.angleStart) / (angleCount - 1)
      : resolved.angleStep;
  }
  resolved.distance = toFiniteNumber(polarConfig.distance, resolved.distance);
  resolved.normAngle = toFiniteNumber(polarConfig.norm_angle, resolved.normAngle);
  resolved.diagonalAngle = toFiniteNumber(polarConfig.inclination, resolved.diagonalAngle);
  resolved.enabledAxes = normalizeEnabledAxes(polarConfig.enabled_axes);
  return resolved;
}

function applyExplicitPolarStateOverrides(resolved, params) {
  if (!params || typeof params !== 'object') {
    return resolved;
  }

  POLAR_NUMERIC_FIELDS.forEach(({ uiKey, stateKey }) => {
    if (params[stateKey] !== undefined && params[stateKey] !== null) {
      resolved[uiKey] = toFiniteNumber(params[stateKey], resolved[uiKey]);
    }
  });

  if (Array.isArray(params.polarEnabledAxes)) {
    resolved.enabledAxes = normalizeEnabledAxes(params.polarEnabledAxes);
  }

  return resolved;
}

function computeAngleRange(uiState) {
  const angleStart = toFiniteNumber(uiState.angleStart, DEFAULT_POLAR_UI_STATE.angleStart);
  const angleEnd = toFiniteNumber(uiState.angleEnd, DEFAULT_POLAR_UI_STATE.angleEnd);
  const angleStepRaw = toFiniteNumber(uiState.angleStep, DEFAULT_POLAR_UI_STATE.angleStep);
  const angleStep = angleStepRaw > 0 ? angleStepRaw : DEFAULT_POLAR_UI_STATE.angleStep;
  const angleCount = Math.max(2, Math.floor((angleEnd - angleStart) / angleStep) + 1);
  return {
    angleStart,
    angleEnd,
    angleStep,
    angleCount
  };
}

function buildCanonicalPolarBlockMap(uiState) {
  const { angleStart, angleEnd, angleCount } = computeAngleRange(uiState);
  return buildCanonicalPolarBlocks({
    enabledAxes: uiState.enabledAxes,
    polarRange: `${angleStart},${angleEnd},${angleCount}`,
    distance: uiState.distance,
    normAngle: uiState.normAngle,
    diagonalAngle: uiState.diagonalAngle
  });
}

function mergePolarBlocks(existingBlocks, nextPolarBlocks) {
  const merged = {};
  if (existingBlocks && typeof existingBlocks === 'object') {
    Object.entries(existingBlocks).forEach(([name, block]) => {
      if (!String(name).startsWith('ABEC.Polars:')) {
        merged[name] = block;
      }
    });
  }
  return {
    ...merged,
    ...nextPolarBlocks
  };
}

function buildPersistedPolarStatePatch(currentParams, nextUiState) {
  const patch = {
    polarAngleStart: nextUiState.angleStart,
    polarAngleEnd: nextUiState.angleEnd,
    polarAngleStep: nextUiState.angleStep,
    polarDistance: nextUiState.distance,
    polarNormAngle: nextUiState.normAngle,
    polarDiagonalAngle: nextUiState.diagonalAngle,
    polarEnabledAxes: [...nextUiState.enabledAxes]
  };
  patch._blocks = mergePolarBlocks(currentParams?._blocks, buildCanonicalPolarBlockMap(nextUiState));
  return patch;
}

export function resolvePolarUiState(params = {}) {
  const resolved = cloneDefaultPolarUiState();
  const withBlocks = params && typeof params === 'object' && params._blocks
    ? derivePolarUiStateFromBlocks(params._blocks)
    : resolved;
  return applyExplicitPolarStateOverrides(withBlocks, params);
}

function appendPolarNumberRow(section, field, doc) {
  const row = doc.createElement('div');
  row.className = 'input-row';

  const { row: labelRow } = createLabelRow(doc, {
    labelText: field.unit ? `${field.label} (${field.unit})` : field.label,
    htmlFor: field.id,
    helpText: field.help
  });
  row.appendChild(labelRow);

  const input = doc.createElement('input');
  input.type = 'number';
  input.id = field.id;
  input.value = formatNumeric(field.fallback);
  if (field.min !== undefined) input.min = String(field.min);
  if (field.max !== undefined) input.max = String(field.max);
  if (field.step !== undefined) input.step = String(field.step);
  row.appendChild(input);

  section.appendChild(row);
}

function appendPolarAxisRow(section, doc) {
  const row = doc.createElement('div');
  row.className = 'input-row';

  const { row: labelRow } = createLabelRow(doc, {
    labelText: 'Directivity Planes',
    helpText: 'Choose which directivity planes to generate.'
  });
  row.appendChild(labelRow);

  const options = doc.createElement('div');
  options.className = 'polar-axis-options';

  POLAR_AXIS_METADATA.forEach((option) => {
    const optionLabel = doc.createElement('label');
    optionLabel.className = 'polar-axis-option';
    const input = doc.createElement('input');
    input.type = 'checkbox';
    input.id = option.id;
    input.checked = true;
    optionLabel.appendChild(input);

    const copy = doc.createElement('div');
    copy.className = 'polar-axis-option-copy';

    const text = doc.createElement('span');
    text.textContent = option.label;
    copy.appendChild(text);

    const helpTrigger = createHelpTrigger(doc, { labelText: option.label, helpText: option.help });
    if (helpTrigger) {
      copy.appendChild(helpTrigger);
    }

    optionLabel.appendChild(copy);

    options.appendChild(optionLabel);
  });

  row.appendChild(options);
  section.appendChild(row);
}

export function renderPolarSettingsSection(doc = document) {
  const container = getElement(doc, POLAR_SETTINGS_CONTAINER_ID);
  if (!container || typeof doc?.createElement !== 'function') {
    return null;
  }

  container.innerHTML = '';

  const section = doc.createElement('details');
  section.className = 'section';
  section.id = 'directivity-map';

  // Restore collapse state from localStorage (guarded for non-browser envs)
  const storageKey = 'wg-section-collapsed-directivity-map';
  const store = typeof localStorage !== 'undefined' ? localStorage : null;
  const wasCollapsed = store ? store.getItem(storageKey) : null;
  section.open = wasCollapsed !== 'true'; // default open

  const summary = doc.createElement('summary');
  summary.textContent = POLAR_SECTION_METADATA.title;
  section.appendChild(summary);

  // Persist collapse state on toggle
  if (store) {
    section.addEventListener('toggle', () => {
      store.setItem(storageKey, section.open ? 'false' : 'true');
    });
  }

  appendSectionNote(section, doc, POLAR_SECTION_METADATA.description);

  POLAR_NUMERIC_FIELDS
    .filter((field) => field.id !== DIAGONAL_ANGLE_INPUT_ID)
    .forEach((field) => appendPolarNumberRow(section, field, doc));

  appendPolarAxisRow(section, doc);
  appendPolarNumberRow(section, getNumericFieldById(DIAGONAL_ANGLE_INPUT_ID), doc);

  container.appendChild(section);
  return section;
}

export function ensurePolarControlsRendered(doc = document) {
  if (getElement(doc, 'polar-angle-start')) {
    return getElement(doc, POLAR_SETTINGS_CONTAINER_ID) || true;
  }
  return renderPolarSettingsSection(doc);
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

export function applyPolarUiStateToDom(uiState, doc = document) {
  if (!uiState || typeof uiState !== 'object') return;

  POLAR_NUMERIC_FIELDS.forEach(({ id, uiKey }) => {
    const element = getElement(doc, id);
    if (!element) return;
    const nextValue = formatNumeric(uiState[uiKey]);
    if (element.value !== nextValue) {
      element.value = nextValue;
    }
  });

  applyPolarSelectionToDom(uiState, doc);
}

export function syncPolarControlsFromBlocks(blocks, doc = document) {
  const uiState = derivePolarUiStateFromBlocks(blocks);
  applyPolarUiStateToDom(uiState, doc);
  return uiState;
}

export function syncPolarControlsFromState(params = {}, doc = document) {
  const uiState = resolvePolarUiState(params);
  applyPolarUiStateToDom(uiState, doc);
  return uiState;
}

export function getPolarStateSignature(params = {}) {
  return JSON.stringify(resolvePolarUiState(params));
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

export function readPolarStateSettings(params = {}) {
  const uiState = resolvePolarUiState(params);
  const { angleStart, angleEnd, angleCount } = computeAngleRange(uiState);

  if (uiState.enabledAxes.length === 0) {
    return {
      ok: false,
      validationError: 'Select at least one polar axis (horizontal, vertical, or diagonal).'
    };
  }

  return {
    ok: true,
    angleRangeArray: [angleStart, angleEnd, angleCount],
    polarRange: `${angleStart},${angleEnd},${angleCount}`,
    distance: uiState.distance,
    normAngle: uiState.normAngle,
    diagonalAngle: uiState.diagonalAngle,
    enabledAxes: [...uiState.enabledAxes]
  };
}

export function isPolarControlId(id) {
  return Boolean(getNumericFieldById(id) || isPolarAxisControlId(id));
}

export function buildPolarStatePatchForControl(id, currentParams = {}, doc = document) {
  if (!isPolarControlId(id)) {
    return null;
  }

  const nextUiState = resolvePolarUiState(currentParams);
  const numericField = getNumericFieldById(id);
  if (numericField) {
    nextUiState[numericField.uiKey] = readNumberInput(doc, numericField.id, nextUiState[numericField.uiKey]);
  } else if (isPolarAxisControlId(id)) {
    nextUiState.enabledAxes = getEnabledAxesFromDom(doc);
  }

  return buildPersistedPolarStatePatch(currentParams, nextUiState);
}

export function buildPolarStatePatchFromConfig(currentParams = {}, polarConfig = null) {
  const nextUiState = derivePolarUiStateFromConfig(polarConfig);
  return buildPersistedPolarStatePatch(currentParams, nextUiState);
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
