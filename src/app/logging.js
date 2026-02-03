import ChangeLog, {
  setAgent,
  startSession,
  getLogs,
  getRecentLogs,
  getSummary,
  subscribe as subscribeToLogs,
  exportLogs,
  printLogs
} from '../logging/index.js';

export function initializeLogging() {
  // Start a new session
  startSession('app-init');

  // Set initial agent to 'user' (default)
  setAgent('user', { source: 'app-init' });

  // Expose logging API globally for AI agents and debugging
  window.ChangeLog = ChangeLog;
  window.setAgent = setAgent;
  window.startSession = startSession;
  window.getLogs = getLogs;
  window.getRecentLogs = getRecentLogs;
  window.getLogSummary = getSummary;
  window.exportLogs = exportLogs;
  window.printLogs = printLogs;

  // Subscribe to log events for console output (optional - for debugging)
  subscribeToLogs((entry) => {
    // Skip system events for cleaner output
    if (entry.category === 'system') return;

    const time = entry.timestamp.toISOString().slice(11, 19);
    console.log(
      `%c[${time}] %c[${entry.agent}] %c${entry.event}`,
      'color: gray',
      'color: cyan',
      'color: white',
      entry.data
    );
  });

  console.log('%c[ChangeLog] Logging system initialized', 'color: green; font-weight: bold');
  console.log('%c[ChangeLog] Use window.printLogs() to view recent changes', 'color: green');
  console.log('%c[ChangeLog] Use window.setAgent("agent-name") to identify the current agent', 'color: green');
}
