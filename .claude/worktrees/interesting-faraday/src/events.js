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

        // Notify listeners. Isolate callback failures so one bad listener does not
        // prevent other listeners from receiving the same event.
        const listeners = this.listeners[event];
        if (Array.isArray(listeners)) {
            for (const cb of [...listeners]) {
                try {
                    cb(processedData);
                } catch (e) {
                    console.warn(`Listener error for event "${event}":`, e);
                }
            }
        }

        // Also notify wildcard listeners with the same isolation behavior.
        const wildcardListeners = this.listeners['*'];
        if (Array.isArray(wildcardListeners)) {
            for (const cb of [...wildcardListeners]) {
                try {
                    cb({ event, data: processedData });
                } catch (e) {
                    console.warn(`Wildcard listener error for event "${event}":`, e);
                }
            }
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
