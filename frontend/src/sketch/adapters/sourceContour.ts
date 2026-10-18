import type { SourceDocument, SourcePatch } from '../../api/sourceEditor';
import type { DomainProblem, Lowered, Reference, SketchAdapter } from '../adapter';
import { entityPoints, validateSketch, type Entity, type Sketch } from '../model';
import { applyCommand, importedSketch } from '../ops';
import type { Command } from '../commands';
import { arcGeometry } from '../geometry';

export interface SourceMeta {
  physical_source_id: string; rim_id: string; channel_id: string; motion: 'normal' | 'axial';
  weights: Record<string, number>; references: Reference[]; endpoints: { pole: string; rim: string };
}
export const references: Reference[] = [{ id: 'axis', kind: 'axis' }, { id: 'baffle', kind: 'baffle' }];
export function seed(document: SourceDocument): { sketch: Sketch; meta: SourceMeta } {
  const { contour, drive } = document;
  const points = contour.points.map(p => ({ id: p.id, x: p.r_mm, y: p.z_mm }));
  const entities: Entity[] = contour.segments.map(e => e.kind === 'line'
    ? { kind: 'line', id: e.id, p1: e.start, p2: e.end, data: { role: e.role } }
    : { kind: 'arc', id: e.id, center: e.start, start: e.start, end: e.end, ccw: e.direction === 'ccw', data: { role: e.role } });
  let sketch = importedSketch(points, entities);
  const run = (command: Command) => {
    const result = applyCommand(sketch, command); if ('error' in result) throw new Error(result.error);
    sketch = result.sketch; return result.created;
  };
  for (const e of contour.segments) if (e.kind === 'arc') {
    if (!e.center_mm) throw new Error(`Missing arc center: ${e.id}.`);
    const [center] = run({ kind: 'addPoint', x: e.center_mm[0], y: e.center_mm[1] });
    sketch = { ...sketch, entities: sketch.entities.map(a => a.id === e.id && a.kind === 'arc' ? { ...a, center } : a) };
  }
  const declared = references.map(ref => {
    const used = [...sketch.points, ...sketch.entities, ...sketch.constraints].some(e => e.id === ref.id);
    const [p1] = run({ kind: 'addPoint', x: 0, y: 0 });
    const [p2] = run({ kind: 'addPoint', x: ref.kind === 'axis' ? 0 : 1, y: ref.kind === 'axis' ? 1 : 0 });
    const [id] = run({ kind: 'addLine', from: p1, to: p2, ...(used ? {} : { id: ref.id }) });
    run({ kind: 'setConstruction', id, construction: true }); return { ...ref, id };
  });
  if (contour.points.length) {
    run({ kind: 'addConstraint', constraint: { kind: 'pointOnLine', point: contour.points[0].id, line: declared[0].id } });
    run({ kind: 'addConstraint', constraint: { kind: 'pointOnLine', point: contour.points.at(-1)!.id, line: declared[1].id } });
  }
  return { sketch, meta: { physical_source_id: contour.physical_source_id, rim_id: contour.rim_id,
    channel_id: drive.channel_id, motion: drive.motion, weights: { ...drive.weights }, references: declared,
    endpoints: { pole: contour.points[0]?.id ?? '', rim: contour.points.at(-1)?.id ?? '' } } };
}
type Chain = { points: string[]; entities: { entity: Entity; reversed: boolean }[] };
function chain(sketch: Sketch, meta: SourceMeta): Chain | DomainProblem {
  const entities = sketch.entities.filter(e => !e.construction), ids = entities.map(e => e.id);
  const problem = (): DomainProblem => ({ code: 'chain', ids, message: 'Use one open chain from the axis to the baffle.' });
  if (!entities.length || entities.some(e => e.kind !== 'line' && e.kind !== 'arc')) return problem();
  const parent = new Map(sketch.points.map(p => [p.id, p.id]));
  const root = (id: string): string => { const next = parent.get(id); return next && next !== id ? root(next) : id; };
  for (const c of sketch.constraints) if (c.kind === 'coincident') {
    const a = root(c.a), b = root(c.b);
    const first = sketch.points.find(p => p.id === a || p.id === b)?.id;
    if (first) parent.set(first === a ? b : a, first);
  }
  const ends = (e: Entity) => (e.kind === 'line' ? [e.p1, e.p2] : e.kind === 'arc' ? [e.start, e.end] : []).map(root);
  const adjacent = new Map<string, Entity[]>();
  for (const e of entities) for (const id of ends(e)) adjacent.set(id, [...adjacent.get(id) ?? [], e]);
  if ([...adjacent.values()].some(a => a.length > 2)) return problem();
  const tips = [...adjacent].filter(([, es]) => es.length === 1).map(([id]) => id);
  if (tips.length !== 2 || !tips.includes(root(meta.endpoints.pole)) || !tips.includes(root(meta.endpoints.rim))) return problem();
  const axis = meta.references.find(r => r.kind === 'axis')?.id;
  const locked = sketch.constraints.filter(c => c.kind === 'pointOnLine' && c.line === axis).map(c => root('point' in c ? c.point : ''));
  const poles = tips.filter(id => locked.length ? locked.includes(id) : sketch.points.find(p => p.id === id)?.x === 0);
  if (poles.length !== 1) return problem();
  const result: Chain = { points: [poles[0]], entities: [] }, visited = new Set<string>();
  let cursor = poles[0];
  while (true) {
    const next = adjacent.get(cursor)?.filter(e => !visited.has(e.id));
    if (!next?.length) break;
    if (next.length !== 1) return problem();
    const e = next[0], [a, b] = ends(e), reversed = a !== cursor;
    visited.add(e.id); cursor = reversed ? a : b;
    result.entities.push({ entity: e, reversed }); result.points.push(cursor);
  }
  if (visited.size !== entities.length) return problem();
  const used = new Set(entities.flatMap(entityPoints));
  const refPoints = new Set(sketch.entities.filter(e => e.construction).flatMap(entityPoints));
  if (sketch.points.some(p => !used.has(p.id) && !refPoints.has(p.id))) return problem();
  return result;
}
const role = (e: Entity): unknown => e.data && typeof e.data === 'object' && !Array.isArray(e.data) ? e.data.role ?? 'moving' : 'moving';
export function rules(sketch: Sketch, meta: SourceMeta): DomainProblem[] {
  const valid = validateSketch(sketch); if (!valid.ok) return [{ code: 'schema', ids: [], message: valid.reason }];
  const ordered = chain(sketch, meta); if ('code' in ordered) return [ordered];
  const problems: DomainProblem[] = [];
  const add = (code: string, ids: string[], message: string) => problems.push({ code, ids, message });
  const pts = ordered.points.map(id => sketch.points.find(p => p.id === id));
  if (pts.some(p => !p)) return [{ code: 'reference', ids: ordered.points, message: 'A contour point is missing.' }];
  const points = pts.map(p => p!);
  if (points[0].x !== 0) add('pole', [points[0].id], 'Place the pole exactly on the axis.');
  if (points.at(-1)!.y !== 0) add('rim', [points.at(-1)!.id], 'Place the rim exactly on the baffle.');
  if (ordered.entities.length > 256) add('patch-count', ordered.entities.map(e => e.entity.id), 'Use at most 256 patches.');
  if (!ordered.entities.some(e => role(e.entity) === 'moving')) add('moving', ordered.entities.map(e => e.entity.id), 'Include at least one moving patch.');
  for (const p of points) if (!Number.isFinite(p.x) || !Number.isFinite(p.y) || p.x < 0) add('point', [p.id], 'Use finite coordinates and a nonnegative radius.');
  ordered.entities.forEach(({ entity: e, reversed }, i) => {
    const a = points[i], b = points[i + 1], r = role(e);
    if (!(b.x > a.x)) add('radius-order', [e.id, a.id, b.id], 'Radius must increase strictly along each patch.');
    if (r !== 'moving' && r !== 'rigid') add('role', [e.id], 'Choose a moving or rigid patch role.');
    if (e.kind === 'arc') {
      if (!sketch.points.some(p => p.id === e.center)) { add('circle', [e.id], 'The arc center is missing.'); return; }
      const g = arcGeometry(sketch, e), sweep = reversed ? -g.sweep : g.sweep, start = reversed ? g.start + g.sweep : g.start;
      if (!Number.isFinite(g.radius + g.endRadius) || g.radius <= 1e-9 || Math.abs(g.radius - g.endRadius) > 1e-9 * Math.max(1, g.radius)) {
        add('circle', [e.id], 'Arc endpoints must share a nonzero circle.'); return;
      }
      const low = Math.min(start, start + sweep), high = Math.max(start, start + sweep);
      const critical = Array.from({ length: 9 }, (_, k) => (k - 4) * Math.PI).some(t => low + 1e-12 < t && t < high - 1e-12);
      if (Math.abs(sweep) > Math.PI + 1e-12 || critical || -g.radius * Math.sin(start + sweep / 2) * sweep <= 0) add('arc-fold', [e.id], 'Arc must increase in radius without folding or exceeding a semicircle.');
    }
  });
  const moving = ordered.entities.filter(e => role(e.entity) === 'moving').map(e => e.entity.id);
  if (Object.keys(meta.weights).some(id => !moving.includes(id)) || moving.some(id => !Number.isFinite(meta.weights[id]))) add('weights', moving, 'Weights must cover exactly the moving patches.');
  return problems;
}
export function lower(sketch: Sketch, meta: SourceMeta): Lowered<SourceDocument> {
  const valid = validateSketch(sketch); if (!valid.ok) return { ok: false, problems: [{ code: 'schema', ids: [], message: valid.reason }] };
  const ordered = chain(sketch, meta); if ('code' in ordered) return { ok: false, problems: [ordered] };
  const points = ordered.points.map((id, i) => {
    const p = sketch.points.find(p => p.id === id)!;
    return { id, r_mm: i === 0 ? 0 : p.x, z_mm: i === ordered.points.length - 1 ? 0 : p.y };
  });
  const segments: SourcePatch[] = ordered.entities.map(({ entity: e, reversed }, i) => ({ id: e.id, start: points[i].id, end: points[i + 1].id,
    role: role(e) as SourcePatch['role'], kind: e.kind as SourcePatch['kind'], center_mm: e.kind === 'arc'
      ? [sketch.points.find(p => p.id === e.center)!.x, sketch.points.find(p => p.id === e.center)!.y] : null,
    direction: e.kind === 'arc' && e.ccw === reversed ? 'cw' : 'ccw' }));
  return { ok: true, document: { contour: { version: 1, physical_source_id: meta.physical_source_id, rim_id: meta.rim_id, points, segments },
    drive: { channel_id: meta.channel_id, motion: meta.motion, weights: { ...meta.weights } } } };
}
export const sourceContourAdapter: SketchAdapter<SourceDocument, SourceMeta> = { id: 'source-contour-v1', references, seed, lower, rules };

