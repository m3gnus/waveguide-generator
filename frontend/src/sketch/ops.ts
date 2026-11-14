import type { Command, PointRef } from './commands';
import { constraintProblem, constraintRefs, entityPoints, isJSON, numericSuffix, parameterNameProblem, validateSketch, type Arc, type NewConstraint, type Constraint, type Entity, type Point, type Sketch } from './model';
import { arcThrough, distance, onArc, onSegment, constraintSurvivesPointMove, curveContains, curveEnds, tangentArc } from './geometry';

import { parseDecimalInput } from '../design/decimalInput';
import { renameParameterReferences, freezeRemovedExpressionReferences, expressionDependents, renameExpressionReferences, rescaleDiameterExpressions, resolveExpressions } from './expressions';
import { internalValue } from './dimensionValue';
import { commandNotice } from './notices';
import { trimGeometry, extendGeometry, type Cut } from './topology';
import { equivalent } from './coincidence';
import { modifyContext } from './tools/modifyContext';
import { applyFillet } from './virtualSharpFillet';
import { isVirtualSharpHelper, pruneVirtualSharps, virtualSharpAliases, virtualSharpConstraintIds } from './virtualSharp';

/** Low-level removal shared by user deletion and reference lifecycle cleanup. */
export function removeEntity(sketch: Sketch, id: string): { sketch: Sketch; removedConstraints: string[] } {
  const entity = sketch.entities.find(e => e.id === id);
  if (!entity) throw new Error(`Unknown entity: ${id}.`);
  const entities = sketch.entities.filter(e => e.id !== id), stillUsed = new Set(entities.flatMap(entityPoints));
  const deleted = new Set(entityPoints(entity).filter(point => !stillUsed.has(point)));
  const constraints = sketch.constraints.filter(c => !constraintRefs(c).some(ref => ref === id || deleted.has(ref)));
  return { sketch: { ...sketch, entities, points: sketch.points.filter(p => !deleted.has(p.id)), constraints },
    removedConstraints: sketch.constraints.filter(c => !constraints.includes(c)).map(c => c.id) };
}
export type CommandResult = { sketch: Sketch; created: string[]; removedConstraints: string[]; notice?: string } | { error: string };
export function applyCommand(sketch: Sketch, command: Command): CommandResult {
  if ((command.kind === 'trim' || command.kind === 'extend') && command.context) {
    try { return applyCommand(modifyContext(sketch, command.context), { ...command, context: undefined }); }
    catch (reason) { return { error: reason instanceof Error ? reason.message : String(reason) }; }
  }
  const s: Sketch = { ...sketch, points: [...sketch.points], entities: [...sketch.entities], constraints: [...sketch.constraints] };
  const created: string[] = [], removedConstraints: string[] = [];
  const used = new Set([...s.points, ...s.entities, ...s.constraints].map(e => e.id));
  function allocate(prefix: string) {
    let id = `${prefix}${s.nextId++}`;
    while (used.has(id) || prefix === 'c' && s.parameters?.some(p => p.name === id)) id = `${prefix}${s.nextId++}`;
    if (used.has(id) || !id || id.trim() !== id) throw new Error(`Duplicate or invalid ID: ${id}.`);
    s.nextId = Math.max(s.nextId, numericSuffix(id) + 1);
    if (!Number.isSafeInteger(s.nextId)) throw new Error('ID suffix exceeds the safe integer range.');
    used.add(id); created.push(id); return id;
  }
  function checkParameterName(name: string, except?: string) {
    const problem = parameterNameProblem(name, [
      ...s.constraints.filter(c => 'value' in c).flatMap(c => [c.id, c.label?.trim()]),
      ...(s.parameters ?? []).filter(p => p.name !== except).map(p => p.name),
    ]);
    if (problem) throw new Error(problem);
  }
  function point(id: string) { const p = s.points.find(p => p.id === id); if (!p) throw new Error(`Unknown point: ${id}.`); return p; }
  function entity(id: string) { const e = s.entities.find(e => e.id === id); if (!e) throw new Error(`Unknown entity: ${id}.`); return e; }
  function addConstraint(c: NewConstraint) {
    const constraint = c as Constraint, problem = constraintProblem(s, constraint);
    if (problem) throw new Error(problem);
    const id = allocate('c'); s.constraints.push({ ...constraint, id }); return id;
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
  function splitCurve(entityId: string, at: { x: number; y: number }) {
    const e = entity(entityId);
    if (e.reference) throw new Error('Reference lines cannot be split.');
    if (e.kind !== 'line' && e.kind !== 'arc') throw new Error('Only lines and arcs can split.');
    const p = { x: at.x, y: at.y }, start = point(e.kind === 'line' ? e.p1 : e.start), end = point(e.kind === 'line' ? e.p2 : e.end);
    if (!Number.isFinite(p.x + p.y) || distance(start, p) <= 1e-9 || distance(end, p) <= 1e-9
      || (e.kind === 'line' ? !onSegment(start, end, p) : !onArc(s, e, p, true))) throw new Error('Split point must be inside the curve.');
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
        const contains = (part: Entity) => (part.kind === 'line' || part.kind === 'arc') && curveContains(s, part, p);
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
        if (ends.some(p => equivalent(s, p, end.id))) return [{ ...c, a: c.a === e.id ? rightId : c.a, b: c.b === e.id ? rightId : c.b }];
      }
      return [c];
    }); return { left, right, join };
  }

  function deleteCurve(id: string) {
    const result = removeEntity(s, id); Object.assign(s, result.sketch); removedConstraints.push(...result.removedConstraints);
  }
  function attach(pointId: string, cut: Pick<Cut, 'point' | 'cutter'>) {
    const endpoints = curveEnds(s, cut.cutter), endpoint = endpoints.find(p => distance(p, cut.point) <= 1e-8);
    if (endpoint) addConstraint({ kind: 'coincident', a: pointId, b: endpoint.id });
    else if (cut.cutter.kind === 'line') addConstraint({ kind: 'pointOnLine', point: pointId, line: cut.cutter.id });
    else addConstraint({ kind: 'pointOnArc', point: pointId, arc: cut.cutter.id });
  }
  try {
    if ((command.kind === 'addPoint' || command.kind === 'addLine') && 'id' in command) throw new Error('Caller-supplied IDs are reserved for imports.');
    switch (command.kind) {
      case 'fillet': {
        applyFillet(s, command.point, command.radius, { allocate, addPoint, addConstraint, cleanConstraints }); break;
      }
      case 'trim': {
        const g = trimGeometry(s, command.entity, command, command.unbounded);
        if (!g.low && !g.high) { deleteCurve(g.entity.id); break; }
        let removedId = g.entity.id, replacementId: string | undefined;
        if (g.low) { const split = splitCurve(removedId, g.low.point); removedId = split.right.id; attach(split.join.id, g.low); }
        if (g.high) { const split = splitCurve(removedId, g.high.point); replacementId = split.right.id; attach(split.join.id, g.high); }
        // Keep the original entity identity for a shortened end piece.
        if (!g.low && replacementId) s.constraints = s.constraints.map(c => c.kind === 'radius' && c.arc === g.entity.id ? { ...c, arc: replacementId! } : c);
        deleteCurve(removedId);
        if (replacementId && !g.low && g.high) {
          s.entities = s.entities.map(e => e.id === replacementId ? { ...e, id: g.entity.id } : e);
          s.constraints = s.constraints.map(c => Object.fromEntries(Object.entries(c).map(([key, value]) => [key, ['a', 'b', 'line', 'arc'].includes(key) && value === replacementId ? g.entity.id : value])) as Constraint);
        }
        break;
      }
      case 'extend': {
        const g = extendGeometry(s, command.entity, command, command.unbounded);
        s.points = s.points.map(p => p.id === g.from.id ? { ...p, x: g.to.x, y: g.to.y } : p);
        cleanConstraints(c => constraintRefs(c).includes(g.from.id) && !constraintSurvivesPointMove(s, c));
        attach(g.from.id, { point: g.to, cutter: g.cutter }); break;
      }
      case 'addParameter': {
        checkParameterName(command.name);
        s.parameters = [...(s.parameters ?? []), { name: command.name, expression: command.expression.trim(), unit: command.unit, value: 0 }]; break;
      }
      case 'setParameter': {
        if (!s.parameters?.some(p => p.name === command.name)) throw new Error(`Unknown parameter "${command.name}". Select an existing parameter.`);
        s.parameters = s.parameters.map(p => p.name === command.name ? { ...p, expression: command.expression.trim(), ...(command.unit === undefined ? {} : { unit: command.unit }) } : p); break;
      }
      case 'renameParameter': {
        if (!s.parameters?.some(p => p.name === command.from)) throw new Error(`Unknown parameter "${command.from}". Select an existing parameter.`);
        checkParameterName(command.to, command.from);
        Object.assign(s, renameParameterReferences(s, command.from, command.to)); break;
      }
      case 'removeParameter': {
        if (!s.parameters?.some(p => p.name === command.name)) throw new Error(`Unknown parameter "${command.name}". Select an existing parameter.`);
        const users = expressionDependents(s, command.name);
        if (users.length) throw new Error(`Parameter "${command.name}" is used by ${users.map(id => s.constraints.find(c => c.id === id)?.label || id).join(', ')}. Clear those expressions before deleting it.`);
        const parameters = s.parameters.filter(p => p.name !== command.name);
        if (parameters.length) s.parameters = parameters; else delete s.parameters;
        break;
      }
      case 'setDimensionExpression': {
        const c = s.constraints.find(c => c.id === command.id);
        if (!c || !('value' in c) || !c.driving) throw new Error('Expressions need a driving dimension. Select a driving dimension first.');
        if (typeof command.expression !== 'string') throw new Error('Enter a dimension expression as text.');
        const text = command.expression.trim(), numeric = parseDecimalInput(text), { expression: _old, ...base } = c;
        const next = !text ? base : numeric.kind === 'number' ? { ...base, value: internalValue(c, numeric.value) } : { ...base, expression: text };
        s.constraints = s.constraints.map(c => c.id === command.id ? next : c); break;
      }
      case 'setDiameter': {
        const c = s.constraints.find(c => c.id === command.id);
        if (!c || !('value' in c)) throw new Error('Diameter needs a radial dimension. Select a radius or dimension to the axis.');
        if (typeof command.diameter !== 'boolean') throw new Error('Choose whether the radial dimension displays a diameter.');
        if (!!c.diameter === command.diameter) break;
        const rewritten = rescaleDiameterExpressions(s, c.id, command.diameter);
        const { diameter: _old, ...base } = rewritten.constraints.find(n => n.id === c.id)!;
        const next = command.diameter ? { ...base, diameter: true as const } : base;
        const problem = constraintProblem(s, next); if (problem) throw new Error(problem);
        Object.assign(s, rewritten);
        s.constraints = rewritten.constraints.map(c => c.id === command.id ? next : c); break;
      }
      case 'addPoint': addPoint(command.x, command.y); break;
      case 'addLine': {
        const a = resolve(command.from), b = resolve(command.to);
        if (a.id === b.id || distance(a, b) <= 1e-12) throw new Error('A line needs distinct endpoints.');
        s.entities.push({ kind: 'line', id: allocate('l'), p1: a.id, p2: b.id }); break;
      }
      case 'addTangentArc': {
        const a = point(command.from), b = resolve(command.to);
        const geometry = tangentArc(s, a.id, b), center = addPoint(geometry.center.x, geometry.center.y);
        const arc: Arc = { kind: 'arc', id: allocate('a'), center: center.id, start: a.id, end: b.id, ccw: geometry.ccw };
        s.entities.push(arc); addConstraint({ kind: 'tangent', a: geometry.incoming, b: arc.id } as Constraint); break;
      }
      case 'addArc3': {
        const a = resolve(command.start), b = resolve(command.end), p = command.through;
        const geometry = arcThrough(a, b, p), center = addPoint(geometry.center.x, geometry.center.y);
        const arc: Arc = { kind: 'arc', id: allocate('a'), center: center.id, start: a.id, end: b.id, ccw: geometry.ccw };
        s.entities.push(arc); break;
      }
      case 'addConstraint': addConstraint(command.constraint); break;
      case 'removeConstraint': {
        if (virtualSharpConstraintIds(s).has(command.id)) throw new Error('This relation holds the virtual sharp at the original corner. Delete the virtual sharp or its dimensions to remove it.');
        if (!s.constraints.some(c => c.id === command.id)) throw new Error(`Unknown constraint: ${command.id}.`);
        cleanConstraints(c => c.id === command.id); break;
      }
      case 'setDimensionPlacement': {
        const c = s.constraints.find(c => c.id === command.id);
        if (!c || !('value' in c)) throw new Error('Expected a dimension.');
        const { placement: previous, ...base } = c;
        const next = { ...base, ...(command.placement === undefined ? {} : { placement: command.placement }) };
        const problem = constraintProblem(s, next); if (problem) throw new Error(problem);
        s.constraints = s.constraints.map(c => c.id === command.id ? next : c); break;
      }
      case 'setConstraintLabel': {
        const c = s.constraints.find(c => c.id === command.id);
        if (!c || !('value' in c)) throw new Error('Expected a dimension.');
        if (typeof command.label !== 'string' || command.label.trim().length > 40) throw new Error('Dimension names must be strings of at most 40 characters.');
        const { label: previous, ...base } = c, label = command.label.trim();
        if (label && s.constraints.some(other => 'value' in other && other.id === label)) throw new Error(`${label} is already a dimension's id; choose another name.`);
        if (label && s.parameters?.some(p => p.name === label)) throw new Error(`${label} is already a parameter name; choose another name.`);
        Object.assign(s, renameExpressionReferences(s, c.id, label));
        s.constraints = s.constraints.map(c => c.id === command.id ? { ...base, ...(label ? { label } : {}) } : c); break;
      }
      case 'setEntityName': {
        const e = entity(command.id);
        if (e.reference) throw new Error('Reference lines cannot be changed.');
        if (typeof command.name !== 'string' || command.name.trim().length > 60) throw new Error('Patch names must be strings of at most 60 characters.');
        const data = e.data && typeof e.data === 'object' && !Array.isArray(e.data) ? { ...e.data } : {};
        const name = command.name.trim();
        if (name) data.name = name; else delete data.name;
        s.entities = s.entities.map(v => v.id === e.id ? { ...e, data } : v); break;
      }
      case 'setDimension': case 'setDriving': {
        const c = s.constraints.find(c => c.id === command.id);
        if (!c || !('value' in c)) throw new Error('Expected a dimension.');
        if (command.kind === 'setDriving' && typeof command.driving !== 'boolean') throw new Error('Choose whether this dimension is driving or reference.');
        if (command.kind === 'setDriving' && !command.driving) {
          const dependents = expressionDependents(s, c.id);
          if (dependents.length) throw new Error(`This dimension is referenced by ${dependents.map(id => s.constraints.find(c => c.id === id)?.label || id).join(', ')}. Clear their expressions before making it a reference dimension.`);
        }
        const { expression: _expression, ...base } = c;
        const next = command.kind === 'setDimension' ? { ...base, value: command.value } : command.driving ? { ...c, driving: true } : { ...base, driving: false };
        const problem = constraintProblem(s, next); if (problem) throw new Error(problem);
        s.constraints = s.constraints.map(c => c.id === command.id ? next : c); break;
      }
      case 'movePoint': {
        point(command.id); if (!Number.isFinite(command.x) || !Number.isFinite(command.y)) throw new Error('Point coordinates must be finite.');
        if (s.entities.some(e => e.reference && entityPoints(e).includes(command.id))) throw new Error('Reference points cannot be moved.');
        s.points = s.points.map(p => p.id === command.id ? { ...p, x: command.x, y: command.y } : p); break;
      }
      case 'deletePoint': {
        point(command.id);
        const adjacent = s.entities.filter(e => !isVirtualSharpHelper(e) && entityPoints(e).includes(command.id));
        if (adjacent.some(e => e.reference)) throw new Error('Reference points cannot be deleted.');
        if (adjacent.length) {
          const [left, right] = adjacent;
          if (adjacent.length !== 2 || left.kind !== 'line' || right.kind !== 'line') throw new Error('Remove an interior point between two lines, or select a curve to delete it.');
          if (!!left.construction !== !!right.construction) throw new Error('Adjacent lines need matching construction flags.');
          const far = right.p1 === command.id ? right.p2 : right.p1;
          if (far === left.p1 || far === left.p2) throw new Error('Removing this point would collapse the chain.');
          s.entities = s.entities.filter(e => e.id !== right.id).map(e => e.id === left.id
            ? { ...left, p1: left.p1 === command.id ? far : left.p1, p2: left.p2 === command.id ? far : left.p2 } : e);
          cleanConstraints(c => constraintRefs(c).includes(right.id) || constraintRefs(c).includes(command.id));
        } else {
          // Deleting a virtual sharp deletes the whole corner, so no alias keeps its helpers alive.
          const corner = virtualSharpAliases(s, command.id);
          cleanConstraints(c => constraintRefs(c).some(id => corner.has(id)));
        }
        s.points = s.points.filter(p => p.id !== command.id); break;
      }
      case 'deleteEntity': {
        const e = entity(command.id);
        if (e.reference) throw new Error('Reference lines cannot be deleted.');
        const removed = removeEntity(s, command.id);
        Object.assign(s, removed.sketch); removedConstraints.push(...removed.removedConstraints); break;
      }
      case 'convertEntity': {
        const e = entity(command.id);
        if (e.reference) throw new Error('Reference lines cannot be converted.');
        if (e.kind !== 'line' && e.kind !== 'arc') throw new Error('Only lines and arcs can be converted.');
        if (e.kind === command.to) break;
        let converted: Entity;
        if (e.kind === 'line') {
          const { kind, p1, p2, ...base } = e, a = point(p1), b = point(p2);
          const dx = b.x - a.x, dy = b.y - a.y, length = distance(a, b);
          if (length <= 1e-12) throw new Error('An arc needs distinct endpoints.');
          // Keep steep chords monotone in radius by reducing the bulge before a tangent folds.
          const ratio = dx === 0 ? .15 : Math.min(.15, .4 * Math.tan(Math.atan2(Math.abs(dx), Math.abs(dy)) / 2));
          const sagitta = length * ratio, radius = length * length / (8 * sagitta) + sagitta / 2;
          const side = dy > 0 ? -1 : dy < 0 ? 1 : dx > 0 ? 1 : -1;
          const center = addPoint((a.x + b.x) / 2 - side * dy / length * (sagitta - radius),
            (a.y + b.y) / 2 + side * dx / length * (sagitta - radius));
          converted = { ...base, kind: 'arc', start: p1, end: p2, center: center.id, ccw: side < 0 };
        } else {
          const { kind, center, start, end, ccw, ...base } = e;
          converted = { ...base, kind: 'line', p1: start, p2: end };
        }
        s.entities = s.entities.map(v => v.id === e.id ? converted : v);
        cleanConstraints(c => constraintRefs(c).includes(e.id) && !!constraintProblem(s, c));
        // The centre belongs to the arc: like deleting a point, it takes its relations with it.
        if (e.kind === 'arc' && !s.entities.some(v => entityPoints(v).includes(e.center))) {
          cleanConstraints(c => constraintRefs(c).includes(e.center)); s.points = s.points.filter(p => p.id !== e.center);
        }
        break;
      }
      case 'splitAt': splitCurve(command.entity, { x: command.x, y: command.y }); break;
      case 'setEntityData': case 'setConstruction': {
        const e = entity(command.id);
        if (e.reference && (command.kind === 'setEntityData' || !command.construction)) throw new Error('Reference lines cannot be changed.');
        if (command.kind === 'setConstruction' && typeof command.construction !== 'boolean') throw new Error('Construction must be boolean.');
        if (command.kind === 'setEntityData' && !isJSON(command.data)) throw new Error('Entity data must be JSON.');
        s.entities = s.entities.map(e => e.id !== command.id ? e : command.kind === 'setEntityData' ? { ...e, data: e.data && typeof e.data === 'object' && !Array.isArray(e.data) && command.data && typeof command.data === 'object' && !Array.isArray(command.data) ? { ...e.data, ...command.data } : command.data } : { ...e, construction: command.construction }); break;
      }
    }
    Object.assign(s, freezeRemovedExpressionReferences(sketch, s));
    removedConstraints.push(...pruneVirtualSharps(s, addConstraint));
    const resolved = resolveExpressions(s);
    if (resolved.errors.length) throw new Error(resolved.errors.map(e => e.message).join(' '));
    // A diameter toggle is presentation only, including exact stored floating-point values.
    if (command.kind !== 'setDiameter') Object.assign(s, resolved.sketch);
    const validation = validateSketch(s); if (!validation.ok) return { error: validation.reason };
    const notice = commandNotice(sketch, s, command.kind);
    return { sketch: s, created, removedConstraints, ...(notice ? { notice } : {}) };
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
