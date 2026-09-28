import { create } from 'zustand';
import { withEditSignals } from './solveSettingsEdits';
import { DEFAULT_CAD_PREPARATION_SYMMETRY } from './solveDefaults';

export type CadSymmetryPreparationMode = 'auto' | 'full';

interface CadPreparationState {
  symmetryMode: CadSymmetryPreparationMode;
  setSymmetryMode: (mode: CadSymmetryPreparationMode) => void;
}

export const useCadPreparationStore = create<CadPreparationState>((set, get) => withEditSignals(get, {
  symmetryMode: DEFAULT_CAD_PREPARATION_SYMMETRY,
  setSymmetryMode: (symmetryMode) => set({ symmetryMode }),
}, ['setSymmetryMode']));

export function resetCadPreparationStore(): void {
  useCadPreparationStore.setState({ symmetryMode: DEFAULT_CAD_PREPARATION_SYMMETRY });
}
