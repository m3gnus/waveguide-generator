import type { Constraint, Sketch } from './model';

/** The only conversion boundary between solver quantities and dimension entry. */
export function displayValue(c: Constraint & { value: number }): number {
  return c.value * (c.kind === 'angle' ? 180 / Math.PI : c.diameter ? 2 : 1);
}
export function internalValue(c: Constraint, display: number): number {
  return display / (c.kind === 'angle' ? 180 / Math.PI : c.diameter ? 2 : 1);
}
export function supportsDiameter(s: Sketch, c: Constraint): boolean {
  const axis = s.entities.filter(e => e.kind === 'line' && e.reference && (e.id === 'axis'
    || e.data && typeof e.data === 'object' && !Array.isArray(e.data) && e.data.name === 'Axis of revolution'));
  if (c.kind === 'radius') return true;
  if (c.kind === 'pointLineDistance') return axis.some(e => e.id === c.line);
  const onAxis = (id: string) => axis.some(e => e.kind === 'line' && ([e.p1, e.p2].includes(id)
    || s.constraints.some(c => c.kind === 'pointOnLine' && c.point === id && c.line === e.id)));
  return c.kind === 'distanceX' && (onAxis(c.a) || onAxis(c.b));
}
