# Optimization Module — AI Agent Context

## Purpose

Automated parameter exploration, design scoring, and optimization algorithms.

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `index.js` | Public API exports | Simple |
| `engine.js` | Optimization loop and algorithms | Complex |
| `objectiveFunctions.js` | Acoustic quality scoring | Medium |
| `parameterSpace.js` | Parameter bounds management | Medium |
| `results.js` | Result storage/management | Simple |
| `api.js` | Clean API interface | Simple |

## Public API

```javascript
import {
  optimizeHorn,         // Main optimization function
  createParameterSpace, // Define search bounds
  ObjectiveFunctions,   // Scoring functions
  OptimizationEngine    // Core engine
} from './optimization/index.js';
```

## Optimization Flow

```
1. Define parameter bounds (which params, min/max)
2. Choose objective functions (what to optimize)
3. Run optimization (grid, random, coordinate descent)
4. Collect results (scores, parameters)
5. Rank and compare designs
```

## Objective Functions

| Function | Purpose |
|----------|---------|
| `smoothResponse` | Minimize on-axis ripple |
| `directivityControl` | Target beamwidth vs frequency |
| `phaseSmoothing` | Consistent group delay |
| `impedanceMatch` | Throat impedance matching |

## For Simple Changes

1. Add objective function → modify `objectiveFunctions.js`
2. Change bounds → modify `parameterSpace.js`
3. Adjust scoring weights → modify calling code

## For Complex Changes

Before adding optimization algorithms:
1. Understand current engine in `engine.js`
2. Follow deterministic patterns (seeded random)
3. Ensure results are reproducible

## Example Usage

```javascript
// Define what to optimize
const space = createParameterSpace({
  r0: { min: 10, max: 20, steps: 5 },
  k: { min: 1, max: 5, steps: 5 }
});

// Define objectives
const objectives = [
  { fn: ObjectiveFunctions.smoothResponse, weight: 0.5 },
  { fn: ObjectiveFunctions.directivityControl, weight: 0.5 }
];

// Run optimization
const results = await optimizeHorn(baseParams, space, objectives);

// Results: array of { params, scores, combined }
```

## Algorithms

| Algorithm | Use Case |
|-----------|----------|
| Grid Search | Small parameter spaces (<100 combinations) |
| Random Sample | Larger spaces, exploration |
| Coordinate Descent | Single parameter refinement |

## Integration with BEM

The optimization module requires BEM results for scoring:

```javascript
// Each candidate design:
// 1. Generate geometry
// 2. Run BEM simulation (or use surrogate model)
// 3. Extract acoustic metrics
// 4. Compute objective scores
```

## Future Algorithms (Phase 7)

- Bayesian Optimization (via AI module)
- CMA-ES (via AI module)
- Genetic algorithms
