import { PARAM_SCHEMA } from './schema.js';

function cloneDefault(value) {
  if (Array.isArray(value)) return value.map(cloneDefault);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneDefault(item)])
    );
  }
  return value;
}

export function getDefaults(modelType) {
  const defaults = {};

  // Core parameters for the specific model
  const core = PARAM_SCHEMA[modelType];
  if (core) {
    for (const [key, def] of Object.entries(core)) {
      defaults[key] = cloneDefault(def.default);
    }
  }

  // Shared groups
  const sharedGroups = ['GEOMETRY', 'MORPH', 'MESH', 'SOURCE', 'SIMULATION', 'ENCLOSURE'];
  for (const group of sharedGroups) {
    const groupSchema = PARAM_SCHEMA[group];
    if (groupSchema) {
      for (const [key, def] of Object.entries(groupSchema)) {
        defaults[key] = cloneDefault(def.default);
      }
    }
  }

  // Server-only profile families define their own cross-section shape. Default
  // them to no morph rather than the shared Rectangle target, which would
  // otherwise conflict with their native geometry.
  if (modelType === 'ICW' || modelType === 'FREEFORM') {
    defaults.morphTarget = 0;
  }
  if (modelType === 'FREEFORM') {
    defaults.angularSegments = 96;
    defaults.lengthSegments = 48;
  }

  return defaults;
}
