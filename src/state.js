import { AppEvents } from './events.js';
import { getDefaults } from './config/defaults.js';
import { getAgent } from './logging/index.js';

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
        try {
            localStorage.setItem('ath_state', JSON.stringify(this.current));
        } catch (e) {
            console.warn('LocalStorage save failed', e);
        }
    }

    loadFromStorage() {
        try {
            const saved = localStorage.getItem('ath_state');
            if (saved) {
                this.current = JSON.parse(saved);
            }
        } catch (e) {
            console.warn('LocalStorage load failed', e);
        }
    }
}

export const GlobalState = new AppState();
