import { PARAM_SCHEMA } from '../../config/schema.js';

const VIEWPORT_CACHE_SCHEMA_GROUPS = ['GEOMETRY', 'MORPH', 'MESH', 'ENCLOSURE', 'SOURCE'];
const VIEWPORT_CACHE_PARAM_KEYS = new Set();
for (const group of VIEWPORT_CACHE_SCHEMA_GROUPS) {
  for (const key of Object.keys(PARAM_SCHEMA[group] || {})) {
    VIEWPORT_CACHE_PARAM_KEYS.add(key);
  }
}
VIEWPORT_CACHE_PARAM_KEYS.add('simType');

export function getViewportStateCacheKey(state = {}) {
  const type = state.type || '';
  const params = state.params || {};
  const modelKeys = Object.keys(PARAM_SCHEMA[type] || {});
  const keyParts = [`type:${type}`];

  for (const key of [...modelKeys, ...VIEWPORT_CACHE_PARAM_KEYS].sort()) {
    keyParts.push(`${key}:${JSON.stringify(params[key])}`);
  }
  return keyParts.join('|');
}

export function isViewportCacheCurrent(cache, state = {}, rejectedStateKey = null) {
  const stateKey = getViewportStateCacheKey(state);
  return cache?.stateKey === stateKey && rejectedStateKey !== stateKey;
}
