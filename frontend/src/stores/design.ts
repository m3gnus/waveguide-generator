import { create } from 'zustand';
import { temporal } from 'zundo';
import { findParameterByPath } from '../design/parameterRegistry';
import { replaceAthPolarBlocks } from './athPolars';

export type DesignFamily = 'OSSE' | 'R-OSSE' | 'ICW' | 'FREEFORM';
export type MutationReason = 'edit' | 'drag' | 'undo' | 'redo' | 'load' | 'family';
export type DesignValue = number | string | boolean | null | FreeformPoint[] | CrossSectionStation[];

export interface ExprNumber {
  value: number | null;
  raw?: string | null;
}

export interface ConfigBlock {
  items: Record<string, string>;
  lines: string[];
  comments?: string[];
  entries?: string[];
}

export interface FreeformPoint {
  t: number;
  r: number;
  angle_deg?: number;
}

export interface FreeformProfile {
  points: FreeformPoint[];
  throat_angle_deg?: number;
  mouth_angle_deg?: number;
}

export interface CrossSectionStation {
  t: number;
  shape: 'ellipse' | 'superellipse' | 'rounded_rectangle';
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
 * Numeric values stay scalar so preview/result consumers remain simple. Exact
 * ATH spelling and any server-evaluated value live in `_expressions`; the
 * serializer overlays those Expr objects on the wire and strips the sidecar.
 * `quadrants` and `baffle_margin` are non-serialized compatibility mirrors.
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
  length?: number;
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
    max_edge?: number | null;
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
  inflection_policy?: string;
  corner_grids?: CornerGrid[];
  /** UI sidecar for lossless ATH expression spelling; stripped on the wire. */
  _expressions?: Record<string, ExprNumber>;
  /** Schema paths omitted by the source config; restored to null on the wire. */
  _absent?: string[];
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
    // Morph OFF by default (v1's morphTarget default). A Rectangle target with
    // 0x0 dimensions is degenerate and collapses the mesher's morph path.
    target_shape: 0, target_width: 0, target_height: 0, corner_radius: 0,
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
    // ATH defaults omitted WallThickness to 5 mm, verified against ath.exe;
    // see hornlab_mesher/config_parser.py's ATH-defaults note.
    vertical_offset: 0, quadrants: 1234, wall_thickness: 5, rear_resolution: 40,
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
    length: 120,
    ...shared,
    profile_h: {
      points: [{ t: 0, r: 12.7 }, { t: 1, r: 140 }],
      throat_angle_deg: 15.5, mouth_angle_deg: 60,
    },
    profile_v: {
      points: [{ t: 0, r: 12.7 }, { t: 1, r: 140 }],
      throat_angle_deg: 15.5, mouth_angle_deg: 60,
    },
    cross_sections: [{ t: 0, shape: 'ellipse' }, { t: 1, shape: 'ellipse' }],
    inflection_policy: 'warn',
    corner_grids: [],
  };
}

const FAMILY_SPECIFIC_FIELDS = {
  OSSE: [
    'L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h', 'throat_profile',
    'rotation', 'guiding_curve', 'circ_arc_radius', 'circ_arc_term_angle',
  ],
  'R-OSSE': ['R', 'a', 'a0', 'r0', 'k', 'm', 'b', 'r', 'q', 'tmax'],
  ICW: [
    'R', 'L', 'r0', 'a0', 'a', 'k', 'q', 'coverage_angle', 'hold_start',
    'hold_end', 'n_coeff', 'termination', 'theta1_deg', 'depth', 'curl',
  ],
  FREEFORM: [
    'length', 'profile_h', 'profile_v', 'cross_sections', 'inflection_policy',
    'corner_grids',
  ],
} as const satisfies Record<DesignFamily, readonly (keyof DesignDocument)[]>;

const ALL_FAMILY_SPECIFIC_FIELDS = new Set<keyof DesignDocument>(
  Object.values(FAMILY_SPECIFIC_FIELDS).flat(),
);

