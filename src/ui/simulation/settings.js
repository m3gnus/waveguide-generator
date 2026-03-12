import {
  readSimulationState,
  updateSimulationStateParams
} from '../../modules/simulation/state.js';
import {
  buildPolarStatePatchForControl,
  ensurePolarControlsRendered,
  getPolarStateSignature,
  isPolarControlId,
  syncPolarControlsFromState
} from './polarSettings.js';

function statePatchMatches(currentParams, nextPatch) {
  return Object.entries(nextPatch).every(([key, value]) => {
    const currentValue = currentParams?.[key];
    if (Array.isArray(value) || (value && typeof value === 'object')) {
      return JSON.stringify(currentValue) === JSON.stringify(value);
    }
    return currentValue === value;
  });
}

export function setupSimulationParamBindings(panel) {
  ensurePolarControlsRendered(document);

  if (!panel._simulationParamBindingsAttached) {
    const bindingsById = new Map(
      panel.simulationParamBindings.map((binding) => [binding.id, binding])
    );
    const changeHandler = (e) => {
      const currentState = readSimulationState();
      const targetId = e?.target?.id;
      if (!targetId) return;

      const binding = bindingsById.get(targetId);
      if (binding) {
        const nextValue = binding.parse(e.target.value);
        if (Number.isNaN(nextValue)) return;

        const currentValue = currentState?.params?.[binding.key];
        if (currentValue === nextValue) return;

        updateSimulationStateParams({ [binding.key]: nextValue });
        return;
      }

      if (!isPolarControlId(targetId)) return;

      const nextPatch = buildPolarStatePatchForControl(targetId, currentState?.params, document);
      if (!nextPatch || statePatchMatches(currentState?.params, nextPatch)) return;

      updateSimulationStateParams(nextPatch);
    };

    document.addEventListener('change', changeHandler);
    panel._simulationParamBindingsAttached = true;
    panel._simulationParamChangeHandler = changeHandler;
  }

  syncSimulationSettings(panel, readSimulationState());
}

export function teardownSimulationParamBindings(panel) {
  if (!panel?._simulationParamBindingsAttached || typeof panel._simulationParamChangeHandler !== 'function') {
    return;
  }

  document.removeEventListener('change', panel._simulationParamChangeHandler);
  panel._simulationParamBindingsAttached = false;
  panel._simulationParamChangeHandler = null;
}

export function syncSimulationSettings(panel, state) {
  if (!state || !state.params) return;

  panel.simulationParamBindings.forEach(({ id, key }) => {
    const element = document.getElementById(id);
    if (!element) return;

    const value = state.params[key];
    if (value === undefined || value === null) return;

    const nextValue = String(value);
    if (element.value !== nextValue) {
      element.value = nextValue;
    }
  });

  const signature = getPolarStateSignature(state.params);
  if (panel._lastPolarStateSignature !== signature) {
    syncPolarControlsFromState(state.params);
    panel._lastPolarStateSignature = signature;
  }
}
