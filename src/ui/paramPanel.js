import { PARAM_SCHEMA } from '../config/schema.js';
import { GlobalState } from '../state.js';
import { resolveAutoQuadrantsForState } from '../modules/design/index.js';
import { normalizeParamInput } from './paramInput.js';
import { appendSectionNote, createLabelRow } from './helpAffordance.js';
import { getParameterSections } from './parameterInventory.js';
import { trapFocus } from './focusTrap.js';
import { mountFreeformProfileEditor } from './freeformProfileEditor.js';
import {
  validateOutputName,
  validateCounter,
  validateJobLabel,
  validateFormula,
  showInputError,
  hideInputError,
} from './inputValidation.js';

// Available mathematical functions from ATH user guide (Appendix A)
const FORMULA_REFERENCE = {
  parameters: [
    {
      name: 'p',
      description: 'Azimuthal angle around waveguide axis (0 to 2π)',
    },
  ],
  functions: [
    { name: 'sin(x)', description: 'Sine function' },
    { name: 'cos(x)', description: 'Cosine function' },
    { name: 'tan(x)', description: 'Tangent function' },
    { name: 'asin(x)', description: 'Arc sine' },
    { name: 'acos(x)', description: 'Arc cosine' },
    { name: 'atan(x)', description: 'Arc tangent' },
    { name: 'atan2(y,x)', description: 'Two-argument arc tangent' },
    { name: 'sinh(x)', description: 'Hyperbolic sine' },
    { name: 'cosh(x)', description: 'Hyperbolic cosine' },
    { name: 'tanh(x)', description: 'Hyperbolic tangent' },
    { name: 'asinh(x)', description: 'Inverse hyperbolic sine' },
    { name: 'acosh(x)', description: 'Inverse hyperbolic cosine' },
    { name: 'atanh(x)', description: 'Inverse hyperbolic tangent' },
    { name: 'abs(x)', description: 'Absolute value' },
    { name: 'sqrt(x)', description: 'Square root' },
    { name: 'cbrt(x)', description: 'Cube root' },
    { name: 'pow(x,y) or x^y', description: 'Power function' },
    { name: 'exp(x)', description: 'Exponential (e^x)' },
    { name: 'exp2(x)', description: '2^x' },
    { name: 'expm1(x)', description: 'e^x - 1' },
    { name: 'log(x)', description: 'Natural logarithm' },
    { name: 'log10(x)', description: 'Base 10 logarithm' },
    { name: 'log2(x)', description: 'Base 2 logarithm' },
    {
      name: 'log1p(x)',
      description: 'log(1 + x) [currently has issues, use ln(1+x) instead]',
    },
    { name: 'floor(x)', description: 'Floor (round down)' },
    { name: 'ceil(x)', description: 'Ceiling (round up)' },
    { name: 'round(x)', description: 'Round to nearest' },
    { name: 'trunc(x)', description: 'Truncate to integer' },
    { name: 'fmod(x,y)', description: 'Floating-point remainder' },
    { name: 'remainder(x,y)', description: 'IEEE remainder' },
    { name: 'fmin(x,y)', description: 'Minimum of x and y' },
    { name: 'fmax(x,y)', description: 'Maximum of x and y' },
    { name: 'hypot(x,y)', description: 'sqrt(x² + y²)' },
    { name: 'copysign(x,y)', description: 'Copy sign of y to x' },
    { name: 'fdim(x,y)', description: 'Positive difference' },
    { name: 'fma(x,y,z)', description: 'x*y + z (fused)' },
    { name: 'pi or pi()', description: 'Returns π (3.14159...)' },
    { name: 'deg(x)', description: 'Convert radians to degrees' },
    { name: 'rad(x)', description: 'Convert degrees to radians' },
  ],
  examples: [
    '45 + 10*cos(p)^2',
    '140 * (abs(cos(p)/1.6)^3 + abs(sin(p)/1)^4)^(-1/4.5)',
    '0.58 + 0.2*cos(p)^2',
    '48.5 - 5.6*cos(2*p)^5 - 31*sin(p)^12',
  ],
};

function getParamKey(element) {
  if (!element) return null;
  return (
    element.dataset?.paramKey ||
    element.getAttribute?.('data-param-key') ||
    element.attributes?.['data-param-key'] ||
    null
  );
}

function isDescendantOf(element, root) {
  if (!element || !root) return false;
  if (typeof root.contains === 'function') return root.contains(element);

  let current = element;
  while (current) {
    if (current === root) return true;
    current = current.parentNode || current.parentElement || null;
  }
  return false;
}

