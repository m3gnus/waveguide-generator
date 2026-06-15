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
  const sharedGroups = ['GEOMETRY', 'MORPH', 'MESH', 'SOURCE', 'SIMULATION', 'ENCLOSURE'];
  for (const group of sharedGroups) {
    const groupSchema = PARAM_SCHEMA[group];
    if (groupSchema) {
      for (const [key, def] of Object.entries(groupSchema)) {
        defaults[key] = def.default;
      }
    }
  }

  // ICW is phi-independent (a body of revolution), so its natural mouth is
  // already circular. Default it to no morph rather than the shared Rectangle
  // default, which would otherwise make a fresh ICW horn non-circular.
  if (modelType === 'ICW') {
    defaults.morphTarget = 0;
  }

  return defaults;
}
