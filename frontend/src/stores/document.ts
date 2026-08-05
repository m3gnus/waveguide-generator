import { create } from 'zustand';

interface DocumentState {
  filename: string;
  savedRevision: number | null;
  setFilename: (filename: string) => void;
  markSaved: (revision: number) => void;
  restoreDocumentState: (state: Pick<DocumentState, 'filename' | 'savedRevision'>) => void;
}

export const useDocumentStore = create<DocumentState>((set) => ({
  filename: 'tritonia_mk2.cfg',
  savedRevision: 1,
  setFilename: (filename) => set({ filename }),
  markSaved: (savedRevision) => set({ savedRevision }),
  restoreDocumentState: ({ filename, savedRevision }) => set({ filename, savedRevision }),
}));

export function resetDocumentStore(): void {
  useDocumentStore.setState({ filename: 'tritonia_mk2.cfg', savedRevision: 1 });
}