interface CarriedScalar {
  value: number;
  expression?: ExprNumber;
}

interface CompatibleFamilyValues {
  throatRadius?: CarriedScalar;
  throatAngle?: CarriedScalar;
  length?: CarriedScalar;
  mouthRadius?: CarriedScalar;
  mouthAngle?: CarriedScalar;
}

function expressionAtPath(design: DesignDocument, path: string): ExprNumber | undefined {
  return Object.entries(design._expressions ?? {})
    .find(([candidate]) => resolveConcretePath(design, candidate) === path)?.[1];
}

function pathIsAbsent(design: DesignDocument, path: string): boolean {
  return (design._absent ?? [])
    .some((candidate) => resolveConcretePath(design, candidate) === path);
}

function scalarCarry(design: DesignDocument, path: string, value: number | undefined): CarriedScalar | undefined {
  if (typeof value !== 'number' || pathIsAbsent(design, path)) return undefined;
  return { value, expression: expressionAtPath(design, path) };
}

function pairedScalarCarry(
  design: DesignDocument,
  leftPath: string,
  left: number | undefined,
  rightPath: string,
  right: number | undefined,
): CarriedScalar | undefined {
  if (typeof left !== 'number' || typeof right !== 'number' || !Object.is(left, right)) return undefined;
  if (pathIsAbsent(design, leftPath) || pathIsAbsent(design, rightPath)) return undefined;
  const leftExpression = expressionAtPath(design, leftPath);
  const rightExpression = expressionAtPath(design, rightPath);
  const expression = JSON.stringify(leftExpression) === JSON.stringify(rightExpression)
    ? leftExpression
    : undefined;
  return { value: left, expression };
}

function compatibleFamilyValues(design: DesignDocument): CompatibleFamilyValues {
  if (design.formula !== 'FREEFORM') {
    return {
      throatRadius: scalarCarry(design, 'r0', design.r0),
      throatAngle: scalarCarry(design, 'a0', design.a0),
      length: design.formula === 'OSSE' || design.formula === 'ICW'
        ? scalarCarry(design, 'L', design.L)
        : undefined,
      mouthRadius: design.formula === 'R-OSSE' || design.formula === 'ICW'
        ? scalarCarry(design, 'R', design.R)
        : undefined,
      mouthAngle: design.formula === 'OSSE' || design.formula === 'R-OSSE'
        ? scalarCarry(design, 'a', design.a)
        : undefined,
    };
  }

  const horizontal = design.profile_h;
  const vertical = design.profile_v;
  const horizontalMouth = horizontal?.points.at(-1);
  const verticalMouth = vertical?.points.at(-1);
  const horizontalMouthPath = `profile_h.points.${Math.max(0, (horizontal?.points.length ?? 1) - 1)}.r`;
  const verticalMouthPath = `profile_v.points.${Math.max(0, (vertical?.points.length ?? 1) - 1)}.r`;
  return {
    throatRadius: pairedScalarCarry(
      design,
      'profile_h.points.0.r', horizontal?.points[0]?.r,
      'profile_v.points.0.r', vertical?.points[0]?.r,
    ),
    throatAngle: pairedScalarCarry(
      design,
      'profile_h.throat_angle_deg', horizontal?.throat_angle_deg,
      'profile_v.throat_angle_deg', vertical?.throat_angle_deg,
    ),
    length: scalarCarry(design, 'length', design.length),
    mouthRadius: pairedScalarCarry(
      design,
      horizontalMouthPath, horizontalMouth?.r,
      verticalMouthPath, verticalMouth?.r,
    ),
    mouthAngle: pairedScalarCarry(
      design,
      'profile_h.mouth_angle_deg', horizontal?.mouth_angle_deg,
      'profile_v.mouth_angle_deg', vertical?.mouth_angle_deg,
    ),
  };
}

