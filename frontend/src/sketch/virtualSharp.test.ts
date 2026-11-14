// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { arcFilletCorner } from './arcFilletTestSupport';
import { applyCommand, importedSketch } from './ops';
import type { Command } from './commands';
import { constraintRefs, entityPoints, parseSketch, toJSON, type Arc, type Sketch } from './model';
import { createSession } from './session';
import { arcGeometry, distance } from './geometry';
import { internalHelper } from './dimensionGraphics';
import { isVirtualSharpHelper, virtualSharpData } from './virtualSharp';
import { geometryCandidates, geometryHit } from './geometryHit';
import { marqueeSelection } from './tools/select';
import { sketchGeometry } from './sketchGeometry';
import { candidateDimension, dimensionPicks } from './tools/dimension';
import { SketchController } from './controller';
import { seedPreset, presetDefaults } from './adapters/sourcePreset';
import { expandedPreset } from './adapters/presetTestSupport';
import { applySourceCommand, lower, sourceContourAdapter } from './adapters/sourceContour';
import { presetDimensionActive, presetDimensionProblem } from './adapters/sourcePresetMapping';
import { serializeSourceAuthoring, parseSourceAuthoring } from './adapters/sourceAuthoring';

function edit(s: Sketch, c: Command) {
  const result = applyCommand(s, c); if ('error' in result) throw new Error(result.error); return result;
}
function lineCorner() {
  return importedSketch([{ id: 'farA', x: 0, y: 10 }, { id: 'corner', x: 0, y: 0 }, { id: 'farB', x: 10, y: 0 }],
    [{ id: 'first', kind: 'line', p1: 'farA', p2: 'corner' }, { id: 'second', kind: 'line', p1: 'corner', p2: 'farB' }]);
}
const pairs = ['line–line', 'line–arc', 'arc–arc'] as const;
function fixture(pair: typeof pairs[number]) {
  const s = pair === 'line–line' ? lineCorner() : arcFilletCorner(pair === 'line–arc' ? 'line' : 'internal');
  return edit(s, { kind: 'addConstraint', constraint: { kind: 'fix', point: 'corner' } }).sketch;
}
function assertSupports(s: Sketch) {
  const sharp = s.points.find(p => p.id === 'corner')!;
  for (const e of s.entities.filter(isVirtualSharpHelper)) {
    const v = virtualSharpData(e)!, support = s.entities.find(e => e.id === v.support)!;
    expect(entityPoints(support)).not.toContain(sharp.id);
    if (support.kind === 'line') {
      const a = s.points.find(p => p.id === support.p1)!, b = s.points.find(p => p.id === support.p2)!;
      expect(Math.abs((b.x - a.x) * (sharp.y - a.y) - (b.y - a.y) * (sharp.x - a.x))).toBeLessThan(1e-7);
    } else if (support.kind === 'arc') {
      const g = arcGeometry(s, support); expect(distance(g.center, sharp)).toBeCloseTo(g.radius, 7);
    }
  }
}

