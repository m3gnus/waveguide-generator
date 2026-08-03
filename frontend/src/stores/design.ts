import { create } from 'zustand';
import { temporal } from 'zundo';

export type DesignFamily = 'OSSE' | 'R-OSSE' | 'ICW' | 'FREEFORM';
export type MutationReason = 'edit' | 'drag' | 'undo' | 'redo' | 'load' | 'family';
export type DesignValue = number | string | boolean | null;

export interface ExprNumber {
  value: number | null;
  raw?: string | null;
}

export interface ConfigBlock {
  items: Record<string, string>;
  lines: string[];
  comments?: string[];
}

export interface FreeformPoint {
  z: number;
  r: number;
  angle_deg?: number;
  strength?: number;
}

export interface FreeformProfile {
  points: FreeformPoint[];
  throat_angle_deg?: number;
  mouth_angle_deg?: number;
  throat_tangent_scale?: number;
  mouth_tangent_scale?: number;
}

export interface CrossSectionStation {
  t: number;
  shape: 'circle' | 'ellipse' | 'superellipse' | 'rounded_rectangle';
  exponent?: number;
  corner_radius_mm?: number;
  corner_grid?: number[][];
}

export interface CornerGrid {
  t: number;
  values: number[][];
}

/**
 * Evaluated client representation of server/design/schema.py's DesignConfig.
 *
 * Pydantic's Expr accepts finite numbers directly, so editable expressions are
 * held as their evaluated numeric value here. `quadrants` and `baffle_margin`
 * are temporary, non-serialized preview compatibility mirrors used by the
 * existing preview adapter; their schema values remain authoritative in
 * `mesh.quadrants` and the four `enclosure.space_*` fields.
 */
export interface DesignDocument {
  formula: DesignFamily;
  scale: number;
  throat_ext_angle: number;
  throat_ext_length: number;
  slot_length: number;
  length_mode: 'profile' | 'total' | null;
  coverage_mode: string | null;

  // Family scalar union from OSSEConfig, ROSSEConfig and ICWConfig.
  R?: number;
  L?: number;
  a?: number;
  a0?: number;
  r0?: number;
  k?: number;
  s?: number;
  n?: number;
  q?: number;
  h?: number;
  b?: number;
  m?: number;
  r?: number;
  tmax?: number;
  coverage_angle?: number;
  hold_start?: number;
  hold_end?: number;
  n_coeff?: number;
  termination?: string;
  theta1_deg?: number;
  depth?: number;
  curl?: number;
  throat_profile?: number;
  rotation?: number;
  circ_arc_radius?: number;
  circ_arc_term_angle?: number;

  guiding_curve: {
    curve_type: number;
    distance: number;
    width: number;
    aspect_ratio: number;
    superellipse_n: number;
    superformula: number;
    sf_a: number;
    sf_b: number;
    sf_m1: number;
    sf_m2: number;
    sf_n1: number;
    sf_n2: number;
    sf_n3: number;
    rotation: number;
  };
  morph: {
    target_shape: number;
    target_width: number;
    target_height: number;
    corner_radius: number;
    rate: number;
    fixed_part: number;
    allow_shrinkage: number;
  };
  source: {
    shape: number;
    radius: number;
    curvature: number;
    velocity: number;
    contours: string;
    velocity_convention: 'normal' | 'axial' | 'legacy';
  };
  quadrants: number[];
  enclosure: {
    depth: number;
    edge_radius: number;
    edge_type: number;
    space_l: number;
    space_t: number;
    space_r: number;
    space_b: number;
    front_resolution: number;
    back_resolution: number;
    baffle_margin: number;
  };
  mesh: {
    angular_segments: number;
    corner_segments: number;
    throat_segments: number;
    length_segments: number;
    throat_resolution: number;
    mouth_resolution: number;
    throat_slice_density: number;
    sampling_mode: string;
    z_map_points: string;
    vertical_offset: number;
    quadrants: number;
    wall_thickness: number;
    rear_resolution: number;
    aperture_resolution_scale: number;
    max_triangles: number;
    allow_large_mesh: number;
  };
  simulation: {
    f1: number;
    f2: number;
    num_frequencies: number;
    sim_type: 'freestanding' | 'infinite-baffle';
    solver_mode: string;
  };
  output: { stl: number; msh: number };
  extra_keys: Record<string, string>;
  extra_blocks: Record<string, ConfigBlock>;
  profile_h?: FreeformProfile;
  profile_v?: FreeformProfile;
  cross_sections?: CrossSectionStation[];
  overshoot_policy?: string;
  inflection_policy?: string;
  corner_grids?: CornerGrid[];
}

