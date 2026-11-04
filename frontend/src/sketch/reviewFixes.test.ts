import { describe, expect, it } from 'vitest';
import { applyCommand, importedSketch } from './ops';
import type { Command } from './commands';
import { validateSketch, type Sketch } from './model';
import { SketchController } from './controller';
import { formatReference, resolveExpressions } from './expressions';
import { inspectConstraints } from './inspector';
import { filletPreview } from './topology';
import { arcGeometry } from './geometry';
import { applySourceCommand, rules } from './adapters/sourceContour';
import { seedPreset, presetDefaults } from './adapters/sourcePreset';
import { expandedPreset } from './adapters/presetTestSupport';

function radial(expression?: string): Sketch {
  const s = importedSketch([{ id: 'p1', x: 0, y: 0 }, { id: 'p2', x: 10, y: 0 }, { id: 'p3', x: 0, y: 10 }, { id: 'p6', x: 30, y: 0 }, { id: 'p7', x: 50, y: 0 }],
    [{ id: 'a4', kind: 'arc', center: 'p1', start: 'p2', end: 'p3', ccw: true }, { id: 'l8', kind: 'line', p1: 'p6', p2: 'p7', construction: true }]);
  s.constraints = [{ id: 'c5', kind: 'radius', arc: 'a4', value: 10, driving: true, label: 'Radius', ...(expression ? { expression } : {}) },
    { id: 'c9', kind: 'distance', a: 'p6', b: 'p7', value: 20, driving: true, expression: 'Radius*2' }];
  s.nextId = 10; return s;
}
function edit(sketch: Sketch, command: Command) {
  const r = applyCommand(sketch, command); if ('error' in r) throw new Error(r.error); return r;
}
const values = (s: Sketch) => s.constraints.filter(c => 'value' in c).map(c => 'value' in c && c.value);

describe('review fixes: expressions', () => {
  it.each([undefined, '5*2', '(5)+(10)/2', '(20)/2'])('keeps values and geometry across diameter toggles with own expression %s', expression => {
    const before = radial(expression), on = edit(before, { kind: 'setDiameter', id: 'c5', diameter: true }).sketch;
    expect(values(on)).toEqual([10, 20]); expect(on.points).toEqual(before.points);
    expect(on.constraints[1].expression).toBe('(Radius/2)*2');
    if (expression) expect(on.constraints[0].expression).toBe(expression === '(20)/2' ? '20' : `(${expression})*2`);
    expect(resolveExpressions(on).errors).toEqual([]); expect(values(resolveExpressions(on).sketch)).toEqual([10, 20]);
    expect(edit(on, { kind: 'setDiameter', id: 'c5', diameter: true }).sketch).toEqual(on);
    const off = edit(on, { kind: 'setDiameter', id: 'c5', diameter: false }).sketch;
    expect(off.constraints.map(c => c.expression)).toEqual(before.constraints.map(c => c.expression));
    expect(values(off)).toEqual(values(before)); expect(off.points).toEqual(before.points);
    expect(values(resolveExpressions(off).sketch)).toEqual(values(before));
  });
  it.each([false, true])('round-trips 199-character expressions ten times from diameter=%s', diameter => {
    const own = `${diameter ? '20' : '10'} ${'+0'.repeat(98)}`;
    const dependent = diameter ? `Radius ${'+0'.repeat(96)}` : `[Radius]+c5${'+0'.repeat(94)}`;
    expect(own).toHaveLength(199); expect(dependent).toHaveLength(199);
    let s = radial(own);
    if (diameter) s.constraints[0].diameter = true;
    s.constraints[1].expression = dependent;
    const original = s.constraints.map(c => c.expression);
    for (let i = 0; i < 10; i++) {
      for (const next of [!diameter, diameter]) {
        s = edit(s, { kind: 'setDiameter', id: 'c5', diameter: next }).sketch;
        expect(validateSketch(s)).toEqual({ ok: true });
        expect(values(s)).toEqual([10, 20]);
        const resolved = resolveExpressions(s);
        expect(resolved.errors).toEqual([]); expect(values(resolved.sketch)).toEqual([10, 20]);
      }
      expect(s.constraints.map(c => c.expression)).toEqual(original);
    }
  });
  it.each([undefined, '5*2'])('uses one undo step for all diameter expression rewrites (%s)', async expression => {
    const initial = { sketch: radial(expression), meta: null };
    const c = new SketchController({ initial, references: [], apply: (state, command) => {
      const result = applyCommand(state.sketch, command); return 'error' in result ? result : { ...state, sketch: result.sketch };
    }, changed: () => {} });
    try {
      await c.ready;
      await c.command({ kind: 'setDiameter', id: 'c5', diameter: true });
      expect(values(c.current.sketch)).toEqual([10, 20]); expect(c.current.sketch.points).toEqual(initial.sketch.points);
      await c.undo(); expect(c.current).toEqual(initial); expect(c.history.canUndo).toBe(false);
    } finally { c.destroy(); }
  });
  it('uses one reference formatter for unencodable labels, reserved names and non-identifier IDs', () => {
    const s = radial();
    s.constraints[0].label = 'Width]'; expect(formatReference(s, 'c5')).toBe('c5');
    s.constraints[0].label = 'pi'; expect(formatReference(s, 'c5')).toBe('[pi]');
    s.constraints[0].id = 'radius-id'; s.constraints[0].label = 'Width]';
    expect(formatReference(s, 'radius-id')).toBe('[radius-id]');
    s.constraints[1].expression = `${formatReference(s, 'radius-id')}*2`;
    expect(values(resolveExpressions(s).sketch)).toEqual([10, 20]);
  });
  it('refuses a rename to another dimension ID before it can redirect references', () => {
    const before = radial();
    const { expression: _expression, ...literal } = before.constraints[1];
    before.constraints[1] = literal;
    before.constraints.push({ ...literal, id: 'c10', expression: 'Radius*2' }); before.nextId = 11;
    const snapshot = structuredClone(before);
    expect(applyCommand(before, { kind: 'setConstraintLabel', id: 'c5', label: 'c9' }))
      .toEqual({ error: "c9 is already a dimension's id; choose another name." });
    expect(before).toEqual(snapshot);
  });
  it('loads unresolved expressions unchanged and surfaces repairable inspector and domain errors', () => {
    const { sketch, meta } = seedPreset(expandedPreset('flat'), 'flat', presetDefaults.flat);
    const id = meta.preset!.dimensions.radius_mm;
    sketch.constraints.find(c => c.id === id)!.expression = 'Missing*2';
    const before = structuredClone(sketch);
    expect(validateSketch(sketch)).toEqual({ ok: true });
    expect(inspectConstraints(sketch).find(c => c.id === id)).toMatchObject({ status: 'broken expression', error: expect.stringContaining('Missing') });
    expect(rules(sketch, meta)).toContainEqual(expect.objectContaining({ code: 'expression', ids: [id], message: expect.stringMatching(/Expression for .+ uses an unknown name; edit or clear it\./) }));
    expect(sketch).toEqual(before);
  });
  it('allows clearing an imported unresolved expression without trying to resolve its old name', () => {
    const s = radial(); s.constraints[1].expression = 'Missing*2';
    const cleared = edit(s, { kind: 'setDimensionExpression', id: 'c9', expression: '' });
    expect(cleared.sketch.constraints[1]).not.toHaveProperty('expression'); expect(values(cleared.sketch)).toEqual([10, 20]);
  });
});

