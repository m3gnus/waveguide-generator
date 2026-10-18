import type { Command, PointRef } from './commands';
import { constraintProblem, constraintRefs, entityPoints, isJSON, numericSuffix, validateSketch, type Arc, type Constraint, type Entity, type Point, type Sketch } from './model';
import { distance, onArc } from './geometry';

export type CommandResult = { sketch: Sketch; created: string[]; removedConstraints: string[] } | { error: string };
export function applyCommand(sketch: Sketch, command: Command): CommandResult {
  const s: Sketch = { ...sketch, points: [...sketch.points], entities: [...sketch.entities], constraints: [...sketch.constraints] };
  const created: string[] = [], removedConstraints: string[] = [];
  const used = new Set([...s.points, ...s.entities, ...s.constraints].map(e => e.id));
  function allocate(prefix: string) {
    let id = `${prefix}${s.nextId++}`;
    while (used.has(id)) id = `${prefix}${s.nextId++}`;
    if (used.has(id) || !id || id.trim() !== id) throw new Error(`Duplicate or invalid ID: ${id}.`);
    s.nextId = Math.max(s.nextId, numericSuffix(id) + 1);
    if (!Number.isSafeInteger(s.nextId)) throw new Error('ID suffix exceeds the safe integer range.');
    used.add(id); created.push(id); return id;
  }
  function point(id: string) { const p = s.points.find(p => p.id === id); if (!p) throw new Error(`Unknown point: ${id}.`); return p; }
  function entity(id: string) { const e = s.entities.find(e => e.id === id); if (!e) throw new Error(`Unknown entity: ${id}.`); return e; }
  function addConstraint(c: Omit<Constraint, 'id'>) {
    const constraint = c as Constraint, problem = constraintProblem(s, constraint);
    if (problem) throw new Error(problem);
    s.constraints.push({ ...constraint, id: allocate('c') });
  }
  function addPoint(x: number, y: number) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error('Point coordinates must be finite.');
    const p = { id: allocate('p'), x, y }; s.points.push(p); return p;
  }
  function resolve(ref: PointRef): Point {
    if (typeof ref === 'string') return point(ref);
    const p = addPoint(ref.snap?.x ?? ref.x, ref.snap?.y ?? ref.y), c = ref.snap?.constraint;
    if (c) {
      if (c.kind === 'coincident' || c.kind === 'horizontal' || c.kind === 'vertical') addConstraint({ kind: c.kind, a: p.id, b: c.point } as Constraint);
      else if ('line' in c) addConstraint({ kind: 'pointOnLine', point: p.id, line: c.line } as Constraint);
    }
    return p;
  }
  function cleanConstraints(remove: (c: Constraint) => boolean) {
    s.constraints = s.constraints.filter(c => { if (!remove(c)) return true; removedConstraints.push(c.id); return false; });
  }
  function equivalent(a: string, b: string) {
    const reached = new Set([a]); let changed = true;
    while (changed) {
      changed = false;
      for (const c of s.constraints) if (c.kind === 'coincident') {
        if (reached.has(c.a) && !reached.has(c.b)) { reached.add(c.b); changed = true; }
        if (reached.has(c.b) && !reached.has(c.a)) { reached.add(c.a); changed = true; }
      }
    }
    return reached.has(b);
  }
  try {
    if ((command.kind === 'addPoint' || command.kind === 'addLine') && 'id' in command) throw new Error('Caller-supplied IDs are reserved for imports.');
    switch (command.kind) {
      case 'addPoint': addPoint(command.x, command.y); break;
      case 'addLine': {
        const a = resolve(command.from), b = resolve(command.to);
        if (a.id === b.id || distance(a, b) <= 1e-12) throw new Error('A line needs distinct endpoints.');
        s.entities.push({ kind: 'line', id: allocate('l'), p1: a.id, p2: b.id }); break;
      }
      case 'addTangentArc': {
        const a = point(command.from), b = resolve(command.to);
        const incoming = s.entities.filter(e => !e.construction && (e.kind === 'line' ? e.p2 === a.id : e.kind === 'arc' && e.end === a.id));
        if (incoming.length !== 1) throw new Error('Tangent arc needs exactly one incoming curve.');
        const e = incoming[0]; let tx: number, ty: number;
        if (e.kind === 'line') { const p = point(e.p1); tx = a.x - p.x; ty = a.y - p.y; }
        else if (e.kind === 'arc') { const p = point(e.center); tx = (e.ccw ? -1 : 1) * (a.y - p.y); ty = (e.ccw ? 1 : -1) * (a.x - p.x); }
        else throw new Error('Unsupported incoming curve.');
        const dx = b.x - a.x, dy = b.y - a.y, denom = 2 * (-ty * dx + tx * dy);
        if (Math.abs(denom) <= 1e-12 || Math.hypot(tx, ty) <= 1e-12) throw new Error('Tangent arc is degenerate.');
        const k = (dx * dx + dy * dy) / denom, center = addPoint(a.x - k * ty, a.y + k * tx);
        const arc: Arc = { kind: 'arc', id: allocate('a'), center: center.id, start: a.id, end: b.id, ccw: k > 0 };
        s.entities.push(arc); addConstraint({ kind: 'tangent', a: e.id, b: arc.id } as Constraint); break;
      }
      case 'addArc3': {
        const a = resolve(command.start), b = resolve(command.end), p = command.through;
        if (!Number.isFinite(p.x + p.y)) throw new Error('Through point must be finite.');
        const bx = b.x - a.x, by = b.y - a.y, px = p.x - a.x, py = p.y - a.y, d = 2 * (bx * py - by * px);
        if (Math.abs(d) <= 1e-12) throw new Error('Three-point arc is collinear.');
        const bb = bx * bx + by * by, pp = px * px + py * py;
        const center = addPoint(a.x + (bb * py - pp * by) / d, a.y + (bx * pp - px * bb) / d);
        const arc: Arc = { kind: 'arc', id: allocate('a'), center: center.id, start: a.id, end: b.id, ccw: false };
        arc.ccw = onArc(s, arc, p) ? false : true; s.entities.push(arc); break;
      }
      case 'addConstraint': addConstraint(command.constraint); break;
      case 'removeConstraint': {
        if (!s.constraints.some(c => c.id === command.id)) throw new Error(`Unknown constraint: ${command.id}.`);
        cleanConstraints(c => c.id === command.id); break;
      }
      case 'setDimension': case 'setDriving': {
        const c = s.constraints.find(c => c.id === command.id);
        if (!c || !('value' in c)) throw new Error('Expected a dimension.');
        const next = command.kind === 'setDimension' ? { ...c, value: command.value } : { ...c, driving: command.driving };
        const problem = constraintProblem(s, next); if (problem) throw new Error(problem);
        s.constraints = s.constraints.map(c => c.id === command.id ? next : c); break;
      }
      case 'movePoint': {
        point(command.id); if (!Number.isFinite(command.x) || !Number.isFinite(command.y)) throw new Error('Point coordinates must be finite.');
        if (s.entities.some(e => e.reference && entityPoints(e).includes(command.id))) throw new Error('Reference points cannot be moved.');
        s.points = s.points.map(p => p.id === command.id ? { ...p, x: command.x, y: command.y } : p); break;
      }
      case 'deleteEntity': {
        const e = entity(command.id);
        if (e.reference) throw new Error('Reference lines cannot be deleted.');
        const candidates = new Set(entityPoints(e));
        s.entities = s.entities.filter(e => e.id !== command.id);
        cleanConstraints(c => constraintRefs(c).includes(command.id));
        const stillUsed = new Set(s.entities.flatMap(entityPoints));
        const deleted = new Set([...candidates].filter(id => !stillUsed.has(id)));
        s.points = s.points.filter(p => !deleted.has(p.id));
        cleanConstraints(c => constraintRefs(c).some(id => deleted.has(id))); break;
      }
      case 'splitAt': {
        const e = entity(command.entity);
        if (e.reference) throw new Error('Reference lines cannot be split.');
        if (e.kind !== 'line' && e.kind !== 'arc') throw new Error('Only lines and arcs can split.');
        const p = { x: command.x, y: command.y }, start = point(e.kind === 'line' ? e.p1 : e.start), end = point(e.kind === 'line' ? e.p2 : e.end);
        const onLine = (a: Point, b: Point, p: { x: number; y: number }) => Math.abs(distance(a, p) + distance(p, b) - distance(a, b)) <= 1e-9;
        if (!Number.isFinite(p.x + p.y) || distance(start, p) <= 1e-9 || distance(end, p) <= 1e-9
          || (e.kind === 'line' ? !onLine(start, end, p) : !onArc(s, e, p, true))) throw new Error('Split point must be inside the curve.');
        const join = addPoint(p.x, p.y), rightId = allocate(e.kind === 'line' ? 'l' : 'a');
        const left: Entity = e.kind === 'line' ? { ...e, p2: join.id } : { ...e, end: join.id };
        const right: Entity = e.kind === 'line' ? { ...e, id: rightId, p1: join.id } : { ...e, id: rightId, start: join.id };
        s.entities.splice(s.entities.indexOf(e), 1, left, right);
        s.constraints = s.constraints.flatMap(c => {
          if (constraintRefs(c).includes(e.id) && ['equal', 'midpoint', 'horizontal', 'vertical', 'parallel', 'perpendicular', 'angle'].includes(c.kind)
            || c.kind === 'distance' && [start.id, end.id].includes(c.a) && [start.id, end.id].includes(c.b)) {
            removedConstraints.push(c.id); return [];
          }
          if ((c.kind === 'pointOnLine' || c.kind === 'pointOnArc') && constraintRefs(c).includes(e.id)) {
            const p = point(c.point);
            const contains = (part: Entity) => part.kind === 'line' ? onLine(point(part.p1), point(part.p2), p) : part.kind === 'arc' && onArc(s, part, p);
            const remap = c.kind === 'pointOnLine' ? { ...c, line: rightId } : { ...c, arc: rightId };
            if (contains(left)) return [c];
            if (contains(right)) return [remap];
            if (e.kind === 'line') {
              const cross = (p.x - start.x) * (end.y - start.y) - (p.y - start.y) * (end.x - start.x);
              if (Math.abs(cross) <= 1e-9 * distance(start, end)) return [c, { ...remap, id: allocate('c') }];
            } else {
              const center = point(e.center);
              if (Math.abs(distance(center, p) - distance(center, start)) <= 1e-9 * Math.max(1, distance(center, start))) return [c];
            }
            removedConstraints.push(c.id); return [];
          }
          // Endpoint tangencies follow the original contact, not the new cut.
          if (c.kind === 'tangent' && (c.a === e.id || c.b === e.id)) {
            const other = entity(c.a === e.id ? c.b : c.a);
            const ends = other.kind === 'line' ? [other.p1, other.p2] : other.kind === 'arc' ? [other.start, other.end] : [];
            if (ends.some(p => equivalent(p, end.id))) return [{ ...c, a: c.a === e.id ? rightId : c.a, b: c.b === e.id ? rightId : c.b }];
          }
          return [c];
        }); break;
      }
      case 'setEntityData': case 'setConstruction': {
        const e = entity(command.id);
        if (e.reference && (command.kind === 'setEntityData' || !command.construction)) throw new Error('Reference lines cannot be changed.');
        if (command.kind === 'setConstruction' && typeof command.construction !== 'boolean') throw new Error('Construction must be boolean.');
        if (command.kind === 'setEntityData' && !isJSON(command.data)) throw new Error('Entity data must be JSON.');
        s.entities = s.entities.map(e => e.id !== command.id ? e : command.kind === 'setEntityData' ? { ...e, data: command.data } : { ...e, construction: command.construction }); break;
      }
    }
    const validation = validateSketch(s); if (!validation.ok) return { error: validation.reason };
    return { sketch: s, created, removedConstraints };
  } catch (e) { return { error: e instanceof Error ? e.message : String(e) }; }
}