describe('virtual sharp fillets', () => {
  const configurations = [['line', 'line'], ['line', 'internal'], ['line', 'external'], ['internal', 'internal'],
    ['internal', 'external'], ['external', 'internal'], ['external', 'external']] as const;
  it.each(configurations)('keeps %s / %s fully constrained while a kept dimension moves the fillet', async (first, second) => {
    let s = second === 'line' ? lineCorner() : arcFilletCorner(first, second);
    const used = new Set(s.entities.flatMap(entityPoints)); s.points = s.points.filter(p => used.has(p.id));
    s.points.push({ id: 'origin', x: -5, y: -5 });
    const add = (constraint: Parameters<typeof edit>[1] & { kind: 'addConstraint' }) => { const r = edit(s, constraint); s = r.sketch; return r.created[0]; };
    add({ kind: 'addConstraint', constraint: { kind: 'fix', point: 'origin' } });
    const x = add({ kind: 'addConstraint', constraint: { kind: 'distanceX', a: 'origin', b: 'corner', value: 5, driving: true } });
    const y = add({ kind: 'addConstraint', constraint: { kind: 'distanceY', a: 'origin', b: 'corner', value: 5, driving: true } });
    for (const e of s.entities) {
      if (e.kind === 'line') add({ kind: 'addConstraint', constraint: { kind: 'fix', point: e.id === 'first' ? 'farA' : 'farB' } });
      if (e.kind === 'arc') {
        add({ kind: 'addConstraint', constraint: { kind: 'fix', point: e.center } });
        add({ kind: 'addConstraint', constraint: { kind: e.id === 'first' ? 'distanceX' : 'distanceY', a: e.center,
          b: e.id === 'first' ? 'farA' : 'farB', value: 0, driving: true } });
      }
    }
    const session = await createSession(s, { references: [] });
    try {
      const before = await session.solveFull(); expect(before.status).toBe('ok'); expect(before.dof).toBe(0);
      const result = edit(session.sketch, { kind: 'fillet', point: 'corner', radius: 2 }); session.sketch = result.sketch;
      const solved = await session.solveFull(); expect(solved.status).toBe('ok'); expect(solved.dof).toBe(0); expect(solved.hasRedundancy).not.toBe(true);
      const arc = result.sketch.entities[1] as Arc, oldCenter = solved.sketch.points.find(p => p.id === arc.center)!;
      const moved = await session.setDimensionContinuation(x, 5.2); expect(moved.ok, moved.reason).toBe(true);
      expect((await session.solveFull()).dof).toBe(0); assertSupports(session.sketch);
      expect(distance(oldCenter, session.sketch.points.find(p => p.id === arc.center)!)).toBeGreaterThan(.01);
      expect(arcGeometry(session.sketch, arc).radius).toBeCloseTo(2, 7);
      let removed = edit(session.sketch, { kind: 'removeConstraint', id: x }).sketch;
      expect(removed.points.some(p => p.id === 'corner')).toBe(true);
      removed = edit(removed, { kind: 'removeConstraint', id: y }).sketch;
      expect(removed.points.some(p => p.id === 'corner')).toBe(false); expect(removed.entities.some(isVirtualSharpHelper)).toBe(false);
    } finally { session.destroy(); }
  });

  it.each(pairs)('preserves point identity and relations on %s, solves and cleans up the last user relation', async pair => {
    const before = fixture(pair), relation = before.constraints[0], result = edit(before, { kind: 'fillet', point: 'corner', radius: 2 });
    expect(result.sketch.constraints).toContainEqual(relation); expect(result.removedConstraints).toEqual([]);
    expect(result.notice).toBeUndefined(); expect(result.sketch.entities.filter(isVirtualSharpHelper)).toHaveLength(2);
    expect(result.sketch.entities.filter(isVirtualSharpHelper).every(e => e.construction && internalHelper(e))).toBe(true);
    expect(parseSketch(toJSON(result.sketch))).toEqual({ ok: true, sketch: result.sketch });
    const session = await createSession(result.sketch, { references: [] });
    try { const solved = await session.solveFull(); expect(solved.status, solved.reason).toBe('ok'); assertSupports(solved.sketch); }
    finally { session.destroy(); }
    const removed = edit(result.sketch, { kind: 'removeConstraint', id: relation.id }).sketch;
    expect(removed.entities.some(isVirtualSharpHelper)).toBe(false); expect(removed.points.some(p => p.id === 'corner')).toBe(false);
    expect(removed.constraints.some(c => constraintRefs(c).includes('corner'))).toBe(false);
  });

  it.each(pairs)('uses the shared selection and dimension tools for the %s sharp', pair => {
    const s = edit(fixture(pair), { kind: 'fillet', point: 'corner', radius: 2 }).sketch;
    const geometry = sketchGeometry(s, [], []), hidden = [...geometry.hiddenCentres, ...geometry.helperPoints];
    expect(geometry.visiblePoints.some(p => p.id === 'corner')).toBe(true);
    expect(geometryHit(geometryCandidates(s, { x: 0, y: 0 }, hidden), .01)?.id).toBe('corner');
    for (const helper of s.entities.filter(isVirtualSharpHelper)) {
      const id = virtualSharpData(helper)!.contact, contact = s.points.find(p => p.id === id)!;
      expect(geometryHit(geometryCandidates(s, contact, hidden), .01)?.id).toBe(id);
    }
    expect(marqueeSelection(s, { x: -.1, y: -.1 }, { x: .1, y: .1 }, hidden)).toContain('corner');
    const candidate = candidateDimension(dimensionPicks(s, ['corner', 'farA'], []), { x: 2, y: 5 });
    expect(candidate).toBeDefined();
    const added = edit(s, { kind: 'addConstraint', constraint: candidate! });
    expect(added.sketch.constraints.some(c => constraintRefs(c).includes('corner') && 'value' in c)).toBe(true);
  });

  it('keeps coincident aliases and all their dimensions, then removes unused aliases together', () => {
    let s = lineCorner(); s.points.push({ id: 'alias', x: 0, y: 0 });
    s.entities[1] = { id: 'second', kind: 'line', p1: 'alias', p2: 'farB' };
    s = edit(s, { kind: 'addConstraint', constraint: { kind: 'coincident', a: 'corner', b: 'alias' } }).sketch;
    const joined = s.constraints[0];
    const relation = edit(s, { kind: 'addConstraint', constraint: { kind: 'distanceY', a: 'alias', b: 'farA', value: 10, driving: true } });
    const after = edit(relation.sketch, { kind: 'fillet', point: 'corner', radius: 2 }).sketch;
    expect(after.constraints).toEqual(expect.arrayContaining(relation.sketch.constraints));
    const removed = edit(after, { kind: 'removeConstraint', id: relation.created[0] });
    expect(removed.sketch.points.some(p => ['corner', 'alias'].includes(p.id))).toBe(false);
    expect(removed.removedConstraints).toContain(joined.id);
  });

  it('deletes a sharp whose coincident alias holds a dimension, with its helpers and aliases, in one step', () => {
    let s = lineCorner(); s.points.push({ id: 'alias', x: 0, y: 0 });
    s.entities[1] = { id: 'second', kind: 'line', p1: 'alias', p2: 'farB' };
    s = edit(s, { kind: 'addConstraint', constraint: { kind: 'coincident', a: 'corner', b: 'alias' } }).sketch;
    const relation = edit(s, { kind: 'addConstraint', constraint: { kind: 'distanceY', a: 'alias', b: 'farA', value: 10, driving: true } });
    const after = edit(relation.sketch, { kind: 'fillet', point: 'corner', radius: 2 }).sketch;
    expect(after.entities.filter(isVirtualSharpHelper)).toHaveLength(2);
    const removed = edit(after, { kind: 'deletePoint', id: 'corner' });
    expect(removed.sketch.entities).toEqual(after.entities.filter(e => !isVirtualSharpHelper(e)));
    expect(removed.sketch.points.some(p => ['corner', 'alias'].includes(p.id))).toBe(false);
    expect(removed.sketch.constraints.some(c => constraintRefs(c).some(id => ['corner', 'alias'].includes(id)))).toBe(false);
    expect(removed.removedConstraints).toEqual(expect.arrayContaining([...relation.created, relation.sketch.constraints[0].id]));
  });

  it('does not create helpers when the only relation is the endpoint join', () => {
    const s = lineCorner(); s.points.push({ id: 'alias', x: 0, y: 0 });
    s.entities[1] = { id: 'second', kind: 'line', p1: 'alias', p2: 'farB' };
    const joined = edit(s, { kind: 'addConstraint', constraint: { kind: 'coincident', a: 'corner', b: 'alias' } });
    const result = edit(joined.sketch, { kind: 'fillet', point: 'corner', radius: 2 });
    expect(result.sketch.entities.some(isVirtualSharpHelper)).toBe(false); expect(result.removedConstraints).toEqual(joined.created);
  });

  it('reuses an existing support relation without introducing a redundant equation', async () => {
    const s = edit(lineCorner(), { kind: 'addConstraint', constraint: { kind: 'pointOnLine', point: 'corner', line: 'first' } }).sketch;
    const result = edit(s, { kind: 'fillet', point: 'corner', radius: 2 });
    expect(result.sketch.constraints.filter(c => c.kind === 'pointOnLine' && c.line === 'first')).toHaveLength(1);
    const session = await createSession(result.sketch, { references: [] });
    try { expect((await session.solveFull()).status).toBe('ok'); } finally { session.destroy(); }
  });

  it('maintains the supporting line when its original user relation is deleted before the last dimension', () => {
    let s = edit(lineCorner(), { kind: 'addConstraint', constraint: { kind: 'pointOnLine', point: 'corner', line: 'first' } }).sketch;
    const relation = s.constraints[0].id;
    s = edit(s, { kind: 'addConstraint', constraint: { kind: 'distanceX', a: 'corner', b: 'farB', value: 10, driving: true } }).sketch;
    s = edit(s, { kind: 'fillet', point: 'corner', radius: 2 }).sketch;
    const removed = edit(s, { kind: 'removeConstraint', id: relation });
    expect(removed.sketch.constraints.some(c => c.id === relation)).toBe(false);
    const support = removed.sketch.constraints.find(c => c.kind === 'pointOnLine' && c.line === 'first')!;
    expect(support).toMatchObject({ kind: 'pointOnLine', point: 'corner' });
    expect(removed.sketch.entities.some(e => virtualSharpData(e)?.constraint === support.id)).toBe(true);
    expect(applyCommand(removed.sketch, { kind: 'removeConstraint', id: support.id })).toHaveProperty('error', expect.stringContaining('Delete the virtual sharp'));
  });

  it.each(pairs)('deletes a selectable %s sharp without deleting the contour', pair => {
    const after = edit(fixture(pair), { kind: 'fillet', point: 'corner', radius: 2 }).sketch;
    const removed = edit(after, { kind: 'deletePoint', id: 'corner' }).sketch;
    expect(removed.entities.some(isVirtualSharpHelper)).toBe(false); expect(removed.points.some(p => p.id === 'corner')).toBe(false);
    expect(removed.entities).toEqual(after.entities.filter(e => !isVirtualSharpHelper(e)));
  });

  it('Make line on an arc supporting a sharp drops the sharp, its relations and the old centre in one step', () => {
    const before = fixture('line–arc'), fix = before.constraints[0].id;
    const after = edit(before, { kind: 'fillet', point: 'corner', radius: 2 }).sketch;
    expect(after.entities.some(e => virtualSharpData(e)?.support === 'second' && e.kind === 'arc')).toBe(true);
    const line = edit(after, { kind: 'convertEntity', id: 'second', to: 'line' });
    expect(line.sketch.entities.some(isVirtualSharpHelper)).toBe(false);
    const used = new Set([...line.sketch.entities.flatMap(entityPoints), ...line.sketch.constraints.flatMap(constraintRefs)]);
    expect(line.sketch.points.filter(p => !used.has(p.id) && p.id !== 'centerA').map(p => p.id)).toEqual([]); // centerA is unused in the line fixture
    expect(line.sketch.points.some(p => ['corner', 'centerB'].includes(p.id))).toBe(false);
    expect(line.removedConstraints).toContain(fix);
    expect(line.notice).toContain('Fix');
  });
});

