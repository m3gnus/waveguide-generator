import { generateId } from './utils.js';

// Current agent context - can be set by agents when they start operations
let currentAgent = 'user';

// Session ID for grouping related operations
let sessionId = generateId();

// Log storage
const logs = [];
const maxLogSize = 1000;

// Subscribers for log events
const subscribers = [];

export function getCurrentAgent() {
  return currentAgent;
}

export function setCurrentAgent(agent) {
  currentAgent = agent;
}

export function getCurrentSessionId() {
  return sessionId;
}

export function setCurrentSessionId(id) {
  sessionId = id;
}

export function getLogsStore() {
  return logs;
}

export function getSubscribers() {
  return subscribers;
}

export function addLogEntry(entry) {
  const logEntry = {
    id: generateId(),
    timestamp: new Date(),
    agent: currentAgent,
    sessionId,
    ...entry
  };

  logs.push(logEntry);

  // Trim old logs if exceeding max size
  if (logs.length > maxLogSize) {
    logs.shift();
  }

  // Notify subscribers
  subscribers.forEach((cb) => {
    try {
      cb(logEntry);
    } catch (e) {
      console.warn('Log subscriber error:', e);
    }
  });

  return logEntry;
}
