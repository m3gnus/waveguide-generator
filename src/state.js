import { AppEvents } from './events.js';
import { getDefaults } from './config/defaults.js';
import { PARAM_SCHEMA } from './config/schema.js';
import { debugWarn } from './logging/debug.js';

const STORAGE_KEY = 'ath_state';
const SHARED_SCHEMA_GROUPS = new Set([
  'GEOMETRY',
  'MORPH',
  'MESH',
  'SOURCE',
  'SIMULATION',
  'ENCLOSURE',
]);
const SUPPORTED_MODEL_TYPES = new Set(
  Object.keys(PARAM_SCHEMA).filter((key) => !SHARED_SCHEMA_GROUPS.has(key))
);

function reportStorageWarning(message, error = null) {
  if (error) {
    debugWarn(message, error);
    return;
  }
  debugWarn(message);
}

function getStorage() {
  const storage = globalThis?.localStorage;
  if (!storage) return null;
  if (typeof storage.getItem !== 'function' || typeof storage.setItem !== 'function') {
    return null;
  }
  return storage;
}

function paramsEqual(a = {}, b = {}) {
  const aKeys = Object.keys(a || {});
  const bKeys = Object.keys(b || {});
  if (aKeys.length !== bKeys.length) return false;
  for (const key of aKeys) {
    if (!Object.hasOwn(b, key) || !Object.is(a[key], b[key])) {
      return false;
    }
  }
  return true;
}

function stateEqual(a, b) {
  if (!a || !b) return false;
  return a.type === b.type && paramsEqual(a.params || {}, b.params || {});
}

export function normalizePersistedState(candidate) {
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
    return null;
  }

  const modelType = typeof candidate.type === 'string' ? candidate.type : '';
  const params = candidate.params;
  if (!modelType || !params || typeof params !== 'object' || Array.isArray(params)) {
    return null;
  }
  if (!SUPPORTED_MODEL_TYPES.has(modelType)) {
    return null;
  }

  const defaults = getDefaults(modelType);
  if (!defaults || Object.keys(defaults).length === 0) {
    return null;
  }

  return {
    type: modelType,
    params: { ...defaults, ...params },
  };
}

export class AppState {
  constructor() {
    this.undoStack = [];
    this.redoStack = [];
    this.maxHistory = 50;

    // Initial state
    this.current = {
      type: 'R-OSSE',
      params: getDefaults('R-OSSE'),
    };

    this._stateVersion = 0;

    // Load from localStorage if available
    this.loadFromStorage();
  }

  get() {
    return this.current;
  }

  getVersion() {
    return this._stateVersion;
  }

  update(newParams, modelType = null) {
    const paramsPatch = newParams && typeof newParams === 'object' ? newParams : {};
    const nextState = {
      type: modelType || this.current.type,
      params: modelType
        ? { ...getDefaults(modelType), ...paramsPatch }
        : { ...this.current.params, ...paramsPatch },
    };
    if (stateEqual(this.current, nextState)) {
      return false;
    }

    // Create deep copy for history
    const previousState = JSON.parse(JSON.stringify(this.current));
    this.pushHistory(previousState);

    this.current = nextState;

    this._stateVersion++;
    this.saveToStorage();

    // Emit with context including previous state and agent
    AppEvents.emit('state:updated', this.current, {
      previousState,
      changedParams: paramsPatch,
      modelTypeChanged: modelType !== null,
      source: 'AppState.update',
    });
    return true;
  }

  // Replace entire state (e.g. loading config file)
  loadState(newState, source = 'config-load') {
    if (stateEqual(this.current, newState)) {
      return false;
    }

    const previousState = JSON.parse(JSON.stringify(this.current));
    this.pushHistory(previousState);

    this.current = JSON.parse(JSON.stringify(newState));
    this._stateVersion++;
    this.saveToStorage();
    AppEvents.emit('state:updated', this.current, {
      previousState,
      source: `AppState.loadState(${source})`,
      fullReplace: true,
    });
    return true;
  }

  pushHistory(state) {
    this.undoStack.push(state);
    if (this.undoStack.length > this.maxHistory) {
      this.undoStack.shift();
    }
    this.redoStack = []; // Clear redo on new action
  }

  undo() {
    if (this.undoStack.length === 0) return;

    const previousState = JSON.parse(JSON.stringify(this.current));
    this.redoStack.push(previousState);

    this.current = this.undoStack.pop();
    this._stateVersion++;
    this.saveToStorage();
    AppEvents.emit('state:updated', this.current, {
      previousState,
      source: 'AppState.undo',
      undoRedo: 'undo',
    });
  }

  redo() {
    if (this.redoStack.length === 0) return;

    const previousState = JSON.parse(JSON.stringify(this.current));
    this.undoStack.push(previousState);

    this.current = this.redoStack.pop();
    this._stateVersion++;
    this.saveToStorage();
    AppEvents.emit('state:updated', this.current, {
      previousState,
      source: 'AppState.redo',
      undoRedo: 'redo',
    });
  }

  saveToStorage() {
    const storage = getStorage();
    if (!storage) return;
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(this.current));
    } catch (e) {
      reportStorageWarning('LocalStorage save failed', e);
    }
  }

  loadFromStorage() {
    const storage = getStorage();
    if (!storage) return;
    try {
      const saved = storage.getItem(STORAGE_KEY);
      if (!saved) return;

      const parsed = JSON.parse(saved);
      const normalized = normalizePersistedState(parsed);
      if (normalized) {
        this.current = normalized;
      } else {
        reportStorageWarning('LocalStorage state schema mismatch; using defaults.');
        if (typeof storage.removeItem === 'function') {
          storage.removeItem(STORAGE_KEY);
        }
      }
    } catch (e) {
      reportStorageWarning('LocalStorage load failed', e);
    }
  }
}

export const GlobalState = new AppState();

/**
 * Module-level state for imported mesh mode.
 * Separate from AppState/GlobalState which is for parametric state.
 */
export const ImportedMeshState = {
  active: false,
  filename: null,
  vertices: null,
  indices: null,
  physicalTags: null,
  physicalNames: null,
};
