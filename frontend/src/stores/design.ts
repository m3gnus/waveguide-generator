import { create } from 'zustand';
import { temporal } from 'zundo';

export type DesignFamily = 'OSSE' | 'R-OSSE' | 'ICW' | 'FREEFORM';
export type MutationReason = 'edit' | 'drag' | 'undo' | 'redo' | 'load' | 'family';

export interface ExprNumber {
  value: number;
}

export interface DesignDocument {
  formula: DesignFamily;
  R?: number;
  L?: number;
  a?: number;
  a0?: number;
  r0?: number;
  k?: number;
  r?: number;
  b?: number;
  m?: number;
  source: {
    shape: number;
    velocity: number;
    velocity_convention: 'normal' | 'axial' | 'legacy';
  };
  quadrants: number[];
  enclosure: {
    depth: number;
    edge_radius: number;
    baffle_margin: number;
  };
  mesh: {
    angular_segments: number;
    length_segments: number;
    mouth_resolution: number;
  };
  profile_h?: { points: Array<{ z: number; r: number }> };
  profile_v?: { points: Array<{ z: number; r: number }> };
  cross_sections?: Array<{ t: number; shape: 'circle' | 'ellipse' }>;
}

const common = {
  source: { shape: 1, velocity: 1, velocity_convention: 'axial' as const },
  quadrants: [1, 2],
  enclosure: { depth: 350, edge_radius: 18, baffle_margin: 24 },
  mesh: { angular_segments: 96, length_segments: 48, mouth_resolution: 12 },
};

export const seedDesign: DesignDocument = {
  formula: 'R-OSSE',
  R: 150,
  r0: 12.7,
  a0: 15,
  a: 42,
  k: 0.82,
  r: 0.36,
  b: 0.28,
  m: 0.85,
  ...structuredClone(common),
};

function designForFamily(family: DesignFamily): DesignDocument {
  if (family === 'R-OSSE') return structuredClone(seedDesign);
  if (family === 'OSSE') {
    return { formula: family, L: 167.4, r0: 12.7, a0: 15, a: 42, k: .82, ...structuredClone(common) };
  }
  if (family === 'ICW') {
    return { formula: family, R: 150, L: 167.4, r0: 12.7, a0: 15, a: 42, k: .82, ...structuredClone(common) };
  }
  return {
    formula: family,
    ...structuredClone(common),
    profile_h: { points: [{ z: 0, r: 12.7 }, { z: 167.4, r: 150 }] },
    profile_v: { points: [{ z: 0, r: 12.7 }, { z: 167.4, r: 110 }] },
    cross_sections: [{ t: 0, shape: 'circle' }, { t: 1, shape: 'ellipse' }],
  };
}

export interface RevisionEvent {
  revision: number;
  reason: MutationReason;
  immediate: boolean;
}

type RevisionListener = (event: RevisionEvent) => void;
type TimerCanceller = () => void;
const revisionListeners = new Set<RevisionListener>();
const timerCancellers = new Set<TimerCanceller>();

export function subscribeRevision(listener: RevisionListener): () => void {
  revisionListeners.add(listener);
  return () => revisionListeners.delete(listener);
}

export function registerRevisionTimer(canceller: TimerCanceller): () => void {
  timerCancellers.add(canceller);
  return () => timerCancellers.delete(canceller);
}

export function cancelRevisionTimers(): void {
  timerCancellers.forEach((cancel) => cancel());
}

function announce(event: RevisionEvent): void {
  revisionListeners.forEach((listener) => listener(event));
}

interface DesignStore {
  design: DesignDocument;
  designRevision: number;
  dragSnapshot: DesignDocument | null;
  updateField: (path: string, value: number) => void;
  setQuadrants: (quadrants: number[]) => void;
  setSourceConvention: (convention: DesignDocument['source']['velocity_convention']) => void;
  setFamily: (family: DesignFamily) => void;
  loadDesign: (design: DesignDocument) => void;
  beginDrag: () => void;
  endDrag: () => void;
  undo: () => void;
  redo: () => void;
}

