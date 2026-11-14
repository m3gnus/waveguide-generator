import { constraintRefs, entityPoints, type Entity, type NewConstraint, type Sketch } from './model';

export type SharpData = { point: string; points: string[]; support: string; contact: string; constraint?: string };
export function virtualSharpData(e: Entity): SharpData | undefined {
  const data = e.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return;
  const v = data.virtualSharp;
  if (!v || typeof v !== 'object' || Array.isArray(v) || typeof v.point !== 'string'
    || typeof v.support !== 'string' || typeof v.contact !== 'string' || !Array.isArray(v.points)
    || !v.points.every(p => typeof p === 'string') || v.constraint !== undefined && typeof v.constraint !== 'string') return;
  return v as SharpData;
}
export const isVirtualSharpHelper = (e: Entity) => !!virtualSharpData(e);
export const virtualSharpPointIds = (s: Sketch) => new Set(s.entities.flatMap(e => virtualSharpData(e)?.points ?? []));
/** A virtual sharp is one corner: its point and every coincident alias it absorbed. */
export const virtualSharpAliases = (s: Sketch, id: string) =>
  new Set(s.entities.map(virtualSharpData).find(v => v?.points.includes(id))?.points ?? [id]);
export const virtualSharpConstraintIds = (s: Sketch) => new Set(s.entities.flatMap(e => virtualSharpData(e)?.constraint ? [virtualSharpData(e)!.constraint!] : []));

/** Preset dimensions still describe the original supporting-curve endpoint. */
export function virtualSharpEndpoint(s: Sketch, support: string, actual: string, original: string): boolean {
  return actual === original || s.entities.some(e => {
    const v = virtualSharpData(e);
    return v?.support === support && v.contact === actual && v.points.includes(original);
  });
}

/** Helper lifecycle at the command boundary; internal equations never keep a sharp alive. */
export function pruneVirtualSharps(s: Sketch, addConstraint?: (c: NewConstraint) => string): string[] {
  const removed: string[] = [];
  const groups = new Map<string, Entity[]>();
  for (const e of s.entities) {
    const v = virtualSharpData(e); if (v) groups.set(v.point, [...groups.get(v.point) ?? [], e]);
  }
  for (const helpers of groups.values()) {
    const data = helpers.map(e => virtualSharpData(e)!);
    const points = new Set(data.flatMap(v => v.points)), owned = new Set(data.flatMap(v => v.constraint ? [v.constraint] : []));
    const user = s.constraints.some(c => !owned.has(c.id) && constraintRefs(c).some(id => points.has(id))
      && !(c.kind === 'coincident' && points.has(c.a) && points.has(c.b)));
    if (user && helpers.every((helper, i) => s.entities.some(e => e.id === data[i].support && e.kind === helper.kind))) {
      // Reuse a user's support relation while it exists. If they delete it,
      // replace just its internal equation while the other dimensions survive.
      if (addConstraint) for (const helper of helpers) {
        const v = virtualSharpData(helper)!;
        if (helper.kind !== 'line' || s.constraints.some(c => c.kind === 'pointOnLine' && c.line === v.support && points.has(c.point))) continue;
        const constraint = addConstraint({ kind: 'pointOnLine', point: v.point, line: v.support });
        s.entities = s.entities.map(e => e.id === helper.id ? { ...e, data: { virtualSharp: { ...v, constraint } } } : e);
      }
      continue;
    }
    const ids = new Set(helpers.map(e => e.id));
    s.entities = s.entities.filter(e => !ids.has(e.id));
    // A sharp whose support was deleted or changed kind cannot hold its corner: its relations go with it.
    s.constraints = s.constraints.filter(c => {
      const drop = owned.has(c.id) || (user ? constraintRefs(c).some(id => points.has(id)) : c.kind === 'coincident' && points.has(c.a) && points.has(c.b));
      if (drop) removed.push(c.id); return !drop;
    });
    const used = new Set([...s.entities.flatMap(entityPoints), ...s.constraints.flatMap(constraintRefs)]);
    s.points = s.points.filter(p => !points.has(p.id) || used.has(p.id));
  }
  return removed;
}
