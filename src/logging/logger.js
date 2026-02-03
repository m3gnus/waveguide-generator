import { addLogEntry } from './store.js';
import { categorizeEvent, sanitizeData } from './utils.js';

export function logEvent(eventName, data, context = {}) {
  return addLogEntry({
    event: eventName,
    category: categorizeEvent(eventName),
    data: sanitizeData(data),
    ...context
  });
}