const common = {
  scale: 1,
  throat_ext_angle: 0,
  throat_ext_length: 0,
  slot_length: 0,
  length_mode: 'profile' as const,
  coverage_mode: 'auto',
  guiding_curve: {
    curve_type: 0, distance: .5, width: 0, aspect_ratio: 1, superellipse_n: 3,
    superformula: 0, sf_a: 1, sf_b: 1, sf_m1: 4, sf_m2: 4, sf_n1: 1,
    sf_n2: 1, sf_n3: 1, rotation: 0,
  },
  morph: {
    target_shape: 1, target_width: 0, target_height: 0, corner_radius: 0,
    rate: 3, fixed_part: 0, allow_shrinkage: 0,
  },
  source: {
    shape: 1, radius: -1, curvature: 0, velocity: 1,
    contours: '', velocity_convention: 'normal' as const,
  },
  quadrants: [1, 2, 3, 4],
  enclosure: {
    depth: 0, edge_radius: 18, edge_type: 1, space_l: 25, space_t: 25,
    space_r: 25, space_b: 25, front_resolution: 25, back_resolution: 40,
    baffle_margin: 25,
  },
  mesh: {
    angular_segments: 40, corner_segments: 4, throat_segments: 0,
    length_segments: 20, throat_resolution: 6, mouth_resolution: 15,
    throat_slice_density: .5, sampling_mode: 'uniform', z_map_points: '',
    vertical_offset: 0, quadrants: 15, wall_thickness: 0, rear_resolution: 40,
    aperture_resolution_scale: 1.5, max_triangles: 50_000, allow_large_mesh: 0,
  },
  simulation: {
    f1: 400, f2: 16_000, num_frequencies: 20,
    sim_type: 'freestanding' as const, solver_mode: 'auto',
  },
  output: { stl: 0, msh: 0 },
  extra_keys: {} as Record<string, string>,
  extra_blocks: {} as Record<string, ConfigBlock>,
};

export const seedDesign: DesignDocument = {
  formula: 'R-OSSE', R: 140, r0: 12.7, a0: 15.5, a: 25, k: 2,
  m: .85, b: .2, r: .4, q: 3.4, tmax: 1,
  ...structuredClone(common),
};

