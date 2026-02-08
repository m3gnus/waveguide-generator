/**
 * Change Logging module for the Waveguide Generator (WG).
 * Tracks all changes made by any AI agent or user interaction.
 * @module logging
 */

import { setAgent, getAgent, startSession, getSessionId } from './agent.js';
import { logEvent } from './logger.js';
import {
  getLogs,
  getRecentLogs,
  getAgentLogs,
  getSummary,
  subscribe,
  clearLogs,
  exportLogs,
  formatLogEntry,
  printLogs
} from './queries.js';

export {
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
