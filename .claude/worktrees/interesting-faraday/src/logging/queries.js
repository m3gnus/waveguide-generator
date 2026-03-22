import { addLogEntry, getLogsStore, getSubscribers } from './store.js';

export function getLogs(filter = {}) {
  let result = [...getLogsStore()];

  if (filter.agent) {
    result = result.filter((log) => log.agent === filter.agent);
  }

  if (filter.category) {
    result = result.filter((log) => log.category === filter.category);
  }

  if (filter.event) {
    result = result.filter((log) => log.event.includes(filter.event));
  }

  if (filter.since) {
    const since = new Date(filter.since);
    result = result.filter((log) => log.timestamp >= since);
  }

  if (filter.until) {
    const until = new Date(filter.until);
    result = result.filter((log) => log.timestamp <= until);
  }

  if (filter.sessionId) {
    result = result.filter((log) => log.sessionId === filter.sessionId);
  }

  if (filter.limit) {
    result = result.slice(-filter.limit);
  }

  return result;
}

export function getRecentLogs(count = 20) {
  return getLogsStore().slice(-count);
}

export function getAgentLogs(agent, limit = 50) {
  return getLogs({ agent, limit });
}

export function getSummary() {
  const logs = getLogsStore();
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
  summary.recentActivity = logs.filter((log) => log.timestamp >= fiveMinutesAgo);

  return summary;
}

export function subscribe(callback) {
  const subscribers = getSubscribers();
  subscribers.push(callback);
  return () => {
    const index = subscribers.indexOf(callback);
    if (index > -1) {
      subscribers.splice(index, 1);
    }
  };
}

export function clearLogs() {
  const logs = getLogsStore();
  logs.length = 0;
  addLogEntry({
    event: 'logs:cleared',
    category: 'system',
    data: {}
  });
}

export function exportLogs(filter = {}) {
  const logsToExport = getLogs(filter);
  return JSON.stringify(logsToExport, null, 2);
}

export function formatLogEntry(log) {
  const time = log.timestamp.toISOString().slice(11, 23);
  return `[${time}] [${log.agent}] ${log.event} - ${JSON.stringify(log.data).slice(0, 100)}`;
}

export function printLogs(count = 10) {
  const recent = getRecentLogs(count);
  console.group('Change Log');
  recent.forEach((log) => console.log(formatLogEntry(log)));
  console.groupEnd();
}