export function designForFamily(family: DesignFamily): DesignDocument {
  const shared = structuredClone(common);
  if (family === 'R-OSSE') return { ...structuredClone(seedDesign), formula: family };
  if (family === 'OSSE') {
    return {
      formula: family, L: 130, a: 45, a0: 10, r0: 12.7, k: 7, s: .85,
      n: 4, q: .991, h: 0, throat_profile: 1, rotation: 0,
      circ_arc_radius: 0, circ_arc_term_angle: 1, ...shared,
    };
  }
  if (family === 'ICW') {
    return {
      formula: family, R: 150, L: 120, r0: 12.7, a0: 15, a: 0, k: 0,
      q: 0, coverage_angle: 0, hold_start: .3, hold_end: .7, n_coeff: 6,
      termination: 'flat_baffle', theta1_deg: 160, depth: 100, curl: 0, ...shared,
    };
  }
  return {
    formula: family,
    ...shared,
    profile_h: {
      points: [{ z: 0, r: 12.7 }, { z: 120, r: 140 }],
      throat_angle_deg: 15.5, mouth_angle_deg: 60,
      throat_tangent_scale: 1, mouth_tangent_scale: 1,
    },
    profile_v: {
      points: [{ z: 0, r: 12.7 }, { z: 120, r: 140 }],
      throat_angle_deg: 15.5, mouth_angle_deg: 60,
      throat_tangent_scale: 1, mouth_tangent_scale: 1,
    },
    cross_sections: [{ t: 0, shape: 'circle' }, { t: 1, shape: 'ellipse' }],
    overshoot_policy: 'reject',
    inflection_policy: 'warn',
    corner_grids: [],
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
  updateValue: (path: string, value: DesignValue) => void;
  updateValues: (updates: Record<string, DesignValue>) => void;
  setQuadrants: (quadrants: number[]) => void;
  setSourceConvention: (convention: DesignDocument['source']['velocity_convention']) => void;
  setFamily: (family: DesignFamily) => void;
  loadDesign: (design: DesignDocument) => void;
  beginDrag: () => void;
  endDrag: () => void;
  undo: () => void;
  redo: () => void;
}

function setAtPath(design: DesignDocument, path: string, value: DesignValue): DesignDocument {
  const next = structuredClone(design);
  const parts = path.split('.');
  let cursor: Record<string, unknown> = next as unknown as Record<string, unknown>;
  for (let index = 0; index < parts.length - 1; index += 1) {
    const child = cursor[parts[index]];
    if (typeof child !== 'object' || child === null) throw new Error(`Unknown design path: ${path}`);
    cursor = child as Record<string, unknown>;
  }
  cursor[parts.at(-1)!] = value;
  if (path.startsWith('enclosure.space_')) next.enclosure.baffle_margin = Number(value);
  return next;
}

function setMany(design: DesignDocument, updates: Record<string, DesignValue>): DesignDocument {
  return Object.entries(updates).reduce((next, [path, value]) => setAtPath(next, path, value), design);
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
      updateField: (path, value) => get().updateValue(path, value),
      updateValue: (path, value) => {
        set((state) => ({
          design: setAtPath(state.design, path, value),
          designRevision: state.designRevision + 1,
        }));
        bump(get().dragSnapshot ? 'drag' : 'edit', false);
      },
      updateValues: (updates) => {
        set((state) => ({
          design: setMany(state.design, updates),
          designRevision: state.designRevision + 1,
        }));
        bump(get().dragSnapshot ? 'drag' : 'edit', false);
      },
      setQuadrants: (quadrants) => {
        const sorted = [...quadrants].sort();
        const mask = sorted.reduce((value, quadrant) => value | (1 << (quadrant - 1)), 0);
        set((state) => ({
          design: { ...state.design, quadrants: sorted, mesh: { ...state.design.mesh, quadrants: mask } },
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

/**
 * Serialize a DesignDocument into the exact shape server/design/schema.py's
 * DesignConfig accepts. Owns the mirror-stripping rules so every send path
 * (preview WS, solve submit) stays schema-valid:
 * - top-level `quadrants` mirror -> mesh.quadrants bitmask
 * - `enclosure.baffle_margin` mirror -> the four space_* fields
 * - `guiding_curve` is OSSE-only in the schema (ROSSEConfig/ICWConfig/
 *   FreeformConfig forbid it) and is dropped for every other family.
 */
export function serializeDesign(design: DesignDocument): Record<string, unknown> {
  const { quadrants, enclosure, mesh, ...root } = structuredClone(design);
  const payload: Record<string, unknown> = {
    ...root,
    mesh: {
      ...mesh,
      quadrants: quadrants.reduce((mask, quadrant) => mask | (1 << (quadrant - 1)), 0),
    },
    enclosure: {
      depth: enclosure.depth,
      edge_radius: enclosure.edge_radius,
      space_l: enclosure.baffle_margin,
      space_t: enclosure.baffle_margin,
      space_r: enclosure.baffle_margin,
      space_b: enclosure.baffle_margin,
    },
  };
  if (design.formula !== 'OSSE') delete payload.guiding_curve;
  return payload;
}
