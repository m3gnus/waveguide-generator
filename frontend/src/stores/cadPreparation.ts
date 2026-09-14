import { create } from 'zustand';
import { withEditSignals } from './solveSettingsEdits';

export type CadSymmetryPreparationMode = 'auto' | 'full';

interface CadPreparationState {
  symmetryMode: CadSymmetryPreparationMode;
  setSymmetryMode: (mode: CadSymmetryPreparationMode) => void;
}

export const useCadPreparationStore = create<CadPreparationState>((set, get) => withEditSignals(get, {
  symmetryMode: 'auto',
  setSymmetryMode: (symmetryMode) => set({ symmetryMode }),
}, ['setSymmetryMode']));

export function resetCadPreparationStore(): void {
  useCadPreparationStore.setState({ symmetryMode: 'auto' });
}
