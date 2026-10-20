/** Shared hit policy: points/markers, curves, then keep-out bands; nearest within each class. */
export function geometryHit<T extends { kind: string; role?: string; gap: number }>(hits: T[], tolerance: number): T | undefined {
  const priority = (hit: T) => hit.role === 'keepout' ? 2 : hit.kind === 'point' ? 0 : 1;
  return hits.filter(hit => hit.gap <= tolerance).sort((a, b) => priority(a) - priority(b) || a.gap - b.gap)[0];
}