function copyCarriedExpression(
  target: DesignDocument,
  carried: CarriedScalar,
  targetPaths: readonly string[],
  requireScalar = false,
): void {
  const expression = carried.expression;
  if (requireScalar && expression?.value === null) return;
  if (!expression) return;
  target._expressions = { ...target._expressions };
  targetPaths.forEach((path) => { target._expressions![path] = structuredClone(expression); });
}

function carriedValueFitsTarget(
  target: DesignDocument,
  carried: CarriedScalar,
  targetPaths: readonly string[],
): boolean {
  const resolvePath = (path: string) => resolveConcretePath(target, path);
  return targetPaths.every((path) => {
    const field = findParameterByPath(path, target.formula, resolvePath);
    if (!field) return true;
    if (field.min !== undefined && !(carried.value >= field.min)) return false;
    if (field.max !== undefined && !(carried.value <= field.max)) return false;
    return true;
  });
}

function applyCarriedScalar(
  target: DesignDocument,
  carried: CarriedScalar | undefined,
  targetPaths: readonly string[],
  assign: (value: number) => void,
  requireScalar = false,
): void {
  if (!carried || !carriedValueFitsTarget(target, carried, targetPaths)) return;
  assign(carried.value);
  copyCarriedExpression(target, carried, targetPaths, requireScalar);
}

function applyCompatibleFamilyValues(
  target: DesignDocument,
  compatible: CompatibleFamilyValues,
): void {
  if (target.formula === 'FREEFORM') {
    const horizontalMouthIndex = target.profile_h!.points.length - 1;
    const verticalMouthIndex = target.profile_v!.points.length - 1;
    applyCarriedScalar(target, compatible.length, ['length'], (value) => {
      target.length = value;
    }, true);
    applyCarriedScalar(target, compatible.throatRadius, [
      'profile_h.points.0.r', 'profile_v.points.0.r',
    ], (value) => {
      target.profile_h!.points[0].r = value;
      target.profile_v!.points[0].r = value;
    });
    applyCarriedScalar(target, compatible.throatAngle, [
      'profile_h.throat_angle_deg', 'profile_v.throat_angle_deg',
    ], (value) => {
      target.profile_h!.throat_angle_deg = value;
      target.profile_v!.throat_angle_deg = value;
    });
    applyCarriedScalar(target, compatible.mouthRadius, [
      `profile_h.points.${horizontalMouthIndex}.r`,
      `profile_v.points.${verticalMouthIndex}.r`,
    ], (value) => {
      target.profile_h!.points[horizontalMouthIndex].r = value;
      target.profile_v!.points[verticalMouthIndex].r = value;
    });
    applyCarriedScalar(target, compatible.mouthAngle, [
      'profile_h.mouth_angle_deg', 'profile_v.mouth_angle_deg',
    ], (value) => {
      target.profile_h!.mouth_angle_deg = value;
      target.profile_v!.mouth_angle_deg = value;
    });
    return;
  }

  applyCarriedScalar(target, compatible.throatRadius, ['r0'], (value) => {
    target.r0 = value;
  });
  applyCarriedScalar(target, compatible.throatAngle, ['a0'], (value) => {
    target.a0 = value;
  });
  if (target.formula === 'OSSE' || target.formula === 'ICW') {
    applyCarriedScalar(target, compatible.length, ['L'], (value) => {
      target.L = value;
    });
  }
  if (target.formula === 'R-OSSE' || target.formula === 'ICW') {
    applyCarriedScalar(target, compatible.mouthRadius, ['R'], (value) => {
      target.R = value;
    });
  }
  if (target.formula === 'OSSE' || target.formula === 'R-OSSE') {
    applyCarriedScalar(target, compatible.mouthAngle, ['a'], (value) => {
      target.a = value;
    });
  }
}

function pathBelongsToSharedState(path: string): boolean {
  const root = path.split('.')[0] as keyof DesignDocument;
  return root !== 'formula' && !ALL_FAMILY_SPECIFIC_FIELDS.has(root);
}

