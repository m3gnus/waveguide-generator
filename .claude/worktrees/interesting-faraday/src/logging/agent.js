import { addLogEntry, getCurrentAgent, getCurrentSessionId, setCurrentAgent, setCurrentSessionId } from './store.js';
import { generateId } from './utils.js';

export function setAgent(agent, metadata = {}) {
  const previousAgent = getCurrentAgent();
  setCurrentAgent(agent);

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

export function getAgent() {
  return getCurrentAgent();
}

export function startSession(description = '') {
  const newSessionId = generateId();
  setCurrentSessionId(newSessionId);

  addLogEntry({
    event: 'session:started',
    category: 'system',
    data: { description }
  });

  return newSessionId;
}

export function getSessionId() {
  return getCurrentSessionId();
}
