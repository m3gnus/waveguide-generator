import { constraintKinds, type Constraint as SolverConstraint } from './solver/types';

export type JSONValue = null | boolean | number | string | JSONValue[] | { [key: string]: JSONValue };
export interface Point { id: string; x: number; y: number }
type Base = { id: string; reference?: boolean; construction?: boolean; data?: JSONValue };
export type Line = Base & { kind: 'line'; p1: string; p2: string };
export type Arc = Base & { kind: 'arc'; center: string; start: string; end: string; ccw: boolean };
export type Entity = Line | Arc | (Base & { kind: 'circle' | 'ellipse' | 'bspline' });
/** Distances use a signed world-mm perpendicular offset and an unbounded fraction.
 * Radius labels use a world angle (radians) and an offset from the arc radius. */
export type DimensionPlacement = { offset: number; along: number } | { offset: number; angle: number };
export type Constraint = SolverConstraint & { label?: string; placement?: DimensionPlacement };
export type NewConstraint = Constraint extends infer C ? C extends Constraint ? Omit<C, 'id'> : never : never;
export interface Sketch {
  version: 'wg-sketch-v1'; points: Point[]; entities: Entity[]; constraints: Constraint[]; nextId: number;
}
export type Validation = { ok: true } | { ok: false; reason: string };
export const emptySketch = (): Sketch => ({ version: 'wg-sketch-v1', points: [], entities: [], constraints: [], nextId: 1 });
export const entityPoints = (e: Entity): string[] => e.kind === 'line' ? [e.p1, e.p2] : e.kind === 'arc' ? [e.center, e.start, e.end] : [];
export function constraintRefs(c: Constraint): string[] {
  return Object.entries(c).flatMap(([key, value]) => ['a', 'b', 'point', 'line', 'arc'].includes(key) ? [value as string] : []);
}
export function numericSuffix(id: string): number { return Number(id.match(/\d+$/)?.[0] ?? 0); }
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const id = (v: unknown): v is string => typeof v === 'string' && v.length > 0 && v.trim() === v;
export function isJSON(v: unknown, seen = new Set<unknown>()): v is JSONValue {
  if (v === null || typeof v === 'boolean' || typeof v === 'string') return true;
  if (typeof v === 'number') return Number.isFinite(v);
  if (typeof v !== 'object' || seen.has(v)) return false;
  if (!Array.isArray(v) && Object.getPrototypeOf(v) !== Object.prototype && Object.getPrototypeOf(v) !== null) return false;
  seen.add(v);
  const ok = Object.values(v).every(value => isJSON(value, seen)); seen.delete(v); return ok;
}
export function constraintProblem(s: Sketch, c: Constraint | NewConstraint): string | undefined {
  const point = (v: string) => s.points.some(p => p.id === v);
  const kind = (v: string) => s.entities.find(e => e.id === v)?.kind;
  const pair = (k: 'point' | 'line') => 'a' in c && 'b' in c && (k === 'point' ? point(c.a) && point(c.b) : kind(c.a) === k && kind(c.b) === k);
  let valid = false;
  switch (c.kind) {
    case 'coincident': case 'distance': case 'distanceX': case 'distanceY': valid = pair('point'); break;
    case 'horizontal': case 'vertical': valid = 'line' in c ? kind(c.line) === 'line' : pair('point'); break;
    case 'parallel': case 'perpendicular': case 'angle': valid = pair('line'); break;
    case 'equal': valid = ['line', 'arc'].includes(kind(c.a) ?? '') && kind(c.a) === kind(c.b); break;
    case 'tangent': valid = ['line', 'arc'].includes(kind(c.a) ?? '') && ['line', 'arc'].includes(kind(c.b) ?? '') && (kind(c.a) === 'arc' || kind(c.b) === 'arc'); break;
    case 'fix': valid = point(c.point); break;
    case 'midpoint': case 'pointOnLine': case 'pointLineDistance': valid = point(c.point) && kind(c.line) === 'line'; break;
    case 'pointOnArc': valid = point(c.point) && kind(c.arc) === 'arc'; break;
    case 'radius': valid = kind(c.arc) === 'arc'; break;
  }
  if (c.label !== undefined && typeof c.label !== 'string') return 'Constraint labels must be strings.';
  if (c.placement !== undefined) {
    const p = c.placement;
    if (!('value' in c) || !object(p) || !Number.isFinite(p.offset)
      || (c.kind === 'radius' ? !('angle' in p) || !Number.isFinite(p.angle) : !('along' in p) || !Number.isFinite(p.along))) return 'Invalid dimension placement.';
  }
  if (!valid) return `Invalid entity kinds or references for ${c.kind}.`;
  if (['distance', 'distanceX', 'distanceY', 'angle', 'pointLineDistance', 'radius'].includes(c.kind)
    && (!('value' in c) || !Number.isFinite(c.value) || typeof c.driving !== 'boolean')) return 'Dimensions need a finite value and a driving flag.';
  if ('value' in c && ['distance', 'radius', 'pointLineDistance'].includes(c.kind) && c.value < 0) return 'This dimension cannot be negative.';
}
export function validateSketch(raw: unknown): Validation {
  const fail = (reason: string): Validation => ({ ok: false, reason });
  if (!object(raw) || raw.version !== 'wg-sketch-v1') return fail('Unknown sketch version.');
  if (!isJSON(raw)) return fail('Sketch must contain plain finite JSON.');
  if (!Array.isArray(raw.points) || !Array.isArray(raw.entities) || !Array.isArray(raw.constraints)) return fail('Expected sketch arrays.');
  if (!Number.isSafeInteger(raw.nextId) || Number(raw.nextId) < 1) return fail('Invalid nextId.');
  const ids = new Set<string>();
  for (const item of [...raw.points, ...raw.entities, ...raw.constraints]) {
    if (!object(item) || !id(item.id) || ids.has(item.id)) return fail('IDs must be nonempty and unique.');
    ids.add(item.id);
    if (numericSuffix(item.id) >= Number(raw.nextId)) return fail('nextId must be ahead of every allocated ID.');
  }
  const s = raw as unknown as Sketch;
  for (const p of s.points) if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) return fail(`Invalid point ${p.id}.`);
  for (const e of s.entities) {
    if (e.kind !== 'line' && e.kind !== 'arc') return fail(`Unsupported entity kind: ${e.kind}.`);
    if (e.construction !== undefined && typeof e.construction !== 'boolean') return fail(`Invalid construction flag: ${e.id}.`);
    if (e.kind === 'arc' && typeof e.ccw !== 'boolean') return fail(`Arc direction missing: ${e.id}.`);
    if (entityPoints(e).some(v => !s.points.some(p => p.id === v))) return fail(`Unknown point on ${e.id}.`);
  }
  for (const c of s.constraints) {
    if (!constraintKinds.includes(c.kind)) return fail(`Unknown constraint kind: ${c.kind}.`);
    const problem = constraintProblem(s, c); if (problem) return fail(problem);
  }
  return { ok: true };
}
export type ParsedSketch = { ok: true; sketch: Sketch } | { ok: false; reason: string; raw: unknown; unknownVersion?: true };
export function parseSketch(raw: unknown): ParsedSketch {
  const original = raw;
  try {
    if (typeof raw === 'string') raw = JSON.parse(raw);
    if (object(raw) && raw.version !== 'wg-sketch-v1') return { ok: false, reason: 'Unknown sketch version.', raw: original, unknownVersion: true };
    const result = validateSketch(raw);
    return result.ok ? { ok: true, sketch: raw as Sketch } : { ...result, raw: original };
  } catch { return { ok: false, reason: 'Invalid sketch JSON.', raw: original }; }
}
export const toJSON = (sketch: Sketch): string => JSON.stringify(sketch);
