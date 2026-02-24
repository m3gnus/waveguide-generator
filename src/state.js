import { AppEvents } from './events.js';
import { getDefaults } from './config/defaults.js';
import { getAgent } from './logging/index.js';
import { PARAM_SCHEMA } from './config/schema.js';

const STORAGE_KEY = 'ath_state';
const SHARED_SCHEMA_GROUPS = new Set(['GEOMETRY', 'MORPH', 'MESH', 'SOURCE', 'SIMULATION', 'ENCLOSURE']);
const SUPPORTED_MODEL_TYPES = new Set(
    Object.keys(PARAM_SCHEMA).filter((key) => !SHARED_SCHEMA_GROUPS.has(key))
);

function getStorage() {
    const storage = globalThis?.localStorage;
    if (!storage) return null;
    if (typeof storage.getItem !== 'function' || typeof storage.setItem !== 'function') {
        return null;
    }
    return storage;
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
        params: { ...defaults, ...params }
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
            params: getDefaults('R-OSSE')
        };

        // Load from localStorage if available
        this.loadFromStorage();
    }

    get() {
        return this.current;
    }

    update(newParams, modelType = null) {
        // Create deep copy for history
        const previousState = JSON.parse(JSON.stringify(this.current));
        this.pushHistory(previousState);

        if (modelType) {
            this.current.type = modelType;
            // If switching types, merge new defaults with existing compatible params?
            // For now, simpler: getting defaults for new type and carrying over common ones implies
            // a sophisticated merge strategy.
            // Phase 1 strategy: If type changes, load defaults for that type + preserve shared params.
            const defaults = getDefaults(modelType);
            this.current.params = { ...defaults, ...newParams };
        } else {
            // Just updating params
            this.current.params = { ...this.current.params, ...newParams };
        }

        this.saveToStorage();

        // Emit with context including previous state and agent
        AppEvents.emit('state:updated', this.current, {
            previousState,
            changedParams: newParams,
            modelTypeChanged: modelType !== null,
            source: 'AppState.update'
        });
    }

    // Replace entire state (e.g. loading config file)
    loadState(newState, source = 'config-load') {
        const previousState = JSON.parse(JSON.stringify(this.current));
        this.pushHistory(previousState);

        this.current = JSON.parse(JSON.stringify(newState));
        this.saveToStorage();
        AppEvents.emit('state:updated', this.current, {
            previousState,
            source: `AppState.loadState(${source})`,
            fullReplace: true
        });
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
        this.saveToStorage();
        AppEvents.emit('state:updated', this.current, {
            previousState,
            source: 'AppState.undo',
            undoRedo: 'undo'
        });
    }

    redo() {
        if (this.redoStack.length === 0) return;

        const previousState = JSON.parse(JSON.stringify(this.current));
        this.undoStack.push(previousState);

        this.current = this.redoStack.pop();
        this.saveToStorage();
        AppEvents.emit('state:updated', this.current, {
            previousState,
            source: 'AppState.redo',
            undoRedo: 'redo'
        });
    }

    saveToStorage() {
        const storage = getStorage();
        if (!storage) return;
        try {
            storage.setItem(STORAGE_KEY, JSON.stringify(this.current));
        } catch (e) {
            console.warn('LocalStorage save failed', e);
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
                console.warn('LocalStorage state schema mismatch; using defaults.');
                if (typeof storage.removeItem === 'function') {
                    storage.removeItem(STORAGE_KEY);
                }
            }
        } catch (e) {
            console.warn('LocalStorage load failed', e);
        }
    }
}

export const GlobalState = new AppState();
