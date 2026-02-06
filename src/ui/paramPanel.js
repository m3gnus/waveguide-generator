import { PARAM_SCHEMA } from '../config/schema.js';
import { GlobalState } from '../state.js';
import { validateParams } from '../config/validator.js';

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
        if (this.simulationContainer) {
            this.simulationContainer.innerHTML = '';
        }
        this.controlIdCounter = 0;
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
            const section = document.createElement('div');
            section.className = 'section';
            const header = document.createElement('div');
            header.className = 'section-header';
            const title = document.createElement('h3');
            title.textContent = `${type} PARAMETERS`;
            const infoBtn = document.createElement('button');
            infoBtn.className = 'formula-info-btn';
            infoBtn.textContent = 'ƒ';
            infoBtn.title = 'View available formulas and functions';
            infoBtn.setAttribute('aria-label', 'View available formulas and functions');
            infoBtn.onclick = (e) => {
                e.preventDefault();
                this.showFormulaInfo();
            };
            header.appendChild(title);
            header.appendChild(infoBtn);
            section.appendChild(header);
            for (const [key, def] of Object.entries(coreSchema)) {
                section.appendChild(this.createControlRow(key, def, state.params[key]));
            }
            this.container.appendChild(section);
        }

        // --- Morphing (for OSSE) ---
        // Architecture ref says: "Improve morphing... OSSE model".
        if (type === 'OSSE') {
            const morphSection = this.createDetailsSection('Morphing & Corner', 'osse-morph-details');
            const morphSchema = PARAM_SCHEMA.MORPH;
            for (const [key, def] of Object.entries(morphSchema)) {
                morphSection.appendChild(this.createControlRow(key, def, state.params[key]));
            }
            this.container.appendChild(morphSection);
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

        // --- Enclosure & Wall (available for both OSSE and R-OSSE) ---
        const meshSchema = PARAM_SCHEMA.MESH || {};
        const enclosureSection = this.createDetailsSection('Enclosure', 'enclosure-details');

        // Wall thickness and rear shape (moved from Simulation → Geometry tab)
        const geomMeshKeys = ['wallThickness', 'rearShape'];
        geomMeshKeys.forEach((key) => {
            const def = meshSchema[key];
            if (def) {
                enclosureSection.appendChild(this.createControlRow(key, def, state.params[key]));
            }
        });

        // All enclosure parameters (now visible for both OSSE and R-OSSE)
        const encSchema = PARAM_SCHEMA.ENCLOSURE;
        if (encSchema) {
            for (const [key, def] of Object.entries(encSchema)) {
                enclosureSection.appendChild(this.createControlRow(key, def, state.params[key]));
            }
        }

        // Vertical offset (geometric, not mesh-density)
        const vertOffsetDef = meshSchema.verticalOffset;
        if (vertOffsetDef) {
            enclosureSection.appendChild(this.createControlRow('verticalOffset', vertOffsetDef, state.params.verticalOffset));
        }

        this.container.appendChild(enclosureSection);

        // --- Geometry Advanced (at bottom of Geometry tab) ---
        const geomSection = this.createDetailsSection('Geometry Advanced', 'geometry-advanced-details');
        const geomSchema = PARAM_SCHEMA.GEOMETRY;
        for (const [key, def] of Object.entries(geomSchema)) {
            geomSection.appendChild(this.createControlRow(key, def, state.params[key]));
        }
        this.container.appendChild(geomSection);

        // --- Output (Advanced) ---
        const outputSchema = PARAM_SCHEMA.OUTPUT;
        if (outputSchema) {
            const outputSection = this.createDetailsSection('Output Settings', 'output-details');
            for (const [key, def] of Object.entries(outputSchema)) {
                outputSection.appendChild(this.createControlRow(key, def, state.params[key]));
            }
            this.container.appendChild(outputSection);
        }

        // --- Simulation Tab ---
        if (this.simulationContainer) {
            const sourceSection = this.createDetailsSection('Source', 'source-details');
            for (const [key, def] of Object.entries(PARAM_SCHEMA.SOURCE)) {
                sourceSection.appendChild(this.createControlRow(key, def, state.params[key]));
            }
            this.simulationContainer.appendChild(sourceSection);

            // Mesh density section (simulation-only parameters)
            const combinedMeshSection = this.createDetailsSection('Mesh Density', 'mesh-details');

            // Params moved to Geometry tab — skip here
            const geomTabKeys = new Set(['wallThickness', 'rearShape', 'verticalOffset']);

            // Mesh density parameters (affect tessellation, not geometry shape)
            const meshDensityOrder = [
                'angularSegments',
                'lengthSegments',
                'cornerSegments',
                'quadrants',
                'throatSegments',
                'throatResolution',
                'mouthResolution',
                'rearResolution',
                'zMapPoints',
                'subdomainSlices',
                'interfaceOffset',
                'interfaceDraw'
            ];

            meshDensityOrder.forEach((key) => {
                if (geomTabKeys.has(key)) return;
                const def = meshSchema[key];
                if (def) {
                    combinedMeshSection.appendChild(this.createControlRow(key, def, state.params[key]));
                }
            });

            this.simulationContainer.appendChild(combinedMeshSection);
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

    createControlRow(key, def, currentValue) {
        const row = document.createElement('div');
        row.className = 'input-row';

        const label = document.createElement('label');
        const controlId = `param-${key}-${this.controlIdCounter++}`;
        label.textContent = def.label;
        if (def.unit) label.textContent += ` (${def.unit})`;
        label.htmlFor = controlId;

        // Add tooltip if available
        if (def.tooltip) {
            label.title = def.tooltip;
            label.style.cursor = 'help';
            label.style.borderBottom = '1px dotted #666';
        }

        row.appendChild(label);

        if (def.type === 'range' || def.type === 'number' || def.type === 'expression') {
            // All numeric/expression fields use auto-resizing text inputs that accept formulas
            const wrapper = document.createElement('div');
            wrapper.className = 'formula-input-wrapper';

            const input = document.createElement('input');
            input.type = 'text';
            input.id = controlId;
            input.value = currentValue;
            input.className = 'formula-input';
            input.placeholder = def.type === 'expression' ? 'e.g., 45 + 10*cos(p)' : 'number or formula';

            // Add min/max hint for range types
            if (def.type === 'range' && def.min !== undefined && def.max !== undefined) {
                input.title = `Range: ${def.min} to ${def.max}`;
            }

            // Auto-resize input based on content
            const autoResize = () => {
                // Create hidden span to measure text width
                const span = document.createElement('span');
                span.style.visibility = 'hidden';
                span.style.position = 'absolute';
                span.style.whiteSpace = 'pre';
                span.style.font = window.getComputedStyle(input).font;
                span.textContent = input.value || input.placeholder;
                document.body.appendChild(span);
                const width = Math.max(60, Math.min(200, span.offsetWidth + 16));
                document.body.removeChild(span);
                input.style.width = width + 'px';
            };

            input.addEventListener('input', autoResize);
            // Initial resize
            setTimeout(autoResize, 0);

            input.onchange = (e) => {
                const value = e.target.value.trim();
                // Try to parse as number first, otherwise treat as expression
                const numVal = parseFloat(value);
                if (!isNaN(numVal) && String(numVal) === value) {
                    this.updateParam(key, numVal);
                } else {
                    this.updateParam(key, value);
                }
            };

            input.oninput = (e) => {
                autoResize();
                // Visual validation feedback
                this.validateFormulaInput(input);
            };

            wrapper.appendChild(input);

            row.appendChild(wrapper);
        } else if (def.type === 'select') {
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

    validateFormulaInput(input) {
        const value = input.value.trim();
        // Check if it's a valid number
        if (!isNaN(parseFloat(value)) && isFinite(value)) {
            input.classList.remove('invalid');
            input.classList.add('valid');
            return true;
        }
        // Check for basic expression syntax (contains formula-like characters)
        if (/^[\d\s\+\-\*\/\^\(\)\.\,a-zA-Z_]+$/.test(value)) {
            input.classList.remove('invalid');
            input.classList.add('valid');
            return true;
        }
        if (value === '') {
            input.classList.remove('valid', 'invalid');
            return false;
        }
        input.classList.remove('valid');
        input.classList.add('invalid');
        return false;
    }

    showFormulaInfo() {
        // Check if info panel already exists
        let infoPanel = document.getElementById('formula-info-panel');
        if (infoPanel) {
            infoPanel.classList.toggle('visible');
            return;
        }

        // Create the info panel
        infoPanel = document.createElement('div');
        infoPanel.id = 'formula-info-panel';
        infoPanel.className = 'formula-info-panel visible';

        const header = document.createElement('div');
        header.className = 'formula-info-header';
        header.innerHTML = `
            <h4>Formula Reference</h4>
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

        document.body.appendChild(infoPanel);
    }

    updateParam(key, value) {
        GlobalState.update({ [key]: value });
    }
}
