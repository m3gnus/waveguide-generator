import { PARAM_SCHEMA } from './schema.js';

export function getDefaults(modelType) {
    const defaults = {};

    // Core parameters for the specific model
    const core = PARAM_SCHEMA[modelType];
    if (core) {
        for (const [key, def] of Object.entries(core)) {
            defaults[key] = def.default;
        }
    }

    // Shared groups
    const sharedGroups = ['MORPH', 'MESH', 'SOURCE', 'ABEC', 'ENCLOSURE'];
    for (const group of sharedGroups) {
        const groupSchema = PARAM_SCHEMA[group];
        if (groupSchema) {
            for (const [key, def] of Object.entries(groupSchema)) {
                defaults[key] = def.default;
            }
        }
    }

    // Rollback defaults (R-OSSE only technically)
    defaults.rollback = false;
    defaults.rollbackStart = 0.5;
    defaults.rollbackAngle = 180;

    return defaults;
}