it('freezes a dome surround-depth dependent before removing its preset helper', () => {
  const state = seedPreset(expandedPreset('dome'), 'dome', presetDefaults.dome), id = state.meta.preset!.dimensions.surround_depth_mm;
  let sketch = edit(state.sketch, { kind: 'addLine', from: { x: 30, y: 0 }, to: { x: 30, y: 2 * presetDefaults.dome.surround_depth_mm } }).sketch;
  const line = sketch.entities.at(-1)!; if (line.kind !== 'line') throw new Error('Expected line');
  sketch = edit(sketch, { kind: 'setConstruction', id: line.id, construction: true }).sketch;
  sketch = edit(sketch, { kind: 'addConstraint', constraint: { kind: 'distance', a: line.p1, b: line.p2, driving: true, value: 2 * presetDefaults.dome.surround_depth_mm, expression: `[${id}]*2`, label: 'Helper length' } }).sketch;
  const dependent = sketch.constraints.at(-1)!;
  const arc = arcGeometry(sketch, sketch.entities.find(e => e.id === 'surround')! as import('./model').Arc);
  const angle = arc.start + arc.sweep / 2, at = { x: arc.center.x + arc.radius * Math.cos(angle), y: arc.center.y + arc.radius * Math.sin(angle) };
  const result = applySourceCommand(sketch, state.meta, { kind: 'splitAt', entity: 'surround', ...at });
  if ('error' in result) throw new Error(result.error);
  expect(result.sketch.constraints.find(c => c.id === dependent.id)).toMatchObject({ value: 2 * presetDefaults.dome.surround_depth_mm });
  expect(result.sketch.constraints.find(c => c.id === dependent.id)).not.toHaveProperty('expression');
  expect(result.sketch.entities.some(e => e.data && typeof e.data === 'object' && !Array.isArray(e.data) && e.data.dimensionHelper)).toBe(false);
  expect(result.meta.preset!.dimensions.surround_depth_mm).toBeUndefined();
  expect(result.notice).toContain('Split kept Helper length at its last value');
});

it.each([false, true])('fillets either endpoint of a snapped corner (second=%s), keeping far ends and reporting lost coincidence', second => {
  const s = importedSketch([{ id: 'p1', x: 0, y: 10 }, { id: 'p2', x: 0, y: 0 }], [{ id: 'l3', kind: 'line', p1: 'p1', p2: 'p2' }]);
  const drawn = edit(s, { kind: 'addLine', from: { x: 0, y: 0, snap: { glyph: 'endpoint', x: 0, y: 0, constraint: { kind: 'coincident', point: 'p2' } } }, to: { x: 10, y: 0 } }).sketch;
  const line = drawn.entities[1]; if (line.kind !== 'line') throw new Error('Expected line');
  const point = second ? line.p1 : 'p2';
  expect(filletPreview(drawn, point, 2)).toMatchObject({ center: { x: 2, y: 2 } });
  const result = edit(drawn, { kind: 'fillet', point, radius: 2 });
  expect(result.sketch.entities.find(e => e.id === 'l3')).toMatchObject({ p1: 'p1' });
  expect(result.sketch.entities.find(e => e.id === line.id)).toMatchObject({ p2: line.p2 });
  expect(result.sketch.constraints.some(c => c.kind === 'coincident')).toBe(false);
  expect(result.sketch.points.some(p => p.id === 'p2' || p.id === line.p1)).toBe(false);
  expect(result.notice).toContain('Coincident'); expect(validateSketch(result.sketch)).toEqual({ ok: true });
});
