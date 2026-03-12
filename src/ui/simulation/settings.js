import {
  readSimulationState,
  updateSimulationStateParams
} from '../../modules/simulation/state.js';
import {
  bindPolarUiToggleHandlers,
  getPolarBlocksSignature,
  syncPolarControlsFromBlocks
} from './polarSettings.js';

export function setupSimulationParamBindings(panel) {
  bindPolarUiToggleHandlers();

  if (!panel._simulationParamBindingsAttached) {
    const bindingsById = new Map(
      panel.simulationParamBindings.map((binding) => [binding.id, binding])
    );
    const changeHandler = (e) => {
      const binding = bindingsById.get(e?.target?.id);
      if (!binding) return;

      const nextValue = binding.parse(e.target.value);
      if (Number.isNaN(nextValue)) return;

      const currentState = readSimulationState();
      const currentValue = currentState?.params?.[binding.key];
      if (currentValue === nextValue) return;

      updateSimulationStateParams({ [binding.key]: nextValue });
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

  const blocks = state.params._blocks;
  const signature = getPolarBlocksSignature(blocks);
  if (panel._lastPolarBlocksSignature !== signature) {
    syncPolarControlsFromBlocks(blocks);
    panel._lastPolarBlocksSignature = signature;
  }
}
