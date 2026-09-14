import { create } from 'zustand';
import { noteSolveSettingsEdit } from './solveSettingsEdits';

export type CadSymmetryPreparationMode = 'auto' | 'full';

interface CadPreparationState {
  symmetryMode: CadSymmetryPreparationMode;
  setSymmetryMode: (mode: CadSymmetryPreparationMode) => void;
}

export const useCadPreparationStore = create<CadPreparationState>((set) => ({
  symmetryMode: 'auto',
  setSymmetryMode: (symmetryMode) => { set({ symmetryMode }); noteSolveSettingsEdit(); },
}));

export function resetCadPreparationStore(): void {
  useCadPreparationStore.setState({ symmetryMode: 'auto' });
}
