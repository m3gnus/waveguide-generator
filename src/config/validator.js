import { PARAM_SCHEMA } from './schema.js';

export function validateParams(params, modelType) {
    const issues = [];

    // Helper to validate a single field
    const validateField = (key, value, schemaDef) => {
        if (!schemaDef) return; // Unknown param, skip or warn

        // Number range validation
        if (schemaDef.type === 'range' || schemaDef.type === 'number') {
            const numVal = parseFloat(value);
            if (isNaN(numVal)) {
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
                validateField(key, params[key], def);
            }
        }
    }

    // 2. Validate Shared Groups
    ['MORPH', 'MESH', 'SOURCE', 'ABEC'].forEach(group => {
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