/** Merge a family template with the current document's non-formula state. */
export function mergeDesignForFamily(current: DesignDocument, family: DesignFamily): DesignDocument {
  if (current.formula === family) return structuredClone(current);

  const compatible = compatibleFamilyValues(current);
  const shared: Partial<DesignDocument> = structuredClone(current);
  delete shared.formula;
  ALL_FAMILY_SPECIFIC_FIELDS.forEach((field) => { delete shared[field]; });

  const expressions = Object.fromEntries(Object.entries(current._expressions ?? {})
    .filter(([path]) => pathBelongsToSharedState(path))
    .map(([path, expression]) => [path, structuredClone(expression)]));
  if (Object.keys(expressions).length) shared._expressions = expressions;
  else delete shared._expressions;
  const absent = (current._absent ?? []).filter(pathBelongsToSharedState);
  if (absent.length) shared._absent = absent;
  else delete shared._absent;

  const next = { ...designForFamily(family), ...shared, formula: family } as DesignDocument;
  applyCompatibleFamilyValues(next, compatible);
  return next;
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
  updateExpression: (path: string, expression: ExprNumber) => void;
  setQuadrants: (quadrants: number[]) => void;
  setSourceConvention: (convention: DesignDocument['source']['velocity_convention']) => void;
  setFamily: (family: DesignFamily) => void;
  loadDesign: (design: DesignDocument) => void;
  replaceDesign: (design: DesignDocument, options?: { keepHistory?: boolean }) => void;
  beginDrag: () => void;
  endDrag: () => void;
  undo: () => void;
  redo: () => void;
}

function setAtPath(design: DesignDocument, path: string, value: DesignValue): DesignDocument {
  const next = structuredClone(design);
  const parts = path.split('.');
  let cursor: Record<string, unknown> | unknown[] = next as unknown as Record<string, unknown>;
  for (let index = 0; index < parts.length - 1; index += 1) {
    const part = parts[index] === '$last' && Array.isArray(cursor) ? String(cursor.length - 1) : parts[index];
    const child = cursor[part as keyof typeof cursor];
    if (typeof child !== 'object' || child === null) throw new Error(`Unknown design path: ${path}`);
    cursor = child as Record<string, unknown> | unknown[];
  }
  const last = parts.at(-1) === '$last' && Array.isArray(cursor) ? String(cursor.length - 1) : parts.at(-1)!;
  (cursor as Record<string, unknown>)[last] = value;
  if (path.startsWith('enclosure.space_')) next.enclosure.baffle_margin = Number(value);
  return next;
}

function pathsOverlap(left: string, right: string): boolean {
  return left === right || left.startsWith(`${right}.`) || right.startsWith(`${left}.`);
}

function withoutPreservedMetadata(design: DesignDocument, path: string): DesignDocument {
  const concretePath = resolveConcretePath(design, path);
  const editedPaths = [path, concretePath];
  const expressionMatches = Object.keys(design._expressions ?? {}).filter((key) => editedPaths.some((edited) => pathsOverlap(key, edited)));
  const absentMatches = (design._absent ?? []).filter((key) => editedPaths.some((edited) => pathsOverlap(key, edited)));
  if (!expressionMatches.length && !absentMatches.length) return design;
  const next = structuredClone(design);
  expressionMatches.forEach((key) => { delete next._expressions?.[key]; });
  if (next._expressions && Object.keys(next._expressions).length === 0) delete next._expressions;
  if (absentMatches.length) {
    const matched = new Set(absentMatches);
    next._absent = next._absent?.filter((key) => !matched.has(key));
    if (!next._absent?.length) delete next._absent;
  }
  return next;
}

