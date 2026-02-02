# Change Logging Module - Agent Documentation

## Overview

This module provides centralized change tracking for all modifications made to the MWG application, regardless of which AI agent or user performs the operation.

## Why This Exists

- **Auditability**: Track all changes with timestamps and agent identification
- **Debugging**: Understand the sequence of events that led to a state
- **Collaboration**: Know which agent made which changes
- **Undo/Redo Context**: See what changed and why

## For AI Agents

### Setting Your Identity

**IMPORTANT**: At the start of any operation, identify yourself:

```javascript
// In browser console or via injected script
window.setAgent('claude', { task: 'fixing mesh generation' });

// Or with more metadata
window.setAgent('copilot', {
  task: 'adding rollback feature',
  version: '1.0',
  confidence: 0.9
});
```

Common agent identifiers:
- `'user'` - Human user (default)
- `'claude'` - Claude AI
- `'copilot'` - GitHub Copilot
- `'cursor'` - Cursor AI
- `'system'` - Automated system operations

### Starting a Session

Group related operations into a session:

```javascript
window.startSession('implement-new-feature');
// ... perform operations ...
// Session ID is automatically attached to all log entries
```

### Checking Logs

```javascript
// View recent logs
window.printLogs(10);

// Get logs as array
const logs = window.getRecentLogs(20);

// Get logs by agent
const myChanges = window.getLogs({ agent: 'claude' });

// Get summary
const summary = window.getLogSummary();
console.log(summary.byAgent); // { user: 5, claude: 12, system: 3 }

// Export for analysis
const json = window.exportLogs();
```

## Log Entry Structure

```javascript
{
  id: "1706812345678-abc123def",
  timestamp: Date,
  agent: "claude",
  sessionId: "1706812340000-xyz789",
  event: "state:updated",
  category: "state",  // state, geometry, workflow, export, simulation, etc.
  data: { /* event payload */ },
  previousState: { /* for state events */ },
  source: "AppState.update"
}
```

## Categories

Events are automatically categorized by prefix:
- `state:*` → `'state'`
- `geometry:*` → `'geometry'`
- `workflow:*` → `'workflow'`
- `export:*` → `'export'`
- `simulation:*` → `'simulation'`
- `optimization:*` → `'optimization'`
- `validation:*` → `'validation'`

## Integration with EventBus

All events emitted via `AppEvents.emit()` are automatically logged. You can pass additional context:

```javascript
import { AppEvents } from './events.js';

// Basic emission (logged automatically)
AppEvents.emit('geometry:updated', { mesh: newMesh });

// With context (includes previousState, source info)
AppEvents.emit('state:updated', newState, {
  previousState: oldState,
  source: 'MyModule.doSomething',
  changedParams: { a0: 15, r0: 25 }
});
```

## Subscribing to Changes

```javascript
import { subscribe } from './logging/index.js';

// Get notified of every change
const unsubscribe = subscribe((entry) => {
  if (entry.agent !== 'user') {
    console.log(`Agent ${entry.agent} made a change: ${entry.event}`);
  }
});

// Later, stop listening
unsubscribe();
```

## Best Practices for Agents

1. **Always identify yourself** at the start of operations
2. **Use sessions** for related operations
3. **Check logs before making changes** to understand context
4. **Don't disable logging** unless explicitly requested by user
5. **Include meaningful context** when emitting events

## Public API (window.*)

| Function | Description |
|----------|-------------|
| `setAgent(name, metadata)` | Set current agent identity |
| `startSession(description)` | Start a new session |
| `getLogs(filter)` | Get filtered logs |
| `getRecentLogs(count)` | Get N most recent logs |
| `getLogSummary()` | Get summary statistics |
| `printLogs(count)` | Print logs to console |
| `exportLogs(filter)` | Export logs as JSON string |

## Module Exports

```javascript
import {
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
} from './logging/index.js';
```
