# AI Module — AI Agent Context

## Purpose

AI-assisted design features including surrogate models, Bayesian optimization, and design insights.

## ⚠️ Current Status

**PHASE 7 — STUBS ONLY**

These modules are placeholders with mock implementations. They require:
- Working BEM solver (Phase 4)
- Training data from real simulations
- GP/ML library integration

## Submodules

| Folder | Purpose | Status |
|--------|---------|--------|
| `knowledge/` | Design knowledge capture | Stub |
| `surrogate/` | Surrogate model (GP, regression) | Stub |
| `optimization/` | Bayesian opt, CMA-ES | Stub |
| `insights/` | Human-readable explanations | Stub |

## Public API

```javascript
import {
  storeDesignKnowledge,    // Save design + results
  createSurrogateModel,    // Build surrogate
  BayesianOptimizer,       // BO with acquisition
  generateDesignInsights   // Human-readable tips
} from './ai/index.js';
```

## Design Principles

1. **Assist, Don't Replace** — AI helps engineers, doesn't make decisions
2. **Traceability** — All suggestions traceable to data
3. **Deterministic** — Reproducible outputs
4. **No Black Boxes** — Explainable predictions

## Knowledge Capture

```javascript
// Store a completed design with results
storeDesignKnowledge({
  params: { r0: 15, k: 3 },
  mesh: { vertices: 12000, triangles: 24000 },
  acoustics: { di: [...], spl: [...] },
  score: 0.85
});
```

## Surrogate Modeling

```javascript
// Create surrogate from stored designs
const surrogate = createSurrogateModel(designHistory);

// Predict outcome for new params
const prediction = surrogate.predict({ r0: 16, k: 3.5 });
// Returns: { mean, uncertainty }
```

## Bayesian Optimization

```javascript
const optimizer = new BayesianOptimizer(space, objectives);

// Get next point to evaluate
const nextParams = optimizer.suggest();

// Update with real result
optimizer.update(nextParams, actualScore);
```

## Insights Generation

```javascript
const insights = generateDesignInsights(design, results);
// Returns: [
//   "Mouth flare dominates DI stability above 3 kHz",
//   "Consider reducing k to smooth 2-4 kHz ripple"
// ]
```

## For Implementation

When implementing these features:
1. Start with knowledge capture (storage layer)
2. Build simple surrogate (polynomial) before GP
3. Test BO with synthetic objective before real BEM
4. Generate insights from simple rules first

## Dependencies

- `src/optimization/` — Base optimization engine
- `src/solver/` — BEM results for training
- `src/config/` — Parameter schemas
