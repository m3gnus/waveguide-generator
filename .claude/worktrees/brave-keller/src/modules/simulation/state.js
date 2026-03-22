import { GlobalState } from '../../state.js';

function isObject(value) {
  return value !== null && typeof value === 'object';
}

export function readSimulationState() {
  return GlobalState.get();
}

export function updateSimulationStateParams(nextParams = {}) {
  if (!isObject(nextParams)) {
    return readSimulationState();
  }
  GlobalState.update(nextParams);
  return readSimulationState();
}

export function loadSimulationStateSnapshot(stateSnapshot, source = 'simulation-job-load-script') {
  if (!isObject(stateSnapshot) || !isObject(stateSnapshot.params)) {
    return null;
  }
  GlobalState.loadState(stateSnapshot, source);
  return readSimulationState();
}

export function applySimulationJobScriptState(script = {}, options = {}) {
  const source = typeof options.source === 'string' && options.source.trim()
    ? options.source
    : 'simulation-job-load-script';

  if (isObject(script.stateSnapshot) && isObject(script.stateSnapshot.params)) {
    loadSimulationStateSnapshot(script.stateSnapshot, source);
    return {
      mode: 'snapshot',
      params: script.stateSnapshot.params
    };
  }

  if (isObject(script.params)) {
    updateSimulationStateParams(script.params);
    return {
      mode: 'params',
      params: script.params
    };
  }

  return {
    mode: 'none',
    params: null
  };
}