function findControlByParamKey(root, key, tagName) {
  if (!root) return null;

  const matches =
    getParamKey(root) === key && String(root.tagName || '').toUpperCase() === String(tagName || '');
  if (matches) return root;

  for (const child of root.children || []) {
    const match = findControlByParamKey(child, key, tagName);
    if (match) return match;
  }
  return null;
}

function isNumericLiteral(value) {
  return (
    /^[+-]?(?:(?:\d+\.?\d*)|(?:\.\d+))(?:e[+-]?\d+)?$/i.test(value) ||
    /^[+-]?(?:infinity|nan)$/i.test(value)
  );
}

function validateNumericValue(input, def) {
  const inputMode = getControlInputMode(def);
  const requiresNumber = input?.type === 'number' || inputMode === 'number';
  const supportsFormula = inputMode === 'formula';
  if (!requiresNumber && !supportsFormula) return { valid: true };

  const raw = String(input?.value ?? '').trim();
  if (!raw) {
    return { valid: false, error: 'Enter a finite number.' };
  }

  const numeric = Number(raw);
  if (!Number.isFinite(numeric)) {
    if (requiresNumber || isNumericLiteral(raw)) {
      return { valid: false, error: 'Enter a finite number.' };
    }
    return { valid: true };
  }

  if (def?.min !== undefined && numeric < def.min) {
    return def.max !== undefined
      ? { valid: false, error: `Enter a value between ${def.min} and ${def.max}.` }
      : { valid: false, error: `Enter a value of at least ${def.min}.` };
  }
  if (def?.max !== undefined && numeric > def.max) {
    return def.min !== undefined
      ? { valid: false, error: `Enter a value between ${def.min} and ${def.max}.` }
      : { valid: false, error: `Enter a value of at most ${def.max}.` };
  }
  if (def?.integer === true && !Number.isInteger(numeric)) {
    return { valid: false, error: 'Enter a whole number.' };
  }

  return { valid: true };
}

function normalizeInteriorPoints(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((point) => Array.isArray(point) && point.length >= 2)
    .map((point) => [Number(point[0]), Number(point[1])])
    .filter((point) => point.every(Number.isFinite))
    .sort((a, b) => a[0] - b[0])
    .slice(0, 62);
}

function clampInteriorZ(value, length, fallback) {
  if (!Number.isFinite(value)) return fallback;
  const inset = Math.min(0.001, length / 1000);
  return Math.min(length - inset, Math.max(inset, value));
}

function flashClampedPoint(input) {
  input.classList.add('freeform-point-clamped');
  const timer = setTimeout(() => input.classList.remove('freeform-point-clamped'), 450);
  timer?.unref?.();
}

function normalizeStations(value) {
  if (!Array.isArray(value) || value.length < 2) {
    return [
      { t: 0, shape: 'circle' },
      { t: 1, shape: 'ellipse' },
    ];
  }
  let stations = value
    .map((station) => ({ ...station, t: Number(station?.t) }))
    .sort((a, b) => a.t - b.t);
  if (stations.length > 32) {
    stations = [...stations.slice(0, 31), stations[stations.length - 1]];
  }
  stations[0] = { t: 0, shape: 'circle' };
  for (let index = 1; index < stations.length; index += 1) {
    if (!['ellipse', 'superellipse', 'rounded_rectangle'].includes(stations[index].shape)) {
      stations[index] = { t: stations[index].t, shape: 'ellipse' };
    }
  }
  stations[stations.length - 1] = { ...stations[stations.length - 1], t: 1 };
  return stations;
}

