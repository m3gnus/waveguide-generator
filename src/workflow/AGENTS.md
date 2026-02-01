# Workflow Module — AI Agent Context

## Purpose

Manages the canonical design workflow state machine, tracking stages from geometry to final results.

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `index.js` | Public API, state machine | Medium |

## Workflow Stages

```
1. GEOMETRY    → Define horn parameters
2. MESH        → Generate mesh for simulation
3. BOUNDARY    → Set boundary conditions
4. SOLVER      → Configure BEM solver
5. SIMULATE    → Run acoustic simulation
6. RESULTS     → View and analyze results
7. OPTIMIZE    → (Optional) Run optimization
8. EXPORT      → Export final design
```

## State Machine

```javascript
const workflow = {
  currentStage: 'GEOMETRY',
  stages: {
    GEOMETRY: { complete: true, data: { params } },
    MESH: { complete: false, data: null },
    // ...
  },
  history: []
};
```

## Public API

```javascript
import {
  WorkflowManager,     // Workflow state machine
  WorkflowStages,      // Stage constants
  getWorkflowState,    // Current state
  advanceWorkflow,     // Move to next stage
  validateStage        // Check stage readiness
} from './workflow/index.js';
```

## For Simple Changes

1. Add validation → modify `validateStage()`
2. Add stage data → extend stage object
3. Change transitions → modify state machine

## Stage Transitions

Each stage has:
- **Entry conditions**: What must be complete before entering
- **Data requirements**: What data must be present
- **Validation**: Check that stage output is valid
- **Exit conditions**: What must be done to leave

## Event Integration

```javascript
// Stage completed
AppEvents.emit('workflow:stage-complete', { stage, data });

// Stage entered
AppEvents.emit('workflow:stage-entered', { stage });

// Workflow reset
AppEvents.emit('workflow:reset');
```

## Example Usage

```javascript
// Check current stage
const state = getWorkflowState();
console.log(state.currentStage); // 'GEOMETRY'

// Validate before advancing
if (validateStage('MESH', meshData)) {
  advanceWorkflow('MESH', meshData);
}

// Get stage data
const geometryData = state.stages.GEOMETRY.data;
```

## Why Workflow Matters

- **Clear progress**: User knows where they are
- **Validation**: Can't skip required steps
- **Reproducibility**: Can replay workflow
- **Debugging**: Intermediate artifacts available
