import { GlobalState } from '../../state.js';

export function setupSimulationParamBindings(panel) {
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

  const circsymModeEl = document.getElementById('circsym-mode');
  const circsymProfileEl = document.getElementById('circsym-profile');
  if (circsymModeEl && circsymProfileEl) {
    circsymModeEl.addEventListener('change', () => {
      const mode = parseInt(circsymModeEl.value, 10);
      if (mode < 0) {
        circsymProfileEl.disabled = true;
        if (GlobalState.get().params.abecSimProfile !== -1) {
          GlobalState.update({ abecSimProfile: -1 });
        }
      } else {
        circsymProfileEl.disabled = false;
        const profile = Math.max(0, parseInt(circsymProfileEl.value || '0', 10));
        if (GlobalState.get().params.abecSimProfile !== profile) {
          GlobalState.update({ abecSimProfile: profile });
        }
      }
    });

    circsymProfileEl.addEventListener('change', () => {
      if (parseInt(circsymModeEl.value, 10) < 0) return;
      const profile = Math.max(0, parseInt(circsymProfileEl.value || '0', 10));
      if (GlobalState.get().params.abecSimProfile !== profile) {
        GlobalState.update({ abecSimProfile: profile });
      }
    });
  }

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

  const circsymModeEl = document.getElementById('circsym-mode');
  const circsymProfileEl = document.getElementById('circsym-profile');
  if (circsymModeEl && circsymProfileEl) {
    const profile = Number(state.params.abecSimProfile ?? -1);
    if (profile >= 0) {
      circsymModeEl.value = '0';
      circsymProfileEl.disabled = false;
      circsymProfileEl.value = String(profile);
    } else {
      circsymModeEl.value = '-1';
      circsymProfileEl.disabled = true;
      circsymProfileEl.value = '0';
    }
  }
}
