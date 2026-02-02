# Validation Module — AI Agent Context

## Purpose

Validate simulation results against known references to build trust in the system.

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `index.js` | Public API, validation logic | Medium |

## Public API

```javascript
import {
  validateAgainstReference,  // Compare to reference data
  ValidationMetrics,         // Metric calculations
  PassFailThresholds,        // Acceptance criteria
  generateValidationReport   // Structured report
} from './validation/index.js';
```

## Validation Types

| Type | Description |
|------|-------------|
| MWG Reference | Compare to MWG software output |
| ABEC Reference | Compare to ABEC simulation |
| Published Data | Compare to published horn measurements |
| Self-Consistency | Check internal consistency |

## Metrics

| Metric | Description | Threshold |
|--------|-------------|-----------|
| SPL Error | dB difference in frequency response | ±2 dB |
| Phase Error | Degrees difference in phase | ±15° |
| DI Error | Directivity Index difference | ±1 dB |
| Coverage Error | Beamwidth difference | ±5° |

## Example Usage

```javascript
// Load reference data
const reference = loadReferenceData("tritonia2_abec.json");

// Run validation
const report = validateAgainstReference(
  simulationResults,
  reference,
  PassFailThresholds.DEFAULT
);

// Check result
if (report.passed) {
  console.log("Validation passed!");
} else {
  console.log("Issues:", report.failures);
}
```

## Report Structure

```javascript
{
  passed: true/false,
  metrics: {
    splError: { max: 1.5, mean: 0.8, passed: true },
    phaseError: { max: 12, mean: 5, passed: true },
    diError: { max: 0.7, mean: 0.3, passed: true }
  },
  failures: [],
  warnings: ["Phase deviation at 10kHz"],
  reference: {
    name: "Tritonia 2 ABEC",
    source: "MWG validation set"
  }
}
```

## For Simple Changes

1. Adjust thresholds → modify `PassFailThresholds`
2. Add metric → modify `ValidationMetrics`
3. Change report format → modify `generateValidationReport`

## Reference Data Format

```javascript
{
  name: "Reference Horn",
  source: "ABEC simulation",
  frequencies: [500, 1000, 2000, ...],
  spl_on_axis: [95, 97, 99, ...],
  di: [4.5, 5.2, 6.1, ...],
  phase: [0, -15, -30, ...]
}
```

## Why Validation Matters

- **Trust**: Know that simulations are accurate
- **Debugging**: Find where errors occur
- **Comparison**: Understand differences between tools
- **Quality**: Ensure consistent results over time
