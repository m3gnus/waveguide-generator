import type { Sketch } from './solver/types';

/** Endpoint equivalence comes from topology or explicit coincidences, never a distance tolerance. */
export function coincidentPoints(s: Pick<Sketch, 'constraints'>, id: string): Set<string> {
  const reached = new Set([id]); let changed = true;
  while (changed) {
    changed = false;
    for (const c of s.constraints) if (c.kind === 'coincident') {
      if (reached.has(c.a) && !reached.has(c.b)) { reached.add(c.b); changed = true; }
      if (reached.has(c.b) && !reached.has(c.a)) { reached.add(c.a); changed = true; }
    }
  }
  return reached;
}
export function equivalent(s: Pick<Sketch, 'constraints'>, a: string, b: string): boolean {
  return coincidentPoints(s, a).has(b);
}