interface ImportBuilder {
  sketch: Sketch;
  point(x: number, y: number): string;
  line(p1: string, p2: string, preferred?: string): string;
  constraint(constraint: import('./model').NewConstraint): string;
}
export function importedSketch(points: Point[], entities: Entity[], build?: (b: ImportBuilder) => void): Sketch {
  const sketch: Sketch = { version: 'wg-sketch-v1', points: [...points], entities: [...entities], constraints: [],
    nextId: 1 + Math.max(0, ...[...points, ...entities].map(e => numericSuffix(e.id))) };
  const used = new Set([...points, ...entities].map(e => e.id));
  const allocate = (prefix: string, preferred?: string) => {
    let id = preferred && !used.has(preferred) ? preferred : `${prefix}${sketch.nextId++}`;
    while (used.has(id)) id = `${prefix}${sketch.nextId++}`;
    used.add(id); sketch.nextId = Math.max(sketch.nextId, numericSuffix(id) + 1); return id;
  };
  build?.({ sketch,
    point(x, y) { const id = allocate('p'); sketch.points.push({ id, x, y }); return id; },
    line(p1, p2, preferred) { const id = allocate('l', preferred); sketch.entities.push({ kind: 'line', id, p1, p2, construction: true, reference: true }); return id; },
    constraint(c) { const id = allocate('c'); sketch.constraints.push({ ...c, id } as Constraint); return id; },
  });
  return sketch;
}
