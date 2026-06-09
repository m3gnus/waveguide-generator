import { isDevRuntime } from '../config/runtimeMode.js';
import { debugLog } from './debug.js';

const NOOP_TIMER = Object.freeze({
  mark() {},
  end() {},
});

function now() {
  const perf = globalThis?.performance;
  if (perf && typeof perf.now === 'function') {
    return perf.now();
  }
  return Date.now();
}

function formatDuration(ms) {
  return `${ms.toFixed(ms >= 10 ? 1 : 2)} ms`;
}

export function createPerfTimer(label) {
  if (!isDevRuntime()) return NOOP_TIMER;

  const start = now();
  let previous = start;

  return {
    mark(phase, metadata = null) {
      const current = now();
      const message = `[Perf] ${label}:${phase} +${formatDuration(current - previous)} total=${formatDuration(current - start)}`;
      if (metadata && typeof metadata === 'object') {
        debugLog(message, metadata);
      } else {
        debugLog(message);
      }
      previous = current;
    },
    end(metadata = null) {
      const current = now();
      const message = `[Perf] ${label} total=${formatDuration(current - start)}`;
      if (metadata && typeof metadata === 'object') {
        debugLog(message, metadata);
      } else {
        debugLog(message);
      }
    },
  };
}

export function measurePerf(label, fn, metadata = null) {
  const timer = createPerfTimer(label);
  try {
    return fn();
  } finally {
    timer.end(metadata);
  }
}