export class ParamPanel {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) throw new Error(`Container ${containerId} not found`);
    this.simulationSettingsContainer = document.getElementById('simulation-settings-container');
    this.simulationContainer = document.getElementById('simulation-param-container');
    this.formulaInfoVisible = false;
    this.profileError = '';
    this.profileErrorStateVersion = null;
    this.controlIdCounter = 0;
    this.freeformEditor = null;
    this.freeformVisibility = { H: true, V: true };
    this.init();
  }

  init() {
    // Subscribe to state changes to update UI
    // But we also need to build the initial UI
    this.renderParams();
  }

  renderParams() {
    this.container.innerHTML = ''; // Clear existing

    // 1. Model Type Selector (Always present)
    // Note: In the final design, this might be outside the generated params,
    // but for now we can rely on the existing HTML structure or rebuild it here.
    // The existing HTML has #model-type outside the param containers.
    // We will target specific containers for specific parameter groups.

    // Actually, let's assume we are populating a specific "params-container" div
    // instead of replacing the entire sidebar.
  }

  // Create the full UI structure
  createFullPanel() {
    if (this.profileError && this.profileErrorStateVersion !== GlobalState.getVersion()) {
      this.profileError = '';
      this.profileErrorStateVersion = null;
    }
    const focusedControl = this.captureFocusedControl();
    this.freeformEditor?.destroy();
    this.freeformEditor = null;
    this.container.innerHTML = '';
    if (this.simulationSettingsContainer) {
      this.simulationSettingsContainer.innerHTML = '';
    }
    if (this.simulationContainer) {
      this.simulationContainer.innerHTML = '';
    }
    this.controlIdCounter = 0;
    const state = GlobalState.get();
    const type = state.type;

    this.renderSections(this.container, getParameterSections('geometry', type), state.params, {
      includeOwners: ['paramPanel'],
    });

    if (this.simulationSettingsContainer) {
      this.renderSections(
        this.simulationSettingsContainer,
        getParameterSections('simulation', type),
        state.params,
        {
          includeIds: ['frequency-sweep'],
        }
      );
    }

    if (this.simulationContainer) {
      this.renderSections(
        this.simulationContainer,
        getParameterSections('simulation', type),
        state.params,
        {
          includeIds: ['source-definition', 'solve-export-mesh'],
        }
      );
    }

    this.restoreFocusedControl(focusedControl);
    this.renderProfileErrorStrip();
  }

  captureFocusedControl() {
    const activeElement = document.activeElement;
    const paramKey = getParamKey(activeElement);
    const roots = [this.container, this.simulationSettingsContainer, this.simulationContainer];
    if (!paramKey || !roots.some((root) => isDescendantOf(activeElement, root))) {
      return null;
    }

    const selectionStart = activeElement.selectionStart;
    const selectionEnd = activeElement.selectionEnd;
    return {
      paramKey,
      tagName: String(activeElement.tagName || '').toUpperCase(),
      selection:
        typeof selectionStart === 'number' && typeof selectionEnd === 'number'
          ? {
              start: selectionStart,
              end: selectionEnd,
              direction: activeElement.selectionDirection || 'none',
            }
          : null,
    };
  }

  restoreFocusedControl(focusedControl) {
    if (!focusedControl) return;

    const roots = [this.container, this.simulationSettingsContainer, this.simulationContainer];
    const replacement = roots
      .map((root) => findControlByParamKey(root, focusedControl.paramKey, focusedControl.tagName))
      .find(Boolean);
    if (!replacement || typeof replacement.focus !== 'function') return;

    try {
      replacement.focus({ preventScroll: true });
    } catch {
      replacement.focus();
    }

    if (!focusedControl.selection || typeof replacement.setSelectionRange !== 'function') return;
    try {
      replacement.setSelectionRange(
        focusedControl.selection.start,
        focusedControl.selection.end,
        focusedControl.selection.direction
      );
    } catch {
      // Some input types do not support text selections.
    }
  }

  createSection(title) {
    const div = document.createElement('div');
    div.className = 'section';
    const h2 = document.createElement('h2');
    h2.textContent = title;
    div.appendChild(h2);
    return div;
  }

  createDetailsSection(summaryText, id) {
    const details = document.createElement('details');
    details.className = 'section';
    if (id) details.id = id;

    // Restore collapse state from localStorage (guarded for non-browser envs)
    const storageKey = `wg-section-collapsed-${id || summaryText}`;
    const store = typeof localStorage !== 'undefined' ? localStorage : null;
    const wasCollapsed = store ? store.getItem(storageKey) : null;
    details.open = wasCollapsed !== 'true'; // default open

    const summary = document.createElement('summary');
    summary.textContent = summaryText;
    details.appendChild(summary);

    // Persist collapse state on toggle
    if (store) {
      details.addEventListener('toggle', () => {
        store.setItem(storageKey, details.open ? 'false' : 'true');
      });
    }

    return details;
  }

  renderSections(target, sections, params, { includeIds = null, includeOwners = null } = {}) {
    if (!target) return;

    sections.forEach((section) => {
      if (Array.isArray(includeIds) && !includeIds.includes(section.id)) {
        return;
      }
      if (Array.isArray(includeOwners) && !includeOwners.includes(section.owner)) {
        return;
      }

      if (section.kind === 'model-selector') {
        target.appendChild(this.createModelTypeSection());
        return;
      }

      const sectionNode = this.createDetailsSection(section.title, section.id);
      if (GlobalState.get().type === 'FREEFORM' && section.id === 'core-profile') {
        this.freeformEditor = mountFreeformProfileEditor(sectionNode, {
          params,
          visibility: this.freeformVisibility,
          onCommit: (patch) => {
            this.setProfileError(null);
            return GlobalState.update(patch);
          },
        });
        this.bindFreeformParamHighlighting(sectionNode);
      }
      appendSectionNote(sectionNode, document, section.description);
      (section.groups || []).forEach(({ group, keys }) => {
        const schemaGroup = PARAM_SCHEMA[group] || {};
        keys.forEach((key) => {
          const def = schemaGroup[key];
          if (def && this.shouldRenderControl(key, params)) {
            sectionNode.appendChild(this.createControlRow(key, def, params[key]));
          }
        });
      });
      target.appendChild(sectionNode);
    });
  }

  bindFreeformParamHighlighting(sectionNode) {
    const findParam = (target) => {
      let node = target;
      while (node && node !== sectionNode) {
        const param = node.getAttribute?.('data-param');
        if (param) return param;
        node = node.parentNode;
      }
      return null;
    };
    const setHighlight = (event, active) => {
      const param = findParam(event.target);
      if (param) this.freeformEditor?.highlightHandle(param, active);
    };
    if (typeof sectionNode.addEventListener === 'function') {
      sectionNode.addEventListener('mouseover', (event) => setHighlight(event, true));
      sectionNode.addEventListener('mouseout', (event) => setHighlight(event, false));
    } else {
      sectionNode.onmouseover = (event) => setHighlight(event, true);
      sectionNode.onmouseout = (event) => setHighlight(event, false);
    }
  }

  shouldRenderControl(key, params = {}) {
    // ICW: the rollback axial-target inputs only apply when Termination =
    // Rollback. Horn Length (L) is the flat-baffle axial target and is ignored
    // by the rollback solver, so hide it there rather than imply it has effect.
    if (key === 'depth' || key === 'theta1_deg') {
      return params.termination === 'rollback';
    }
    if (key === 'L' && params.termination === 'rollback') {
      return false;
    }
    if (
      (key === 'coverage_angle' || key === 'hold_start' || key === 'hold_end') &&
      params.termination === 'rollback'
    ) {
      return false;
    }

    if (key !== 'slotLength') return true;

    const angle = Number(params.throatExtAngle ?? 0);
    const slotLength = Number(params.slotLength ?? 0);
    if (Number.isFinite(slotLength) && Math.abs(slotLength) > 1e-12) return true;
    if (!Number.isFinite(angle)) return true;
    return Math.abs(angle) > 1e-12;
  }

  createModelTypeSection() {
    const typeSection = this.createSection('Model Type');
    const typeRow = document.createElement('div');
    typeRow.className = 'input-row';
    const typeSelect = document.createElement('select');
    typeSelect.id = 'model-type';
    typeSelect.setAttribute('data-param-key', 'model-type');
    typeSelect.setAttribute('aria-label', 'Model type');

    const currentType = GlobalState.get().type;
    ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM'].forEach((type) => {
      const option = document.createElement('option');
      option.value = type;
      option.textContent = type;
      if (type === currentType) {
        option.selected = true;
      }
      typeSelect.appendChild(option);
    });

    typeSelect.onchange = (e) => {
      GlobalState.update({}, e.target.value);
    };

    typeRow.appendChild(typeSelect);
    typeSection.appendChild(typeRow);
    return typeSection;
  }

  createControlRow(key, def, currentValue) {
    const row = document.createElement('div');
    row.className = 'input-row';
    row.setAttribute('data-param-key', key);
    row.setAttribute('data-param', key);

    const controlId = def.controlId || `param-${key}-${this.controlIdCounter++}`;
    const labelText = def.unit ? `${def.label} (${def.unit})` : def.label;
    const inputMode = getControlInputMode(def);
    const isFormulaField = inputMode === 'formula';

    const { row: labelRow } = createLabelRow(document, {
      labelText,
      htmlFor: controlId,
      helpText: def.tooltip || '',
    });

    if (isFormulaField) {
      const infoBtn = document.createElement('button');
      infoBtn.type = 'button';
      infoBtn.className = 'formula-info-btn';
      infoBtn.textContent = 'ƒ';
      infoBtn.title = `View formula reference for ${def.label}`;
      infoBtn.setAttribute('aria-label', `View formula reference for ${def.label}`);
      infoBtn.setAttribute('data-param-key', key);
      infoBtn.onclick = (e) => {
        e.preventDefault();
        this.showFormulaInfo(def.label);
      };
      labelRow.appendChild(infoBtn);
    }

    row.appendChild(labelRow);

    if (def.type === 'points') {
      row.appendChild(this.createPointsControl(key, currentValue, controlId));
    } else if (def.type === 'stations') {
      row.appendChild(this.createStationsControl(key, currentValue, controlId));
    } else if (inputMode === 'formula' || inputMode === 'number' || inputMode === 'text') {
      const wrapper = document.createElement('div');
      wrapper.className = isFormulaField ? 'formula-input-wrapper' : 'param-input-wrapper';

      const input = document.createElement('input');
      input.type = inputMode === 'number' ? 'number' : 'text';
      input.id = controlId;
      input.value = currentValue ?? '';
      input.setAttribute('data-param-key', key);
      input.setAttribute('data-param', key);
      if (isFormulaField) {
        input.className = 'formula-input';
        input.placeholder = 'e.g., 45 + 10*cos(p)';
      } else if (inputMode === 'text') {
        input.placeholder = def.placeholder || '';
      }

      if (inputMode === 'number') {
        if (def.min !== undefined) input.min = String(def.min);
        if (def.max !== undefined) input.max = String(def.max);
        if (def.step !== undefined) input.step = String(def.step);
      } else if (def.type === 'range' && def.min !== undefined && def.max !== undefined) {
        input.title = `Range: ${def.min} to ${def.max}`;
      }

      input.oninput = (e) => {
        this.validateInputOnChange(e.target, key, def);
      };

      input.onchange = (e) => {
        const valid = this.validateInputOnChange(e.target, key, def);
        if (valid !== false) {
          this.updateParam(key, normalizeParamInput(e.target.value));
        }
      };

      wrapper.appendChild(input);
      row.appendChild(wrapper);
    } else if (inputMode === 'select') {
      const wrapper = document.createElement('div');
      wrapper.className =
        def.autoAction === 'quadrants' ? 'param-select-action-wrapper' : 'param-input-wrapper';
      const select = document.createElement('select');
      select.id = controlId;
      select.setAttribute('data-param-key', key);
      select.setAttribute('data-param', key);
      def.options.forEach((opt) => {
        const option = document.createElement('option');
        option.value = opt.value;
        option.textContent = opt.label;
        if (String(opt.value) === String(currentValue)) option.selected = true;
        select.appendChild(option);
      });
      select.onchange = (e) => {
        const val = isNaN(parseFloat(e.target.value)) ? e.target.value : parseFloat(e.target.value);
        this.updateParam(key, val);
      };
      wrapper.appendChild(select);
      if (def.autoAction === 'quadrants') {
        const autoButton = document.createElement('button');
        autoButton.type = 'button';
        autoButton.className = 'param-auto-btn';
        autoButton.textContent = 'Auto';
        autoButton.title = 'Choose quadrants from detected symmetry';
        autoButton.setAttribute('aria-label', 'Choose quadrants from detected symmetry');
        autoButton.setAttribute('data-param-key', key);
        autoButton.onclick = (e) => {
          e.preventDefault();
          this.applyAutoQuadrants();
        };
        wrapper.appendChild(autoButton);
      }
      row.appendChild(wrapper);
    }

    return row;
  }

  createPointsControl(key, currentValue, controlId) {
    const wrapper = document.createElement('div');
    wrapper.className = 'freeform-points-editor';
    const points = normalizeInteriorPoints(currentValue);
    const stateParams = GlobalState.get().params || {};
    const length = Number(stateParams.length);
    const safeLength = Number.isFinite(length) && length > 0 ? length : 120;
    const throatRadius = Number(stateParams.throatRadius);
    const mouthRadius = Number(
      key === 'interiorH' ? stateParams.mouthRadiusH : stateParams.mouthRadiusV
    );
    const startRadius = Number.isFinite(throatRadius) ? throatRadius : 12.7;
    const endRadius = Number.isFinite(mouthRadius) ? mouthRadius : 140;
    const commitPoints = (nextPoints) => {
      this.updateParam(key, normalizeInteriorPoints(nextPoints));
    };

    const header = document.createElement('div');
    header.className = 'freeform-point-header';
    for (const text of ['z (mm)', 'r (mm)', '']) {
      const cell = document.createElement('span');
      cell.textContent = text;
      header.appendChild(cell);
    }
    wrapper.appendChild(header);

    if (points.length === 0) {
      const hint = document.createElement('p');
      hint.className = 'freeform-points-empty';
      hint.textContent = 'No interior points — throat and mouth define a 2-anchor curve';
      wrapper.appendChild(hint);
    }

    points.forEach((point, index) => {
      const pointRow = document.createElement('div');
      pointRow.className = 'freeform-point-row';

      const zInput = document.createElement('input');
      zInput.type = 'number';
      zInput.id = index === 0 ? controlId : `${controlId}-z-${index}`;
      zInput.min = '0';
      zInput.max = String(safeLength);
      zInput.step = '0.1';
      zInput.value = point[0];
      zInput.setAttribute('data-param-key', key);
      zInput.setAttribute('data-param', key);
      zInput.setAttribute('aria-label', `${key} point ${index + 1} z`);
      zInput.onchange = (event) => {
        const requested = Number(event.target.value);
        const z = clampInteriorZ(requested, safeLength, point[0]);
        event.target.value = z;
        if (!Number.isFinite(requested) || z !== requested) {
          flashClampedPoint(event.target);
        }
        commitPoints(points.map((item, itemIndex) => (itemIndex === index ? [z, item[1]] : item)));
      };
      pointRow.appendChild(zInput);

      const radiusInput = document.createElement('input');
      radiusInput.type = 'number';
      radiusInput.step = '0.1';
      radiusInput.value = point[1];
      radiusInput.setAttribute('data-param-key', key);
      radiusInput.setAttribute('data-param', key);
      radiusInput.setAttribute('aria-label', `${key} point ${index + 1} radius`);
      radiusInput.onchange = (event) => {
        const radius = Number(event.target.value);
        if (!Number.isFinite(radius)) {
          showInputError(event.target, 'Enter a finite radius.');
          return;
        }
        hideInputError(event.target, true);
        commitPoints(
          points.map((item, itemIndex) => (itemIndex === index ? [item[0], radius] : item))
        );
      };
      pointRow.appendChild(radiusInput);

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'freeform-point-remove';
      remove.textContent = '−';
      remove.title = 'Remove point';
      remove.setAttribute('aria-label', `Remove ${key} point ${index + 1}`);
      remove.setAttribute('data-param-key', key);
      remove.onclick = (event) => {
        event.preventDefault();
        commitPoints(points.filter((_item, itemIndex) => itemIndex !== index));
      };
      pointRow.appendChild(remove);
      wrapper.appendChild(pointRow);
    });

    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'freeform-point-add';
    add.textContent = 'Add point';
    add.disabled = points.length >= 62;
    add.setAttribute('data-param-key', key);
    add.onclick = (event) => {
      event.preventDefault();
      if (points.length >= 62) return;
      const anchors = [[0, startRadius], ...points, [safeLength, endRadius]];
      let insertAfter = 0;
      let widestGap = -1;
      for (let index = 0; index < anchors.length - 1; index += 1) {
        const gap = anchors[index + 1][0] - anchors[index][0];
        if (gap > widestGap) {
          widestGap = gap;
          insertAfter = index;
        }
      }
      const [z0, r0] = anchors[insertAfter];
      const [z1, r1] = anchors[insertAfter + 1];
      const z = (z0 + z1) / 2;
      const ratio = z1 === z0 ? 0.5 : (z - z0) / (z1 - z0);
      const radius = Math.round((r0 + ratio * (r1 - r0)) * 1e6) / 1e6;
      commitPoints([...points, [z, radius]]);
    };
    wrapper.appendChild(add);
    return wrapper;
  }

  createStationsControl(key, currentValue, controlId) {
    const wrapper = document.createElement('div');
    wrapper.className = 'freeform-stations-editor';
    const stations = normalizeStations(currentValue);
    const commitStations = (nextStations) => {
      const sorted = normalizeStations(nextStations).slice(0, 32);
      this.updateParam(key, sorted);
    };

    stations.forEach((station, index) => {
      const isFirst = index === 0;
      const isLast = index === stations.length - 1;
      const stationRow = document.createElement('div');
      stationRow.className = 'freeform-station-row';

      const position = document.createElement('input');
      position.type = 'number';
      position.id = index === 0 ? controlId : `${controlId}-t-${index}`;
      position.min = '0';
      position.max = '1';
      position.step = '0.01';
      position.value = isFirst ? 0 : isLast ? 1 : station.t;
      position.disabled = isFirst || isLast;
      position.setAttribute('data-param-key', key);
      position.setAttribute('data-param', key);
      position.setAttribute('aria-label', `Station ${index + 1} position`);
      position.onchange = (event) => {
        const t = Number(event.target.value);
        const duplicate = stations.some(
          (candidate, candidateIndex) =>
            candidateIndex !== index && Math.abs(candidate.t - t) < 1e-9
        );
        if (!Number.isFinite(t) || t <= 0 || t >= 1 || duplicate) {
          showInputError(event.target, 'Station positions must be unique numbers between 0 and 1.');
          return;
        }
        hideInputError(event.target, true);
        commitStations(
          stations.map((item, itemIndex) => (itemIndex === index ? { ...item, t } : item))
        );
      };
      stationRow.appendChild(position);

      const shape = document.createElement('select');
      shape.setAttribute('data-param-key', key);
      shape.setAttribute('data-param', key);
      shape.setAttribute('aria-label', `Station ${index + 1} shape`);
      const shapeOptions = isFirst
        ? [{ value: 'circle', label: 'Circle' }]
        : [
            { value: 'ellipse', label: 'Ellipse' },
            { value: 'superellipse', label: 'Superellipse' },
            { value: 'rounded_rectangle', label: 'Rounded rectangle' },
          ];
      shapeOptions.forEach((optionDef) => {
        const option = document.createElement('option');
        option.value = optionDef.value;
        option.textContent = optionDef.label;
        option.selected = optionDef.value === station.shape;
        shape.appendChild(option);
      });
      shape.value = isFirst ? 'circle' : station.shape;
      shape.onchange = (event) => {
        const nextShape = event.target.value;
        const nextStation = { t: station.t, shape: nextShape };
        if (nextShape === 'superellipse') {
          nextStation.exponent = Number.isFinite(Number(station.exponent))
            ? Number(station.exponent)
            : 4;
        } else if (nextShape === 'rounded_rectangle') {
          nextStation.cornerRatio = Number.isFinite(Number(station.cornerRatio))
            ? Number(station.cornerRatio)
            : 0.12;
        }
        commitStations(
          stations.map((item, itemIndex) => (itemIndex === index ? nextStation : item))
        );
      };
      stationRow.appendChild(shape);

      const parameterDef =
        station.shape === 'superellipse'
          ? { key: 'exponent', min: 2, max: 16, step: 0.1, fallback: 4, label: 'Exponent' }
          : station.shape === 'rounded_rectangle'
            ? {
                key: 'cornerRatio',
                min: 0.02,
                max: 1,
                step: 0.01,
                fallback: 0.12,
                label: 'Corner ratio',
              }
            : null;
      if (parameterDef) {
        const parameter = document.createElement('input');
        parameter.type = 'number';
        parameter.min = String(parameterDef.min);
        parameter.max = String(parameterDef.max);
        parameter.step = String(parameterDef.step);
        parameter.value = station[parameterDef.key] ?? parameterDef.fallback;
        parameter.setAttribute('data-param-key', key);
        parameter.setAttribute('aria-label', `Station ${index + 1} ${parameterDef.label}`);
        parameter.onchange = (event) => {
          const numeric = Number(event.target.value);
          if (
            !Number.isFinite(numeric) ||
            numeric < parameterDef.min ||
            numeric > parameterDef.max
          ) {
            showInputError(
              event.target,
              `${parameterDef.label} must be between ${parameterDef.min} and ${parameterDef.max}.`
            );
            return;
          }
          hideInputError(event.target, true);
          commitStations(
            stations.map((item, itemIndex) =>
              itemIndex === index ? { ...item, [parameterDef.key]: numeric } : item
            )
          );
        };
        stationRow.appendChild(parameter);
      }

      if (!isFirst && !isLast) {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'freeform-station-remove';
        remove.textContent = '−';
        remove.title = 'Remove station';
        remove.setAttribute('aria-label', `Remove station ${index + 1}`);
        remove.setAttribute('data-param-key', key);
        remove.onclick = (event) => {
          event.preventDefault();
          commitStations(stations.filter((_item, itemIndex) => itemIndex !== index));
        };
        stationRow.appendChild(remove);
      }

      wrapper.appendChild(stationRow);
    });

    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'freeform-station-add';
    add.textContent = 'Add station';
    add.disabled = stations.length >= 32;
    add.setAttribute('data-param-key', key);
    add.onclick = (event) => {
      event.preventDefault();
      if (stations.length >= 32) return;
      let insertAfter = 0;
      let widestGap = -1;
      for (let index = 0; index < stations.length - 1; index += 1) {
        const gap = stations[index + 1].t - stations[index].t;
        if (gap > widestGap) {
          widestGap = gap;
          insertAfter = index;
        }
      }
      const t = (stations[insertAfter].t + stations[insertAfter + 1].t) / 2;
      commitStations([...stations, { t, shape: 'ellipse' }]);
    };
    wrapper.appendChild(add);
    return wrapper;
  }

  applyAutoQuadrants() {
    const state = GlobalState.get();
    this.updateParam('quadrants', resolveAutoQuadrantsForState(state));
  }

  showFormulaInfo(fieldLabel = null) {
    let infoPanel = document.getElementById('formula-info-panel');
    if (infoPanel) {
      this.updateFormulaInfoContext(infoPanel, fieldLabel);
      infoPanel.classList.add('visible');
      infoPanel.setAttribute('aria-hidden', 'false');
      this._formulaInfoReleaseFocus = trapFocus(infoPanel, {
        initialFocus: infoPanel.querySelector('.formula-info-close'),
      });
      return;
    }

    infoPanel = document.createElement('div');
    infoPanel.id = 'formula-info-panel';
    infoPanel.className = 'formula-info-panel visible';
    infoPanel.setAttribute('role', 'dialog');
    infoPanel.setAttribute('aria-modal', 'true');
    infoPanel.setAttribute('aria-labelledby', 'formula-info-title');
    infoPanel.setAttribute('aria-hidden', 'false');

    const header = document.createElement('div');
    header.className = 'formula-info-header';
    header.innerHTML = `
            <div>
                <h4 class="formula-info-title" id="formula-info-title">Formula Reference</h4>
                <p class="formula-info-context"></p>
            </div>
            <button class="formula-info-close" title="Close">&times;</button>
        `;
    infoPanel.appendChild(header);

    const paramsSection = document.createElement('div');
    paramsSection.className = 'formula-info-section';
    paramsSection.innerHTML = `<h5>Parameters</h5>`;
    const paramsList = document.createElement('div');
    paramsList.className = 'formula-list';
    FORMULA_REFERENCE.parameters.forEach((p) => {
      const item = document.createElement('div');
      item.className = 'formula-item';
      item.innerHTML = `<code>${p.name}</code><span>${p.description}</span>`;
      paramsList.appendChild(item);
    });
    paramsSection.appendChild(paramsList);
    infoPanel.appendChild(paramsSection);

    const funcsSection = document.createElement('div');
    funcsSection.className = 'formula-info-section';
    funcsSection.innerHTML = `<h5>Functions</h5>`;
    const funcsList = document.createElement('div');
    funcsList.className = 'formula-list scrollable';
    FORMULA_REFERENCE.functions.forEach((f) => {
      const item = document.createElement('div');
      item.className = 'formula-item';
      item.innerHTML = `<code>${f.name}</code><span>${f.description}</span>`;
      funcsList.appendChild(item);
    });
    funcsSection.appendChild(funcsList);
    infoPanel.appendChild(funcsSection);

    const examplesSection = document.createElement('div');
    examplesSection.className = 'formula-info-section';
    examplesSection.innerHTML = `<h5>Examples</h5>`;
    const examplesList = document.createElement('div');
    examplesList.className = 'formula-examples';
    FORMULA_REFERENCE.examples.forEach((ex) => {
      const item = document.createElement('code');
      item.className = 'formula-example';
      item.textContent = ex;
      examplesList.appendChild(item);
    });
    examplesSection.appendChild(examplesList);
    infoPanel.appendChild(examplesSection);

    const closeBtn = header.querySelector('.formula-info-close');
    const closePanel = () => {
      infoPanel.classList.remove('visible');
      infoPanel.setAttribute('aria-hidden', 'true');
      if (this._formulaInfoReleaseFocus) {
        this._formulaInfoReleaseFocus();
        this._formulaInfoReleaseFocus = null;
      }
      infoPanel.removeEventListener('keydown', escapeHandler);
    };

    const escapeHandler = (e) => {
      if (e.key === 'Escape') {
        closePanel();
      }
    };

    closeBtn.onclick = closePanel;
    infoPanel.addEventListener('keydown', escapeHandler);

    this.updateFormulaInfoContext(infoPanel, fieldLabel);
    document.body.appendChild(infoPanel);
    this._formulaInfoReleaseFocus = trapFocus(infoPanel, {
      initialFocus: closeBtn,
    });
  }

  updateFormulaInfoContext(infoPanel, fieldLabel) {
    const context = infoPanel.querySelector('.formula-info-context');
    if (!context) return;
    if (fieldLabel) {
      context.textContent = `For ${fieldLabel}`;
      context.hidden = false;
      return;
    }
    context.textContent = '';
    context.hidden = true;
  }

  updateParam(key, value) {
    this.setProfileError(null);
    GlobalState.update({ [key]: value });
  }

  setProfileError(message) {
    this.profileError = String(message || '').trim();
    this.profileErrorStateVersion = this.profileError ? GlobalState.getVersion() : null;
    this.renderProfileErrorStrip();
  }

  renderProfileErrorStrip() {
    const existing = document.getElementById('freeform-profile-error');
    if (existing) existing.remove();
    if (!this.profileError || GlobalState.get().type !== 'FREEFORM') return;
    const section = document.getElementById('core-profile');
    if (!section) return;
    const strip = document.createElement('div');
    strip.id = 'freeform-profile-error';
    strip.className = 'freeform-profile-error';
    strip.setAttribute('role', 'alert');
    strip.textContent = this.profileError;
    section.appendChild(strip);
  }

  validateInputOnChange(input, key, def = null) {
    const validators = {
      outputName: validateOutputName,
      counter: validateCounter,
      jobLabel: validateJobLabel,
    };

    const validator = validators[key];
    let result = validator ? validator(input.value) : validateNumericValue(input, def);
    if (result.valid && !validator && getControlInputMode(def) === 'formula') {
      result = validateFormula(input.value);
    }
    if (!result.valid) {
      showInputError(input, result.error);
      return false;
    }

    hideInputError(input, true);
    return true;
  }
}

export function getControlInputMode(def) {
  if (!def) return 'text';
  if (def.type === 'select') return 'select';
  if (def.supportsFormula) return 'formula';
  if (def.type === 'number' || def.type === 'range') return 'number';
  return 'text';
}