function resolveConcretePath(design: DesignDocument, path: string): string {
  const resolved: string[] = [];
  let cursor: unknown = design;
  path.split('.').forEach((part) => {
    const key = part === '$last' && Array.isArray(cursor) ? String(cursor.length - 1) : part;
    resolved.push(key);
    cursor = typeof cursor === 'object' && cursor !== null ? (cursor as Record<string, unknown>)[key] : undefined;
  });
  return resolved.join('.');
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
          design: withoutPreservedMetadata(setAtPath(state.design, path, value), path),
          designRevision: state.designRevision + 1,
        }));
        bump(get().dragSnapshot ? 'drag' : 'edit', false);
      },
      updateValues: (updates) => {
        set((state) => ({
          design: Object.keys(updates).reduce((next, path) => withoutPreservedMetadata(next, path), setMany(state.design, updates)),
          designRevision: state.designRevision + 1,
        }));
        bump(get().dragSnapshot ? 'drag' : 'edit', false);
      },
      updateExpression: (path, expression) => {
        set((state) => {
          let design = withoutPreservedMetadata(structuredClone(state.design), path);
          if (expression.value !== null) design = setAtPath(design, path, expression.value);
          design._expressions = { ...design._expressions, [path]: structuredClone(expression) };
          return { design, designRevision: state.designRevision + 1 };
        });
        bump('edit', false);
      },
      setQuadrants: (quadrants) => {
        const sorted = [...quadrants].sort();
        set((state) => {
          const design = withoutPreservedMetadata(state.design, 'mesh.quadrants');
          return {
            design: {
              ...design,
              quadrants: sorted,
              mesh: { ...design.mesh, quadrants: encodeQuadrants(sorted) },
            },
            designRevision: state.designRevision + 1,
          };
        });
        bump('edit', false);
      },
      setSourceConvention: (velocity_convention) => {
        set((state) => {
          const design = withoutPreservedMetadata(state.design, 'source.velocity_convention');
          return {
            design: { ...design, source: { ...design.source, velocity_convention } },
            designRevision: state.designRevision + 1,
          };
        });
        bump('edit', false);
      },
      setFamily: (family) => {
        cancelRevisionTimers();
        get().endDrag();
        set((state) => ({ design: mergeDesignForFamily(state.design, family), designRevision: state.designRevision + 1 }));
        bump('family', true);
      },
      loadDesign: (design) => {
        cancelRevisionTimers();
        get().endDrag();
        set((state) => ({ design: structuredClone(design), designRevision: state.designRevision + 1 }));
        bump('load', true);
      },
      replaceDesign: (design, options) => {
        cancelRevisionTimers();
        get().endDrag();
        set((state) => ({ design: structuredClone(design), designRevision: state.designRevision + 1 }));
        // Opening a file starts a new document, so its history starts empty.
        // Browsing to a previous run does not: that is a step inside the current
        // session, and undo has to be able to bring the working design back.
        if (!options?.keepHistory) useDesignStore.temporal.getState().clear();
        useDesignStore.temporal.getState().resume();
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
        const snapshot = get().dragSnapshot;
        if (!snapshot && !useDesignStore.temporal.getState().pastStates.length) return;
        cancelRevisionTimers();
        if (snapshot) {
          useDesignStore.temporal.getState().resume();
          set((state) => ({ design: snapshot, dragSnapshot: null, designRevision: state.designRevision + 1 }));
        } else {
          useDesignStore.temporal.getState().undo();
          set((state) => ({ designRevision: state.designRevision + 1 }));
        }
        bump('undo', true);
      },
      redo: () => {
        if (!useDesignStore.temporal.getState().futureStates.length) return;
        cancelRevisionTimers();
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
  bump('load', true);
}

/**
 * Record the ATH polar blocks that were actually committed without creating a
 * geometry revision. Directivity changes do not alter the preview mesh; this
 * sidecar update keeps CAD-link hashing and the next CAD-linked export aligned
 * with the committed `.cfg` while avoiding a pointless coarse/fine geometry rebuild.
 */
export function recordCommittedAthPolars(polarConfig: unknown): void {
  const state = useDesignStore.getState();
  const extraBlocks = replaceAthPolarBlocks(state.design.extra_blocks, polarConfig);
  if (extraBlocks === null) return;
  useDesignStore.setState({
    design: { ...state.design, extra_blocks: extraBlocks },
  });
}

