import type { SourceDocument, SourcePatch } from '../../api/sourceEditor';
import type { DomainProblem, Lowered, Reference, SketchAdapter } from '../adapter';
import { entityPoints, validateSketch, type Entity, type Sketch } from '../model';
import { applyCommand, importedSketch } from '../ops';
import type { Command } from '../commands';
import { arcGeometry } from '../geometry';
import { presetDimensionActive } from './sourcePresetMapping';

export interface SourceMeta {
  physical_source_id: string; rim_id: string; channel_id: string; motion: 'normal' | 'axial';
  weights: Record<string, number>; references: Reference[];
  preset?: { shape: 'flat' | 'dome' | 'cone'; dimensions: Record<string, string>; values?: Record<string, number> };
  limit?: SourceLimit;
}
export interface SourceLimit { kind: 'aperture' | 'mouth' | 'throat'; radius: number; hard?: boolean }

/** Install fixed context separately from the editable contour. A throat also holds the rim. */
export function withSourceLimit(state: { sketch: Sketch; meta: SourceMeta }, limit: SourceLimit): { sketch: Sketch; meta: SourceMeta } {
  if (!(limit.radius > 0) || !Number.isFinite(limit.radius)) throw new Error('Limit radius must be positive and finite.');
  let sketch = structuredClone(state.sketch);
  const ordered = contourPointOrder(sketch, state.meta), rim = ordered.at(-1);
  const old = state.meta.references.find(r => r.kind === 'limit');
  let line = old && sketch.entities.find(e => e.id === old.id);
  if (!line) {
    const result = applyCommand(sketch, { kind: 'addLine', from: { x: limit.radius, y: 0 }, to: { x: limit.radius, y: 1 } });
    if ('error' in result) throw new Error(result.error);
    sketch = result.sketch;
    line = sketch.entities.find(e => e.id === result.created.find(id => sketch.entities.some(e => e.id === id)))!;
    line.reference = true; line.construction = true;
  }
  if (line.kind !== 'line') throw new Error('Expected a limit line.');
  const referencePoints = new Set(sketch.entities.filter(e => e.reference).flatMap(entityPoints));
  const radius = sketch.points.find(p => p.id === rim)?.x;
  // When attaching an existing contour to a new throat, preserve its shape and circles.
  if (limit.hard && radius && radius !== limit.radius) {
    const scale = limit.radius / radius;
    sketch.points = sketch.points.map(p => referencePoints.has(p.id) ? p : { ...p, x: p.x * scale, y: p.y * scale });
    sketch.constraints = sketch.constraints.map(c => 'value' in c && c.kind !== 'angle' ? { ...c, value: c.value * scale } : c);
  }
  const ends = [line.p1, line.p2];
  sketch.points = sketch.points.map(p => ends.includes(p.id) ? { ...p, x: limit.radius } : limit.hard && p.id === rim ? { ...p, x: limit.radius } : p);
  sketch.constraints = sketch.constraints.filter(c => !(c.kind === 'pointOnLine' && c.line === line.id));
  if (limit.hard && rim) {
    const result = applyCommand(sketch, { kind: 'addConstraint', constraint: { kind: 'pointOnLine', point: rim, line: line.id } });
    if ('error' in result) throw new Error(result.error);
    sketch = result.sketch;
    // Rim = radius + surround + land. The locked rim drives radius instead.
    const radiusDimension = state.meta.preset?.dimensions.radius_mm;
    sketch.constraints = sketch.constraints.map(c => c.id === radiusDimension && 'driving' in c ? { ...c, driving: false } : c);
  }
  const reference: Reference = { id: line.id, kind: 'limit', radius: limit.radius,
    label: `${limit.kind[0].toUpperCase()}${limit.kind.slice(1)} r ${limit.radius.toFixed(2)}`, locked: !!limit.hard, ...(rim ? { point: rim } : {}) };
  return { sketch, meta: { ...state.meta, limit, references: [...state.meta.references.filter(r => r.kind !== 'limit'), reference] } };
}
export const references: Reference[] = [{ id: 'axis', kind: 'axis' }, { id: 'baffle', kind: 'baffle' }];
export function seed(document: SourceDocument): { sketch: Sketch; meta: SourceMeta } {
  const { contour, drive } = document;
  const points = contour.points.map(p => ({ id: p.id, x: p.r_mm, y: p.z_mm }));
  const entities: Entity[] = contour.segments.map(e => e.kind === 'line'
    ? { kind: 'line', id: e.id, p1: e.start, p2: e.end, data: { role: e.role } }
    : { kind: 'arc', id: e.id, center: e.start, start: e.start, end: e.end, ccw: e.direction === 'ccw', data: { role: e.role } });
  let declared: Reference[] = [];
  const sketch = importedSketch(points, entities, b => {
    const centers = new Map<string, string>();
    for (const e of contour.segments) if (e.kind === 'arc') {
      if (!e.center_mm) throw new Error(`Missing arc center: ${e.id}.`);
      centers.set(e.id, b.point(e.center_mm[0], e.center_mm[1]));
    }
    b.sketch.entities = b.sketch.entities.map(e => e.kind === 'arc' ? { ...e, center: centers.get(e.id)! } : e);
    declared = references.map(ref => {
      const p1 = b.point(0, 0), p2 = b.point(ref.kind === 'axis' ? 0 : 1, ref.kind === 'axis' ? 1 : 0);
      return { ...ref, id: b.line(p1, p2, ref.id) };
    });
    if (contour.points.length) {
      b.constraint({ kind: 'pointOnLine', point: contour.points[0].id, line: declared[0].id });
      b.constraint({ kind: 'pointOnLine', point: contour.points.at(-1)!.id, line: declared[1].id });
    }
  });
  const valid = validateSketch(sketch); if (!valid.ok) throw new Error(valid.reason);
  return { sketch, meta: { physical_source_id: contour.physical_source_id, rim_id: contour.rim_id,
    channel_id: drive.channel_id, motion: drive.motion, weights: { ...drive.weights }, references: declared } };
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
  if (tips.length !== 2) return problem();
  const axis = meta.references.find(r => r.kind === 'axis')?.id;
  const locked = sketch.constraints.filter(c => c.kind === 'pointOnLine' && c.line === axis).map(c => root('point' in c ? c.point : ''));
  const poles = tips.filter(id => locked.includes(id));
  const baffle = meta.references.find(r => r.kind === 'baffle')?.id;
  const rims = sketch.constraints.flatMap(c => c.kind === 'pointOnLine' && c.line === baffle ? [root(c.point)] : []);
  if (!poles.length) {
    const candidates = tips.filter(id => !rims.includes(id) && (!locked.length && rims.length > 0 || Math.abs(sketch.points.find(p => p.id === id)!.x) <= 1e-9));
    if (candidates.length === 1) return { code: 'pole', ids: candidates, message: 'Lock the pole to the axis' };
  }
  if (!tips.some(id => rims.includes(id) && !poles.includes(id))) {
    const candidates = tips.filter(id => !poles.includes(id) && (poles.length > 0 || Math.abs(sketch.points.find(p => p.id === id)!.y) <= 1e-9));
    if (candidates.length === 1) return { code: 'rim', ids: candidates, message: 'Lock the rim to the baffle' };
    return problem();
  }
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
  const stray = sketch.points.find(p => !used.has(p.id) && !refPoints.has(p.id));
  if (stray) return { code: 'stray-point', ids: [stray.id], message: 'Remove the free point or join it to the contour.' };
  return result;
}
const role = (e: Entity): unknown => e.data && typeof e.data === 'object' && !Array.isArray(e.data) ? e.data.role ?? 'moving' : 'moving';
/** Default absent moving weights without rounding or replacing literal signed values. */
export function movingWeights(sketch: Sketch, weights: Record<string, number>) {
  return Object.fromEntries(sketch.entities.filter(e => !e.construction && role(e) === 'moving').map(e => [e.id, weights[e.id] ?? 1]));
}
/** Best-effort table order also works while the domain chain is unfinished. */
export function contourPointOrder(sketch: Sketch, meta: SourceMeta): string[] {
  const ordered = chain(sketch, meta);
  if (!('code' in ordered)) return ordered.points;
  const edges = sketch.entities.filter(e => !e.construction).flatMap(e => e.kind === 'line' ? [[e.p1, e.p2]] : e.kind === 'arc' ? [[e.start, e.end]] : []);
  const ids = new Set(edges.flat()), adjacent = (id: string) => edges.filter(e => e.includes(id));
  const axis = meta.references.find(r => r.kind === 'axis')?.id;
  const pole = sketch.constraints.find(c => c.kind === 'pointOnLine' && c.line === axis && ids.has(c.point));
  let cursor: string | undefined = pole && 'point' in pole ? pole.point : sketch.points.filter(p => ids.has(p.id) && adjacent(p.id).length === 1).sort((a, b) => a.x - b.x)[0]?.id;
  const result: string[] = [], visited = new Set<string[]>();
  while (cursor && !result.includes(cursor)) {
    result.push(cursor);
    const next: string[][] = adjacent(cursor).filter(e => !visited.has(e));
    if (next.length !== 1) break;
    visited.add(next[0]); cursor = next[0].find(id => id !== cursor);
  }
  return [...result, ...sketch.points.filter(p => ids.has(p.id) && !result.includes(p.id)).map(p => p.id)];
}
export function rules(sketch: Sketch, meta: SourceMeta): DomainProblem[] {
  const valid = validateSketch(sketch); if (!valid.ok) return [{ code: 'schema', ids: [], message: valid.reason }];
  const ordered = chain(sketch, meta); if ('code' in ordered) return [ordered];
  const problems: DomainProblem[] = [];
  const add = (code: string, ids: string[], message: string) => problems.push({ code, ids, message });
  const pts = ordered.points.map(id => sketch.points.find(p => p.id === id));
  if (pts.some(p => !p)) return [{ code: 'reference', ids: ordered.points, message: 'A contour point is missing.' }];
  const raw = pts.map(p => p!);
  const points = raw.map((p, i) => ({ ...p, x: i === 0 && Math.abs(p.x) <= 1e-9 ? 0 : p.x,
    y: i === raw.length - 1 && Math.abs(p.y) <= 1e-9 ? 0 : p.y }));
  const byId = new Map(sketch.points.map(p => [p.id, p]));
  const limit = meta.limit, rim = points.at(-1)!;
  if (limit?.hard && Math.abs(rim.x - limit.radius) > 1e-8) add('rim-limit', [rim.id], 'Rim must match the horn throat');
  else if (limit && !limit.hard) {
    const rawGap = limit.radius - rim.x, gap = Math.abs(rawGap) <= 1e-9 ? 0 : rawGap;
    if (gap < 0 || limit.kind === 'mouth' && gap <= .1 || limit.kind === 'aperture' && gap > 0 && gap <= .1) {
      problems.push({ code: 'rim-limit', ids: [rim.id, ordered.entities.at(-1)!.entity.id], severity: 'warning',
        message: gap < 0 ? limit.kind === 'aperture' ? 'Rim exceeds the baffle aperture' : 'Rim exceeds the horn mouth'
          : limit.kind === 'mouth' ? 'Horn mouth must exceed the rim by more than 0.1 mm' : 'Aperture gap must be zero or more than 0.1 mm' });
    }
  }
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
      const emitted = new Map([[e.center, byId.get(e.center)!], [e.start, reversed ? b : a], [e.end, reversed ? a : b]]);
      const g = arcGeometry(sketch, e, emitted), sweep = reversed ? -g.sweep : g.sweep, start = reversed ? g.start + g.sweep : g.start;
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
  const pole = sketch.points.find(p => p.id === ordered.points[0])!, rim = sketch.points.find(p => p.id === ordered.points.at(-1))!;
  if (meta.limit?.hard && Math.abs(rim.x - meta.limit.radius) > 1e-8) return { ok: false, problems: [{ code: 'rim-limit', ids: [rim.id], message: 'Rim must match the horn throat' }] };
  if (Math.abs(pole.x) > 1e-9 || Math.abs(rim.y) > 1e-9) return { ok: false, problems: [
    ...(Math.abs(pole.x) > 1e-9 ? [{ code: 'pole', ids: [pole.id], message: 'Place the pole on the axis.' }] : []),
    ...(Math.abs(rim.y) > 1e-9 ? [{ code: 'rim', ids: [rim.id], message: 'Place the rim on the baffle.' }] : []),
  ] };
  const points = ordered.points.map((id, i) => {
    const p = sketch.points.find(p => p.id === id)!;
    return { id, r_mm: i === 0 ? 0 : i === ordered.points.length - 1 && meta.limit && (meta.limit.hard || Math.abs(p.x - meta.limit.radius) <= 1e-9) ? meta.limit.radius : p.x, z_mm: i === ordered.points.length - 1 ? 0 : p.y };
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
  if (meta.limit?.hard && command.kind === 'removeConstraint' && sketch.constraints.some(c => c.id === command.id && c.kind === 'pointOnLine' && meta.references.some(r => r.kind === 'limit' && r.id === c.line))) return { error: 'Set by the horn design' };
  if (command.kind === 'deletePoint') {
    const adjacent = sketch.entities.filter(e => !e.reference && entityPoints(e).includes(command.id));
    if (adjacent.length === 2 && (role(adjacent[0]) !== role(adjacent[1]) || meta.weights[adjacent[0].id] !== meta.weights[adjacent[1].id])) return { error: 'Adjacent patches need the same role and literal weight to remove their join.' };
  }
  const result = applyCommand(sketch, command); if ('error' in result) return result;
  let preset = meta.preset;
  if (preset) {
    // Older envelopes only stored dimension IDs. Capture their values before an edit
    // can remove them, so intentional zero parameters differ from invalidated mappings.
    const values = preset.values ?? Object.fromEntries(Object.entries(preset.dimensions).flatMap(([parameter, id]) => {
      const c = sketch.constraints.find(c => c.id === id);
      return c && 'value' in c ? [[parameter, c.value]] : [];
    }));
    const dimensions = { ...preset.dimensions };
    for (const [parameter, id] of Object.entries(dimensions)) if (!presetDimensionActive(result.sketch, meta, parameter)) {
      delete dimensions[parameter];
      if (result.sketch.constraints.some(c => c.id === id)) {
        result.sketch = { ...result.sketch, constraints: result.sketch.constraints.filter(c => c.id !== id) };
        result.removedConstraints.push(id);
      }
    }
    if (!dimensions.surround_depth_mm) {
      const helpers = result.sketch.entities.filter(e => e.data && typeof e.data === 'object' && !Array.isArray(e.data) && e.data.dimensionHelper === true);
      for (const helper of helpers) {
        const cleaned = applyCommand(result.sketch, { kind: 'deleteEntity', id: helper.id });
        if ('error' in cleaned) return cleaned;
        result.sketch = cleaned.sketch; result.removedConstraints.push(...cleaned.removedConstraints);
      }
    }
    preset = { ...preset, dimensions, values };
  }
  const weights = { ...meta.weights };
  if (command.kind === 'splitAt' && Object.hasOwn(weights, command.entity)) {
    const right = result.created.find(id => result.sketch.entities.some(e => e.id === id));
    if (right) weights[right] = weights[command.entity];
  }
  const moving = result.sketch.entities.filter(e => !e.construction && role(e) === 'moving');
  for (const id of Object.keys(weights)) if (!moving.some(e => e.id === id)) delete weights[id];
  for (const e of moving) if (!Object.hasOwn(weights, e.id)) weights[e.id] = 1;
  return { ...result, meta: { ...meta, weights, ...(preset ? { preset } : {}) } };
}
