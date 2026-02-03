# Results Module - AI Agent Context

## Purpose

Render and post-process simulation results for the UI (smoothing, charts, reports).

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `smoothing.js` | Fractional-octave and psychoacoustic smoothing | Medium |

## Public API

```javascript
import { applySmoothing } from './results/smoothing.js';
```

## Notes

- Keep rendering concerns in the UI layer; results should focus on data shaping.
- Add new result utilities here to keep UI modules smaller.
