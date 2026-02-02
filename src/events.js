import { logEvent as logToChangeLog } from './logging/index.js';

export class EventBus {
    constructor() {
        this.listeners = {};
        this.loggingEnabled = true;
        this.middleware = [];
    }

    on(event, callback) {
        if (!this.listeners[event]) {
            this.listeners[event] = [];
        }
        this.listeners[event].push(callback);
    }

    off(event, callback) {
        if (!this.listeners[event]) return;
        this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
    }

    emit(event, data, context = {}) {
        // Log all events to the change log
        if (this.loggingEnabled) {
            try {
                logToChangeLog(event, data, context);
            } catch (e) {
                console.warn('Event logging failed:', e);
            }
        }

        // Run middleware
        let processedData = data;
        for (const mw of this.middleware) {
            try {
                const result = mw(event, processedData, context);
                if (result !== undefined) {
                    processedData = result;
                }
            } catch (e) {
                console.warn('Middleware error:', e);
            }
        }

        // Notify listeners
        if (this.listeners[event]) {
            this.listeners[event].forEach(cb => cb(processedData));
        }

        // Also notify wildcard listeners
        if (this.listeners['*']) {
            this.listeners['*'].forEach(cb => cb({ event, data: processedData }));
        }
    }

    /**
     * Add middleware to process events before they are dispatched
     * @param {Function} fn - Middleware function (event, data, context) => data
     */
    use(fn) {
        this.middleware.push(fn);
    }

    /**
     * Enable or disable logging
     * @param {boolean} enabled
     */
    setLogging(enabled) {
        this.loggingEnabled = enabled;
    }
}

export const AppEvents = new EventBus();
