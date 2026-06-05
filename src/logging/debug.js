import { isDevRuntime } from '../config/runtimeMode.js';

function emit(method, args) {
  if (!isDevRuntime()) {
    return;
  }
  console[method](...args);
}

export function debugLog(...args) {
  emit('log', args);
}

export function debugWarn(...args) {
  emit('warn', args);
}

export function debugError(...args) {
  emit('error', args);
}
