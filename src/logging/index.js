/**
 * Change Logging module for MWG - Mathematical Waveguide Generator.
 * Tracks all changes made by any AI agent or user interaction.
 * @module logging
 */

/**
 * Log entry structure
 * @typedef {Object} LogEntry
 * @property {string} id - Unique log entry ID
 * @property {Date} timestamp - When the change occurred
 * @property {string} agent - Agent identifier (e.g., 'user', 'claude', 'copilot', 'system')
 * @property {string} event - Event name that triggered the change
 * @property {string} category - Event category (state, geometry, workflow, export, simulation)
 * @property {Object} data - Event data/payload
 * @property {Object} [previousState] - State before the change (for state events)
 * @property {string} [source] - Source file/module that emitted the event
 */

// Current agent context - can be set by agents when they start operations
let currentAgent = 'user';

// Session ID for grouping related operations
let sessionId = generateId();

// Log storage
const logs = [];
const maxLogSize = 1000;

// Subscribers for log events
const subscribers = [];

/**
 * Generate a unique ID
 * @returns {string} Unique identifier
 */
function generateId() {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Categorize an event by its name
 * @param {string} eventName - Name of the event
 * @returns {string} Category
 */
function categorizeEvent(eventName) {
  if (eventName.startsWith('state:')) return 'state';
  if (eventName.startsWith('geometry:')) return 'geometry';
  if (eventName.startsWith('workflow:')) return 'workflow';
  if (eventName.startsWith('export:')) return 'export';
  if (eventName.startsWith('simulation:')) return 'simulation';
  if (eventName.startsWith('optimization:')) return 'optimization';
  if (eventName.startsWith('validation:')) return 'validation';
  return 'general';
}

/**
 * Set the current agent context
 * @param {string} agent - Agent identifier
 * @param {Object} [metadata] - Optional metadata about the agent
 */
export function setAgent(agent, metadata = {}) {
  const previousAgent = currentAgent;
  currentAgent = agent;

  // Log the agent switch
  addLogEntry({
    event: 'agent:switched',
    category: 'system',
    data: {
      from: previousAgent,
      to: agent,
      metadata
    }
  });
}

/**
 * Get the current agent
 * @returns {string} Current agent identifier
 */
export function getAgent() {
  return currentAgent;
}

/**
 * Start a new session (groups related operations)
 * @param {string} [description] - Optional session description
 * @returns {string} New session ID
 */
export function startSession(description = '') {
  sessionId = generateId();

  addLogEntry({
    event: 'session:started',
    category: 'system',
    data: { description }
  });

  return sessionId;
}

/**
 * Get current session ID
 * @returns {string} Current session ID
 */
export function getSessionId() {
  return sessionId;
}

/**
 * Add a log entry
 * @param {Object} entry - Partial log entry
 * @returns {LogEntry} Complete log entry
 */
function addLogEntry(entry) {
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
  subscribers.forEach(cb => {
    try {
      cb(logEntry);
    } catch (e) {
      console.warn('Log subscriber error:', e);
    }
  });

  return logEntry;
}

/**
 * Log an event (called by the EventBus middleware)
 * @param {string} eventName - Event name
 * @param {Object} data - Event data
 * @param {Object} [context] - Additional context (previousState, source, etc.)
 * @returns {LogEntry} The created log entry
 */
export function logEvent(eventName, data, context = {}) {
  return addLogEntry({
    event: eventName,
    category: categorizeEvent(eventName),
    data: sanitizeData(data),
    ...context
  });
}

/**
 * Sanitize data for logging (remove circular refs, large arrays, etc.)
 * @param {*} data - Data to sanitize
 * @param {number} [depth=3] - Max recursion depth
 * @returns {*} Sanitized data
 */
function sanitizeData(data, depth = 3) {
  if (depth <= 0) return '[max depth]';
  if (data === null || data === undefined) return data;
  if (typeof data !== 'object') return data;

  // Handle arrays
  if (Array.isArray(data)) {
    if (data.length > 100) {
      return `[Array(${data.length})]`;
    }
    return data.slice(0, 20).map(item => sanitizeData(item, depth - 1));
  }

  // Handle typed arrays (e.g., Float32Array from Three.js)
  if (ArrayBuffer.isView(data)) {
    return `[${data.constructor.name}(${data.length})]`;
  }

  // Handle plain objects
  const result = {};
  const keys = Object.keys(data);

  for (const key of keys.slice(0, 50)) {
    result[key] = sanitizeData(data[key], depth - 1);
  }

  if (keys.length > 50) {
    result._truncated = `${keys.length - 50} more keys`;
  }

  return result;
}

/**
 * Get all logs
 * @param {Object} [filter] - Optional filter criteria
 * @param {string} [filter.agent] - Filter by agent
 * @param {string} [filter.category] - Filter by category
 * @param {string} [filter.event] - Filter by event name (partial match)
 * @param {Date} [filter.since] - Filter by timestamp (after)
 * @param {Date} [filter.until] - Filter by timestamp (before)
 * @param {string} [filter.sessionId] - Filter by session ID
 * @param {number} [filter.limit] - Limit number of results
 * @returns {Array<LogEntry>} Filtered log entries
 */
export function getLogs(filter = {}) {
  let result = [...logs];

  if (filter.agent) {
    result = result.filter(log => log.agent === filter.agent);
  }

  if (filter.category) {
    result = result.filter(log => log.category === filter.category);
  }

  if (filter.event) {
    result = result.filter(log => log.event.includes(filter.event));
  }

  if (filter.since) {
    const since = new Date(filter.since);
    result = result.filter(log => log.timestamp >= since);
  }

  if (filter.until) {
    const until = new Date(filter.until);
    result = result.filter(log => log.timestamp <= until);
  }

  if (filter.sessionId) {
    result = result.filter(log => log.sessionId === filter.sessionId);
  }

  if (filter.limit) {
    result = result.slice(-filter.limit);
  }

  return result;
}

/**
 * Get recent logs (convenience method)
 * @param {number} [count=20] - Number of recent logs to get
 * @returns {Array<LogEntry>} Recent log entries
 */
export function getRecentLogs(count = 20) {
  return logs.slice(-count);
}

/**
 * Get logs for a specific agent
 * @param {string} agent - Agent identifier
 * @param {number} [limit=50] - Max number of logs
 * @returns {Array<LogEntry>} Log entries for the agent
 */
export function getAgentLogs(agent, limit = 50) {
  return getLogs({ agent, limit });
}

/**
 * Get a summary of changes by agent
 * @returns {Object} Summary with counts per agent and category
 */
export function getSummary() {
  const summary = {
    totalLogs: logs.length,
    byAgent: {},
    byCategory: {},
    recentActivity: []
  };

  for (const log of logs) {
    // Count by agent
    summary.byAgent[log.agent] = (summary.byAgent[log.agent] || 0) + 1;

    // Count by category
    summary.byCategory[log.category] = (summary.byCategory[log.category] || 0) + 1;
  }

  // Recent activity (last 5 minutes)
  const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000);
  summary.recentActivity = logs.filter(log => log.timestamp >= fiveMinutesAgo);

  return summary;
}

