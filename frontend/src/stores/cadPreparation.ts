import { create } from 'zustand';

export type CadSymmetryPreparationMode = 'auto' | 'full';

interface CadPreparationState {
  symmetryMode: CadSymmetryPreparationMode;
  setSymmetryMode: (mode: CadSymmetryPreparationMode) => void;
}

export const useCadPreparationStore = create<CadPreparationState>((set) => ({
  symmetryMode: 'auto',
  setSymmetryMode: (symmetryMode) => set({ symmetryMode }),
}));

export function resetCadPreparationStore(): void {
  useCadPreparationStore.setState({ symmetryMode: 'auto' });
}
