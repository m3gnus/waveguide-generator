import { PARAM_SCHEMA } from '../config/schema.js';
import { GlobalState } from '../state.js';
import { normalizeParamInput } from './paramInput.js';
import { appendSectionNote, createLabelRow } from './helpAffordance.js';
import { getParameterSections } from './parameterInventory.js';

// Available mathematical functions from ATH user guide (Appendix A)
const FORMULA_REFERENCE = {
    parameters: [
        { name: 'p', description: 'Azimuthal angle around waveguide axis (0 to 2π)' }
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
        { name: 'log1p(x)', description: 'log(1 + x) [currently has issues, use ln(1+x) instead]' },
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
        { name: 'rad(x)', description: 'Convert degrees to radians' }
    ],
    examples: [
        '45 + 10*cos(p)^2',
        '140 * (abs(cos(p)/1.6)^3 + abs(sin(p)/1)^4)^(-1/4.5)',
        '0.58 + 0.2*cos(p)^2',
        '48.5 - 5.6*cos(2*p)^5 - 31*sin(p)^12'
    ]
};

export class ParamPanel {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        if (!this.container) throw new Error(`Container ${containerId} not found`);
        this.simulationSettingsContainer = document.getElementById('simulation-settings-container');
        this.simulationContainer = document.getElementById('simulation-param-container');
        this.formulaInfoVisible = false;
        this.controlIdCounter = 0;
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
            includeOwners: ['paramPanel']
        });

        if (this.simulationSettingsContainer) {
            this.renderSections(this.simulationSettingsContainer, getParameterSections('simulation', type), state.params, {
                includeIds: ['frequency-sweep']
            });
        }

        if (this.simulationContainer) {
            this.renderSections(this.simulationContainer, getParameterSections('simulation', type), state.params, {
                includeIds: ['source-definition', 'preview-mesh', 'solve-export-mesh']
            });
        }
    }

    createSection(title) {
        const div = document.createElement('div');
        div.className = 'section';
        const h3 = document.createElement('h3');
        h3.textContent = title;
        div.appendChild(h3);
        return div;
    }

    createDetailsSection(summaryText, id) {
        const section = document.createElement('div');
        section.className = 'section';
        if (id) section.id = id;
        const h3 = document.createElement('h3');
        h3.textContent = summaryText;
        section.appendChild(h3);
        return section;
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
            appendSectionNote(sectionNode, document, section.description);
            (section.groups || []).forEach(({ group, keys }) => {
                const schemaGroup = PARAM_SCHEMA[group] || {};
                keys.forEach((key) => {
                    const def = schemaGroup[key];
                    if (def) {
                        sectionNode.appendChild(this.createControlRow(key, def, params[key]));
                    }
                });
            });
            target.appendChild(sectionNode);
        });
    }

    createModelTypeSection() {
        const typeSection = this.createSection('Model Type');
        const typeRow = document.createElement('div');
        typeRow.className = 'input-row';
        const typeSelect = document.createElement('select');
        typeSelect.id = 'model-type';

        const currentType = GlobalState.get().type;
        ['R-OSSE', 'OSSE'].forEach((type) => {
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

        const controlId = def.controlId || `param-${key}-${this.controlIdCounter++}`;
        const labelText = def.unit ? `${def.label} (${def.unit})` : def.label;
        const { row: labelRow } = createLabelRow(document, {
            labelText,
            htmlFor: controlId,
            helpText: def.tooltip || ''
        });
        row.appendChild(labelRow);

        const inputMode = getControlInputMode(def);
        const isFormulaField = inputMode === 'formula';

        if (inputMode === 'formula' || inputMode === 'number' || inputMode === 'text') {
            const wrapper = document.createElement('div');
            wrapper.className = isFormulaField ? 'formula-input-wrapper' : 'param-input-wrapper';

            const input = document.createElement('input');
            input.type = inputMode === 'number' ? 'number' : 'text';
            input.id = controlId;
            input.value = currentValue ?? '';
            input.setAttribute('data-param-key', key);
            if (isFormulaField) {
                input.className = 'formula-input';
                input.placeholder = 'e.g., 45 + 10*cos(p)';
                input.style.width = '100%';
                input.style.whiteSpace = 'pre-wrap';
                input.style.overflowY = 'auto';
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

            input.onchange = (e) => {
                this.updateParam(key, normalizeParamInput(e.target.value));
            };

            wrapper.appendChild(input);
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
                wrapper.appendChild(infoBtn);
            }

            row.appendChild(wrapper);
        } else if (inputMode === 'select') {
            const select = document.createElement('select');
            select.id = controlId;
            def.options.forEach(opt => {
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
            row.appendChild(select);
        }

        return row;
    }

    showFormulaInfo(fieldLabel = null) {
        // Check if info panel already exists
        let infoPanel = document.getElementById('formula-info-panel');
        if (infoPanel) {
            this.updateFormulaInfoContext(infoPanel, fieldLabel);
            infoPanel.classList.add('visible');
            return;
        }

        // Create the info panel
        infoPanel = document.createElement('div');
        infoPanel.id = 'formula-info-panel';
        infoPanel.className = 'formula-info-panel visible';

        const header = document.createElement('div');
        header.className = 'formula-info-header';
        header.innerHTML = `
            <div>
                <h4 class="formula-info-title">Formula Reference</h4>
                <p class="formula-info-context"></p>
            </div>
            <button class="formula-info-close" title="Close">&times;</button>
        `;
        infoPanel.appendChild(header);

        // Parameters section
        const paramsSection = document.createElement('div');
        paramsSection.className = 'formula-info-section';
        paramsSection.innerHTML = `<h5>Parameters</h5>`;
        const paramsList = document.createElement('div');
        paramsList.className = 'formula-list';
        FORMULA_REFERENCE.parameters.forEach(p => {
            const item = document.createElement('div');
            item.className = 'formula-item';
            item.innerHTML = `<code>${p.name}</code><span>${p.description}</span>`;
            paramsList.appendChild(item);
        });
        paramsSection.appendChild(paramsList);
        infoPanel.appendChild(paramsSection);

        // Functions section
        const funcsSection = document.createElement('div');
        funcsSection.className = 'formula-info-section';
        funcsSection.innerHTML = `<h5>Functions</h5>`;
        const funcsList = document.createElement('div');
        funcsList.className = 'formula-list scrollable';
        FORMULA_REFERENCE.functions.forEach(f => {
            const item = document.createElement('div');
            item.className = 'formula-item';
            item.innerHTML = `<code>${f.name}</code><span>${f.description}</span>`;
            funcsList.appendChild(item);
        });
        funcsSection.appendChild(funcsList);
        infoPanel.appendChild(funcsSection);

        // Examples section
        const examplesSection = document.createElement('div');
        examplesSection.className = 'formula-info-section';
        examplesSection.innerHTML = `<h5>Examples</h5>`;
        const examplesList = document.createElement('div');
        examplesList.className = 'formula-examples';
        FORMULA_REFERENCE.examples.forEach(ex => {
            const item = document.createElement('code');
            item.className = 'formula-example';
            item.textContent = ex;
            examplesList.appendChild(item);
        });
        examplesSection.appendChild(examplesList);
        infoPanel.appendChild(examplesSection);

        // Add close handler
        header.querySelector('.formula-info-close').onclick = () => {
            infoPanel.classList.remove('visible');
        };

        this.updateFormulaInfoContext(infoPanel, fieldLabel);
        document.body.appendChild(infoPanel);
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
        GlobalState.update({ [key]: value });
    }
}

export function getControlInputMode(def) {
    if (!def) return 'text';
    if (def.type === 'select') return 'select';
    if (def.supportsFormula) return 'formula';
    if (def.type === 'number' || def.type === 'range') return 'number';
    return 'text';
}
