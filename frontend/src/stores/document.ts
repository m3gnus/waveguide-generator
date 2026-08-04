import { create } from 'zustand';

interface DocumentState {
  filename: string;
  setFilename: (filename: string) => void;
}

export const useDocumentStore = create<DocumentState>((set) => ({
  filename: 'tritonia_mk2.cfg',
  setFilename: (filename) => set({ filename }),
}));
