
import { PARAM_SCHEMA } from '../config/schema.js';
import { GlobalState } from '../state.js';
import { validateParams } from '../config/validator.js';

export class ParamPanel {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        if (!this.container) throw new Error(`Container ${containerId} not found`);
        this.init();
    }

    init() {
        // Subscribe to state changes to update UI
        // But we also need to build the initial UI
        this.renderParams();
    }

    renderParams() {
        const state = GlobalState.get();
        const type = state.type;
        const params = state.params;

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
        const state = GlobalState.get();
        const type = state.type;

        // --- Model Type Selector ---
        const typeSection = this.createSection('MODEL TYPE');
        const typeRow = document.createElement('div');
        typeRow.className = 'input-row';
        const typeSelect = document.createElement('select');
        typeSelect.id = 'model-type'; // Keep ID for compatibility if needed, but not strictly necessary

        ['R-OSSE', 'OSSE'].forEach(t => {
            const opt = document.createElement('option');
            opt.value = t;
            opt.textContent = t;
            if (t === type) opt.selected = true;
            typeSelect.appendChild(opt);
        });

        typeSelect.onchange = (e) => {
            GlobalState.update({}, e.target.value);
        };

        typeRow.appendChild(typeSelect);
        typeSection.appendChild(typeRow);
        this.container.appendChild(typeSection);

        // --- Model Specific Params ---
        const coreSchema = PARAM_SCHEMA[type];
        if (coreSchema) {
            const section = this.createSection(`${type} PARAMETERS`);
            for (const [key, def] of Object.entries(coreSchema)) {
                section.appendChild(this.createControlRow(key, def, state.params[key]));
            }
            this.container.appendChild(section);
        }

        // --- Morphing (only for OSSE in original, but schema has it separate) ---
        // Original logic: Morph/Enclosure only shown for OSSE.
        // We can respect that or make it available for all.
        // Architecture ref says: "Improve morphing... OSSE model".
        if (type === 'OSSE') {
            const morphSection = this.createDetailsSection('Morphing & Corner', 'osse-morph-details');
            const morphSchema = PARAM_SCHEMA.MORPH;
            for (const [key, def] of Object.entries(morphSchema)) {
                morphSection.appendChild(this.createControlRow(key, def, state.params[key]));
            }
            this.container.appendChild(morphSection);

            const encSection = this.createDetailsSection('Mesh Enclosure', 'osse-enc-details');
            const encSchema = PARAM_SCHEMA.ENCLOSURE;
            if (encSchema) {
                for (const [key, def] of Object.entries(encSchema)) {
                    // Combine space L/T/R/B into one row? For now, list them.
                    encSection.appendChild(this.createControlRow(key, def, state.params[key]));
                }
                this.container.appendChild(encSection);
            }
        }

        // --- Rollback (R-OSSE primarily, but available for both) ---
        if (type === 'R-OSSE') {
            const rollSection = this.createDetailsSection('Mouth Rollback', 'rollback-details');
            const rollSchema = PARAM_SCHEMA.ROLLBACK;
            for (const [key, def] of Object.entries(rollSchema)) {
                rollSection.appendChild(this.createControlRow(key, def, state.params[key]));
            }
            this.container.appendChild(rollSection);
        }

        // --- Mesh Settings (Shared) ---
        const meshSection = this.createDetailsSection('Mesh & Rear', 'mesh-details');
        for (const [key, def] of Object.entries(PARAM_SCHEMA.MESH)) {
            meshSection.appendChild(this.createControlRow(key, def, state.params[key]));
        }
        this.container.appendChild(meshSection);

        // --- Source & ABEC ---
        const sourceSection = this.createDetailsSection('Source & ABEC', 'source-details');
        for (const [key, def] of Object.entries(PARAM_SCHEMA.SOURCE)) {
            sourceSection.appendChild(this.createControlRow(key, def, state.params[key]));
        }
        for (const [key, def] of Object.entries(PARAM_SCHEMA.ABEC)) {
            sourceSection.appendChild(this.createControlRow(key, def, state.params[key]));
        }
        this.container.appendChild(sourceSection);
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
        const details = document.createElement('details');
        if (id) details.id = id;
        const summary = document.createElement('summary');
        summary.textContent = summaryText;
        details.appendChild(summary);
        return details;
    }

    createControlRow(key, def, currentValue) {
        const row = document.createElement('div');
        row.className = 'input-row';

        const label = document.createElement('label');
        label.textContent = def.label;
        if (def.unit) label.textContent += ` (${def.unit})`;

        // Add tooltip if available
        if (def.tooltip) {
            label.title = def.tooltip;
            label.style.cursor = 'help';
            label.style.borderBottom = '1px dotted #666';
        }

        row.appendChild(label);

        if (def.type === 'range' || def.type === 'number') {
            const input = document.createElement('input');
            input.type = 'number';
            input.value = currentValue;
            input.step = def.step || (def.type === 'number' ? 'any' : 1);
            if (def.type === 'range') {
                input.style.width = '60px';
            }

            input.onchange = (e) => {
                const val = parseFloat(e.target.value);
                this.updateParam(key, val);
            };
            row.appendChild(input);

            if (def.type === 'range') {
                const slider = document.createElement('input');
                slider.type = 'range';
                slider.min = def.min;
                slider.max = def.max;
                slider.step = def.step;
                slider.value = currentValue;

                // Sync
                slider.oninput = (e) => {
                    input.value = e.target.value;
                    const val = parseFloat(e.target.value);
                    this.updateParam(key, val);
                };
                input.oninput = (e) => {
                    slider.value = e.target.value;
                };

                row.appendChild(slider);
            }
        } else if (def.type === 'expression') {
            // Create wrapper for expression input with indicator
            const wrapper = document.createElement('div');
            wrapper.style.display = 'flex';
            wrapper.style.alignItems = 'center';
            wrapper.style.gap = '4px';
            wrapper.style.flex = '1';

            // Expression indicator badge
            const badge = document.createElement('span');
            badge.textContent = 'ƒx';
            badge.className = 'expression-badge';
            badge.title = 'This field accepts mathematical expressions (e.g., sin, cos, abs, ^)';

            const input = document.createElement('input');
            input.type = 'text';
            input.value = currentValue;
            input.className = 'expression-input';
            input.style.flex = '1';

            input.onchange = (e) => {
                this.updateParam(key, e.target.value);
            };

            // Add validation feedback
            input.oninput = (e) => {
                this.validateExpression(e.target.value, badge);
            };

            wrapper.appendChild(badge);
            wrapper.appendChild(input);
            row.appendChild(wrapper);
        } else if (def.type === 'select') {
            const select = document.createElement('select');
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

    validateExpression(expr, badge) {
        // Simple validation - just check for basic syntax
        try {
            // Try to detect obvious errors
            if (expr.includes('//') || expr.includes('/*')) {
                badge.style.background = '#f44';
                badge.title = 'Invalid expression';
                return false;
            }
            badge.style.background = '#4a9eff';
            badge.title = 'Expression syntax looks valid';
            return true;
        } catch (e) {
            badge.style.background = '#f44';
            badge.title = 'Invalid expression: ' + e.message;
            return false;
        }
    }

    updateParam(key, value) {
        GlobalState.update({ [key]: value });
    }
}
