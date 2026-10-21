/** Solver contract. Coordinates and lengths are mm; angles are radians. */
/** Maximum solver entities (points and curves) for per-point determination. */
export const DETERMINATION_ENTITY_LIMIT = 64;
export interface XY { x: number; y: number }
export interface Point extends XY { kind: 'point'; id: string; fixed?: boolean }
export interface Line { kind: 'line'; id: string; p1: string; p2: string }
/** CCW, with an explicit direction; endpoints must never be exchanged by an adapter. */
export interface Arc { kind: 'arc'; id: string; center: string; start: string; end: string; direction: 'ccw' }
export type Entity = Point | Line | Arc | { kind: 'circle' | 'ellipse' | 'bspline'; id: string };
type Pair = { a: string; b: string };
type Dimension = { value: number; driving: boolean };
export type Constraint = { id: string } & (
  | ({ kind: 'coincident' } & Pair)
  | ({ kind: 'horizontal' | 'vertical' } & ({ line: string } | Pair))
  | ({ kind: 'parallel' | 'perpendicular' | 'equal' } & Pair)
  /** Tangency of supporting curves; at shared endpoints for SolveSpace. */
  | ({ kind: 'tangent' } & Pair)
  | { kind: 'midpoint' | 'pointOnLine'; point: string; line: string }
  /** On the finite CCW span, including endpoints. */
  | { kind: 'pointOnArc'; point: string; arc: string }
  | { kind: 'fix'; point: string }
  | ({ kind: 'distance' | 'distanceX' | 'distanceY' | 'angle' } & Pair & Dimension)
  /** Unsigned distance to the infinite supporting line. */
  | ({ kind: 'pointLineDistance'; point: string; line: string } & Dimension)
  | ({ kind: 'radius'; arc: string } & Dimension)
);
export type ConstraintKind = Constraint['kind'];
export const constraintKinds: ConstraintKind[] = ['coincident', 'horizontal', 'vertical', 'parallel',
  'perpendicular', 'tangent', 'equal', 'midpoint', 'pointOnLine', 'pointOnArc', 'fix',
  'distance', 'distanceX', 'distanceY', 'pointLineDistance', 'angle', 'radius'];
export interface Sketch { entities: Entity[]; constraints: Constraint[] }
export type DragTarget = { pointId: string } & XY;
/** tolerance: acceptable cursor error in mm for drag frames (default exact, about 1e-8). */
export interface SolveOptions { mode: 'drag' | 'full'; dragTargets?: DragTarget[]; tolerance?: number }
export interface SolveResult {
  status: 'ok' | 'failed' | 'unsupported'; points: Record<string, XY>; iterations?: number;
  pointDetermined?: Record<string, boolean>;
  dof?: number; conflicting?: string[]; redundant?: string[]; partiallyRedundant?: string[];
  /** Full mode: the solver detected redundancy, even when it cannot attribute it to user constraints. */
  hasRedundancy?: boolean;
  timings: { totalMs: number; updateMs: number; solveMs: number; readbackMs: number; marshalMs: number };
  /** Explains unsupported input, without inventing solver diagnostics. */
  reason?: string;
  /** Unmodified package status, when available. */
  nativeStatus?: string | number;
}
export interface Capability { support: 'native' | 'emulated' | 'unsupported'; detail: string }
export interface Capabilities {
  constraints: Record<ConstraintKind, Capability>;
  diagnostics: Record<'dof' | 'conflicting' | 'redundant' | 'partiallyRedundant', Capability>;
  drag: string; referenceDimensions: string;
}
export const solverNames = ['planegcs', 'slvs'] as const;
export type SolverName = typeof solverNames[number];
export interface SketchSolver {
  load(sketch: Sketch): void;
  solve(options: SolveOptions): SolveResult;
  /** Build once. load() or solve() cancels an active session. */
  beginDrag(pointIds: string[]): void;
  /** Targets must name every session point exactly once. */
  dragTo(targets: DragTarget[], options?: { tolerance?: number }): SolveResult;
  /** Remove temporary goals and return full sketch diagnosis. */
  endDrag(): SolveResult;
  capabilities(): Capabilities;
  /** Current WASM heap capacity, not live allocations. */
  memoryBytes(): number | undefined;
  destroy(): void;
}
/** No package or WASM is loaded until this function is called. */
export async function createSolver(name: SolverName): Promise<SketchSolver> {
  switch (name) {
    case 'planegcs': return (await import('./planegcs')).create();
    case 'slvs': return (await import('./slvs')).create();
  }
}
export class Unsupported extends Error { override name = 'Unsupported' }
