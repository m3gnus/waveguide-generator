import { GlobalState } from '../../state.js';
import {
  bindPolarUiToggleHandlers,
  getPolarBlocksSignature,
  syncPolarControlsFromBlocks
} from './polarSettings.js';

export function setupSimulationParamBindings(panel) {
  bindPolarUiToggleHandlers();

  panel.simulationParamBindings.forEach(({ id, key, parse }) => {
    const element = document.getElementById(id);
    if (!element) return;

    element.addEventListener('change', (e) => {
      const nextValue = parse(e.target.value);
      if (Number.isNaN(nextValue)) return;

      const currentValue = GlobalState.get().params[key];
      if (currentValue === nextValue) return;

      GlobalState.update({ [key]: nextValue });
    });
  });

  syncSimulationSettings(panel, GlobalState.get());
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