describe('Cone-and-dust-cap virtual sharp', () => {
  it.each(['cap.rim', 'cone.rim'])('keeps preset dimensions and zero DOF at %s, edits dimensions, lowers and restores in one undo step', async point => {
    const initial = seedPreset(expandedPreset('cone'), 'cone', presetDefaults.cone);
    const controller = new SketchController({ initial, references: initial.meta.references, changed: () => {},
      apply: (state, c) => { const r = applySourceCommand(state.sketch, state.meta, c); return 'error' in r ? r : { sketch: r.sketch, meta: r.meta }; },
      dimensionProblem: (state, id, value) => presetDimensionProblem(state.sketch, state.meta, id, value) });
    try {
      await controller.ready; expect(controller.diagnosis?.dof).toBe(0);
      await controller.command({ kind: 'fillet', point, radius: .3 });
      const after = controller.current;
      expect(controller.diagnosis?.status).toBe('ok'); expect(controller.diagnosis?.dof).toBe(0);
      expect(controller.diagnosis?.hasRedundancy).not.toBe(true);
      for (const c of initial.sketch.constraints) expect(controller.diagnosis?.constraintStates[c.id]).toBe('ok');
      expect(after.sketch.constraints).toEqual(expect.arrayContaining(initial.sketch.constraints));
      expect(after.meta.preset?.dimensions).toEqual(initial.meta.preset?.dimensions);
      for (const parameter of Object.keys(initial.meta.preset!.dimensions)) expect(presetDimensionActive(after.sketch, after.meta, parameter), parameter).toBe(true);
      const lowered = lower(after.sketch, after.meta); expect(lowered.ok).toBe(true);
      const noSharp = structuredClone(after.sketch);
      noSharp.entities = noSharp.entities.filter(e => !internalHelper(e));
      const used = new Set(noSharp.entities.flatMap(entityPoints));
      noSharp.points = noSharp.points.filter(p => used.has(p.id));
      const refs = new Set([...noSharp.points, ...noSharp.entities].map(e => e.id));
      noSharp.constraints = noSharp.constraints.filter(c => constraintRefs(c).every(id => refs.has(id)));
      expect(lower(noSharp, after.meta)).toEqual(lowered);
      expect(sourceContourAdapter.status(after).kind).toBe('valid');
      if (!lowered.ok) throw new Error('Expected lowered contour');
      expect(parseSourceAuthoring(serializeSourceAuthoring(after), lowered.document)).toEqual(after);
      await controller.undo(); expect(controller.current).toEqual(initial); expect(controller.history.canUndo).toBe(false);
      await controller.undo(true); expect(controller.current).toEqual(after);
      const oldFillet = after.sketch.entities.find(e => e.kind === 'arc' && !initial.sketch.entities.some(old => old.id === e.id) && !e.construction) as Arc;
      const oldCenter = after.sketch.points.find(p => p.id === oldFillet.center)!;
      for (const [parameter, value] of [['radius_mm', 11], ['depth_mm', 4.5]] as const) {
        await controller.dimension(after.meta.preset!.dimensions[parameter], value);
        expect(controller.diagnosis?.dof).toBe(0); expect(controller.diagnosis?.status).toBe('ok');
      }
      const next = controller.current.sketch;
      expect(distance(oldCenter, next.points.find(p => p.id === oldFillet.center)!)).toBeGreaterThan(.01);
      expect(arcGeometry(next, next.entities.find(e => e.id === oldFillet.id) as Arc).radius).toBeCloseTo(.3, 7);
      expect(sourceContourAdapter.status(controller.current).kind).toBe('valid');
    } finally { controller.destroy(); }
  });
});
