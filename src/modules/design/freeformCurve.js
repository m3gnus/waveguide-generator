// Design-layer facade used by UI consumers. The pure implementation remains
// in geometry so it can also be tested and reused without browser dependencies.
export { buildFreeformDisplayCurve, computeInflectionSpans } from '../../geometry/freeformCurve.js';