export function applySourceCommand(sketch: Sketch, meta: SourceMeta, command: Command) {
  const result = applyCommand(sketch, command); if ('error' in result) return result;
  const weights = { ...meta.weights };
  if (command.kind === 'splitAt' && Object.hasOwn(weights, command.entity)) {
    const right = result.created.find(id => result.sketch.entities.some(e => e.id === id));
    if (right) weights[right] = weights[command.entity];
  }
  const moving = result.sketch.entities.filter(e => !e.construction && role(e) === 'moving');
  for (const id of Object.keys(weights)) if (!moving.some(e => e.id === id)) delete weights[id];
  for (const e of moving) if (!Object.hasOwn(weights, e.id)) weights[e.id] = 1;
  let endpoints = meta.endpoints;
  if (command.kind === 'addLine' || command.kind === 'addTangentArc' || command.kind === 'addArc3') {
    const added = result.sketch.entities.find(e => result.created.includes(e.id));
    if (added?.kind === 'line' || added?.kind === 'arc') {
      const start = added.kind === 'line' ? added.p1 : added.start, end = added.kind === 'line' ? added.p2 : added.end;
      const attaches = start === endpoints.rim || result.sketch.constraints.some(c => c.kind === 'coincident'
        && (c.a === start && c.b === endpoints.rim || c.b === start && c.a === endpoints.rim));
      if (!sketch.entities.some(e => !e.construction)) endpoints = { pole: start, rim: end };
      else if (attaches) endpoints = { ...endpoints, rim: end };
    }
  }
  return { ...result, meta: { ...meta, weights, endpoints } };
}
