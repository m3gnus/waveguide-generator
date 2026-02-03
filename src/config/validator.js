import { PARAM_SCHEMA } from './schema.js';

const NUMERIC_PATTERN = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

function isNumericString(value) {
    if (typeof value !== 'string') return false;
    const trimmed = value.trim();
    if (!trimmed) return false;
    return NUMERIC_PATTERN.test(trimmed);
}

export function validateParams(params, modelType) {
    const issues = [];

    // Helper to validate a single field
    const validateField = (key, value, schemaDef, options = {}) => {
        if (!schemaDef) return; // Unknown param, skip or warn

        const allowExpression = options.allowExpression === true;

        // Number range validation
        if (schemaDef.type === 'range' || schemaDef.type === 'number') {
            const isExpressionValue = typeof value === 'function'
                || (typeof value === 'string' && !isNumericString(value));

            if (allowExpression && isExpressionValue) {
                if (typeof value === 'string' && value.trim() === '') {
                    issues.push({ param: key, message: 'Expression cannot be empty', severity: 'error' });
                }
                return;
            }

            const numVal = Number(value);
            if (!Number.isFinite(numVal)) {
                issues.push({ param: key, message: 'Must be a number', severity: 'error' });
                return;
            }
            if (schemaDef.min !== undefined && numVal < schemaDef.min) {
                issues.push({ param: key, message: `Value ${numVal} is below minimum ${schemaDef.min}`, severity: 'warning' });
            }
            if (schemaDef.max !== undefined && numVal > schemaDef.max) {
                issues.push({ param: key, message: `Value ${numVal} is above maximum ${schemaDef.max}`, severity: 'warning' });
            }
        }

        // Expression validation (basic check)
        if (schemaDef.type === 'expression') {
            if (typeof value === 'number') return;
            if (typeof value === 'function') return;
            if (typeof value !== 'string' || value.trim() === '') {
                issues.push({ param: key, message: 'Expression cannot be empty', severity: 'error' });
            }
        }
    };

    // 1. Validate Core Params
    const coreSchema = PARAM_SCHEMA[modelType];
    if (coreSchema) {
        for (const [key, def] of Object.entries(coreSchema)) {
            if (params[key] !== undefined) {
                validateField(key, params[key], def, { allowExpression: true });
            }
        }
    }

    // 2. Validate Shared Groups
    ['GEOMETRY', 'MORPH', 'MESH', 'ROLLBACK', 'ENCLOSURE', 'SOURCE', 'ABEC', 'OUTPUT'].forEach(group => {
        const groupSchema = PARAM_SCHEMA[group];
        for (const [key, def] of Object.entries(groupSchema)) {
            if (params[key] !== undefined) {
                validateField(key, params[key], def);
            }
        }
    });

    return {
        valid: issues.filter(i => i.severity === 'error').length === 0,
        issues
    };
}