function setAtPath(design: DesignDocument, path: string, value: number): DesignDocument {
  const next = structuredClone(design);
  const parts = path.split('.');
  let cursor: Record<string, unknown> = next as unknown as Record<string, unknown>;
  for (let index = 0; index < parts.length - 1; index += 1) {
    const child = cursor[parts[index]];
    if (typeof child !== 'object' || child === null) throw new Error(`Unknown design path: ${path}`);
    cursor = child as Record<string, unknown>;
  }
  cursor[parts.at(-1)!] = value;
  return next;
}

function bump(reason: MutationReason, immediate: boolean): void {
  const revision = useDesignStore.getState().designRevision;
  announce({ revision, reason, immediate });
}

export const useDesignStore = create<DesignStore>()(
  temporal(
    (set, get) => ({
      design: structuredClone(seedDesign),
      designRevision: 1,
      dragSnapshot: null,
      updateField: (path, value) => {
        set((state) => ({
          design: setAtPath(state.design, path, value),
          designRevision: state.designRevision + 1,
        }));
        bump(get().dragSnapshot ? 'drag' : 'edit', false);
      },
      setQuadrants: (quadrants) => {
        set((state) => ({
          design: { ...state.design, quadrants: [...quadrants].sort() },
          designRevision: state.designRevision + 1,
        }));
        bump('edit', false);
      },
      setSourceConvention: (velocity_convention) => {
        set((state) => ({
          design: { ...state.design, source: { ...state.design.source, velocity_convention } },
          designRevision: state.designRevision + 1,
        }));
        bump('edit', false);
      },
      setFamily: (family) => {
        cancelRevisionTimers();
        set((state) => ({ design: designForFamily(family), designRevision: state.designRevision + 1 }));
        bump('family', true);
      },
      loadDesign: (design) => {
        cancelRevisionTimers();
        set((state) => ({ design: structuredClone(design), designRevision: state.designRevision + 1 }));
        bump('load', true);
      },
      beginDrag: () => {
        if (get().dragSnapshot) return;
        set({ dragSnapshot: structuredClone(get().design) });
        useDesignStore.temporal.getState().pause();
      },
      endDrag: () => {
        const snapshot = get().dragSnapshot;
        if (!snapshot) return;
        useDesignStore.temporal.getState().resume();
        if (JSON.stringify(snapshot) !== JSON.stringify(get().design)) {
          useDesignStore.temporal.setState((state) => ({
            pastStates: [...state.pastStates.slice(-99), { design: snapshot }],
            futureStates: [],
          }));
        }
        set({ dragSnapshot: null });
      },
      undo: () => {
        cancelRevisionTimers();
        const snapshot = get().dragSnapshot;
        if (snapshot) {
          useDesignStore.temporal.getState().resume();
          set((state) => ({ design: snapshot, dragSnapshot: null, designRevision: state.designRevision + 1 }));
        } else if (useDesignStore.temporal.getState().pastStates.length) {
          useDesignStore.temporal.getState().undo();
          set((state) => ({ designRevision: state.designRevision + 1 }));
        } else {
          return;
        }
        bump('undo', true);
      },
      redo: () => {
        cancelRevisionTimers();
        if (!useDesignStore.temporal.getState().futureStates.length) return;
        useDesignStore.temporal.getState().redo();
        set((state) => ({ designRevision: state.designRevision + 1 }));
        bump('redo', true);
      },
    }),
    {
      partialize: (state) => ({ design: state.design }),
      equality: (past, current) => JSON.stringify(past.design) === JSON.stringify(current.design),
      limit: 100,
    },
  ),
);

export function resetDesignStore(): void {
  useDesignStore.temporal.getState().clear();
  useDesignStore.temporal.getState().resume();
  useDesignStore.setState({ design: structuredClone(seedDesign), designRevision: 1, dragSnapshot: null });
}