/**
 * Subscribe to log events
 * @param {Function} callback - Called with each new log entry
 * @returns {Function} Unsubscribe function
 */
export function subscribe(callback) {
  subscribers.push(callback);
  return () => {
    const index = subscribers.indexOf(callback);
    if (index > -1) {
      subscribers.splice(index, 1);
    }
  };
}

/**
 * Clear all logs
 */
export function clearLogs() {
  logs.length = 0;
  addLogEntry({
    event: 'logs:cleared',
    category: 'system',
    data: {}
  });
}

/**
 * Export logs as JSON
 * @param {Object} [filter] - Optional filter (same as getLogs)
 * @returns {string} JSON string of logs
 */
export function exportLogs(filter = {}) {
  const logsToExport = getLogs(filter);
  return JSON.stringify(logsToExport, null, 2);
}

/**
 * Format a log entry for console display
 * @param {LogEntry} log - Log entry
 * @returns {string} Formatted string
 */
export function formatLogEntry(log) {
  const time = log.timestamp.toISOString().slice(11, 23);
  return `[${time}] [${log.agent}] ${log.event} - ${JSON.stringify(log.data).slice(0, 100)}`;
}

/**
 * Print recent logs to console (for debugging)
 * @param {number} [count=10] - Number of logs to print
 */
export function printLogs(count = 10) {
  const recent = getRecentLogs(count);
  console.group('Change Log');
  recent.forEach(log => console.log(formatLogEntry(log)));
  console.groupEnd();
}

// Default export with all functions
export default {
  setAgent,
  getAgent,
  startSession,
  getSessionId,
  logEvent,
  getLogs,
  getRecentLogs,
  getAgentLogs,
  getSummary,
  subscribe,
  clearLogs,
  exportLogs,
  formatLogEntry,
  printLogs
};