/**
 * Serialize a DesignDocument into the exact shape server/design/schema.py's
 * DesignConfig accepts. Owns the mirror-stripping rules so every send path
 * (preview WS, solve submit) stays schema-valid:
 * - top-level `quadrants` mirror -> mesh.quadrants ATH digit list
 * - `enclosure.baffle_margin` is UI-only; individual space_* fields are authoritative
 * - `guiding_curve` is OSSE-only in the schema (ROSSEConfig/ICWConfig/
 *   FreeformConfig forbid it) and is dropped for every other family.
 */
export function serializeDesign(design: DesignDocument): Record<string, unknown> {
  const { quadrants, enclosure, mesh, _expressions, _absent, ...root } = structuredClone(design);
  const wireEnclosure = {
    depth: enclosure.depth,
    edge_radius: enclosure.edge_radius,
    edge_type: enclosure.edge_type,
    space_l: enclosure.space_l,
    space_t: enclosure.space_t,
    space_r: enclosure.space_r,
    space_b: enclosure.space_b,
    front_resolution: enclosure.front_resolution,
    back_resolution: enclosure.back_resolution,
  };
  const payload: Record<string, unknown> = {
    ...root,
    mesh: {
      ...mesh,
      quadrants: encodeQuadrants(quadrants),
    },
    enclosure: wireEnclosure,
  };
  if (design.formula !== 'OSSE') delete payload.guiding_curve;
  Object.entries(_expressions ?? {}).forEach(([path, expression]) => {
    setWirePath(payload, path, expression);
  });
  (_absent ?? []).forEach((path) => setWirePath(payload, path, null));
  // Solver path is a machine execution preference. Legacy files are still
  // parsed into the design model, but every outgoing portable design omits it;
  // solve submission carries the current machine-local value in options.
  const simulation = payload.simulation;
  if (simulation && typeof simulation === 'object' && !Array.isArray(simulation)) {
    delete (simulation as Record<string, unknown>).solver_mode;
  }
  return payload;
}

/** Keep save round-trips lossless while ensuring a solve runs the sweep shown in the panel. */
export function serializeSolveDesign(design: DesignDocument): Record<string, unknown> {
  const payload = serializeDesign(design);
  const absent = new Set(design._absent ?? []);
  (['f1', 'f2', 'num_frequencies'] as const).forEach((key) => {
    if (absent.has(`simulation.${key}`)) setWirePath(payload, `simulation.${key}`, design.simulation[key]);
  });
  return payload;
}

function setWirePath(root: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.split('.');
  let cursor: Record<string, unknown> | unknown[] = root;
  for (let index = 0; index < parts.length - 1; index += 1) {
    const part = parts[index] === '$last' && Array.isArray(cursor) ? String(cursor.length - 1) : parts[index];
    const child = cursor[part as keyof typeof cursor];
    if (!child || typeof child !== 'object') return;
    cursor = child as Record<string, unknown> | unknown[];
  }
  const last = parts.at(-1) === '$last' && Array.isArray(cursor) ? String(cursor.length - 1) : parts.at(-1)!;
  (cursor as Record<string, unknown>)[last] = structuredClone(value);
}

/** ATH represents domains as concatenated quadrant digits, never bit flags. */
export function encodeQuadrants(quadrants: readonly number[]): number {
  const digits = [...new Set(quadrants)]
    .filter((quadrant) => Number.isInteger(quadrant) && quadrant >= 1 && quadrant <= 4)
    .sort((left, right) => left - right);
  if (!digits.length) throw new Error('At least one quadrant is required');
  return Number(digits.join(''));
}

export function decodeQuadrants(value: unknown): number[] {
  const digits = String(value ?? '').trim().split('').map(Number);
  const quadrants = [...new Set(digits.filter((digit) => digit >= 1 && digit <= 4))]
    .sort((left, right) => left - right);
  return quadrants.length ? quadrants : [1, 2, 3, 4];
}
