// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { emptySketch, parseSketch, toJSON, validateSketch, type Sketch } from './model';
import { applyCommand } from './ops';

describe('sketch JSON and integrity', () => {
  const line = (): Sketch => {
    const result = applyCommand(emptySketch(), { kind: 'addLine', from: { x: 0, y: 0 }, to: { x: 10, y: 0 } });
    if ('error' in result) throw new Error(result.error); return result.sketch;
  };
  it('round trips without rewriting coordinates or adapter JSON', () => {
    const s = line(); s.entities[0].data = { role: 'moving', opaque: [null, false, -.123456789] };
    expect(parseSketch(toJSON(s))).toEqual({ ok: true, sketch: s });
  });
  it('preserves optional dimension names through JSON and edits and rejects non-string labels', () => {
    const base = line();
    const added = applyCommand(base, { kind: 'addConstraint', constraint: { kind: 'distanceX', a: base.points[0].id, b: base.points[1].id, value: 10, driving: true, label: 'Radius' } });
    if ('error' in added) throw new Error(added.error);
    let sketch = added.sketch;
    for (const command of [{ kind: 'setDimension', id: added.created[0], value: 12 }, { kind: 'setDriving', id: added.created[0], driving: false }] as const) {
      const edited = applyCommand(sketch, command); if ('error' in edited) throw new Error(edited.error); sketch = edited.sketch;
      expect(sketch.constraints[0].label).toBe('Radius');
    }
    expect(parseSketch(toJSON(sketch))).toEqual({ ok: true, sketch });
    expect(parseSketch({ ...sketch, constraints: [{ ...sketch.constraints[0], label: 4 }] }).ok).toBe(false);
  });
  it.each([null, false, 42, [], '{', {}, { version: 'wg-sketch-v1' }])('never throws parsing malformed input %j', raw => {
    expect(parseSketch(raw).ok).toBe(false);
  });
  it('keeps an unknown version untouched', () => {
    const raw = { version: 'wg-sketch-v9', future: { constraints: ['new'] } };
    const result = parseSketch(raw); expect(result).toMatchObject({ ok: false, unknownVersion: true });
    expect('raw' in result && result.raw).toBe(raw);
    expect(parseSketch(JSON.stringify(raw))).toMatchObject({ raw: JSON.stringify(raw), unknownVersion: true });
  });
  it('rejects duplicate, dangling, nonfinite and reserved input', () => {
    const s = line();
    expect(validateSketch({ ...s, points: [...s.points, s.points[0]] }).ok).toBe(false);
    expect(validateSketch({ ...s, nextId: 1 }).ok).toBe(false);
    expect(validateSketch({ ...s, points: s.points.slice(1) }).ok).toBe(false);
    expect(validateSketch({ ...s, points: [{ id: 'p9', x: Infinity, y: 0 }], nextId: 10 }).ok).toBe(false);
    expect(validateSketch({ ...s, entities: [{ kind: 'ellipse', id: 'e1' }] }).ok).toBe(false);
    expect(validateSketch({ ...s, constraints: [{ kind: 'radius', id: 'c1', arc: s.entities[0].id, value: 2, driving: true }] }).ok).toBe(false);
    expect(validateSketch({ ...s, constraints: [{ kind: 'unknown', id: 'c1' }] }).ok).toBe(false);
    expect(validateSketch({ ...s, constraints: [{ kind: 'distance', id: 'c1', a: s.points[0].id, b: s.points[1].id }] }).ok).toBe(false);
    const cyclic: Record<string, unknown> = {}; cyclic.self = cyclic;
    expect(validateSketch({ ...s, data: cyclic }).ok).toBe(false);
  });
});

it('round trips optional dimension fields and validates their allowed kinds without rewriting old sketches', () => {
  const s: Sketch = { version: 'wg-sketch-v1', nextId: 10,
    points: [{ id: 'p1', x: 0, y: 0 }, { id: 'p2', x: 0, y: 1 }, { id: 'p3', x: 5, y: 0 }, { id: 'p4', x: 0, y: 5 }],
    entities: [{ id: 'axis', kind: 'line', p1: 'p1', p2: 'p2', reference: true, construction: true }, { id: 'a5', kind: 'arc', center: 'p1', start: 'p3', end: 'p4', ccw: true }],
    constraints: [{ id: 'c6', kind: 'radius', arc: 'a5', value: 5, driving: true, diameter: true, expression: '2*5' },
      { id: 'c7', kind: 'distanceX', a: 'p1', b: 'p3', value: 5, driving: true, diameter: true },
      { id: 'c8', kind: 'pointLineDistance', point: 'p4', line: 'axis', value: 0, driving: true, diameter: true }],
  };
  expect(parseSketch(toJSON(s))).toEqual({ ok: true, sketch: s });
  const invalid = (fields: Record<string, unknown>) => validateSketch({ ...s, constraints: [{ ...s.constraints[0], ...fields }] });
  expect(invalid({ expression: 'x'.repeat(201) }).ok).toBe(false); expect(invalid({ driving: false }).ok).toBe(false);
  expect(invalid({ diameter: false }).ok).toBe(false); expect(invalid({ expression: 5 }).ok).toBe(false);
  expect(invalid({ kind: 'distanceY', a: 'p1', b: 'p3' }).ok).toBe(false);
  expect(invalid({ kind: 'fix', point: 'p1' }).ok).toBe(false);
  const old = { ...s, constraints: s.constraints.map(({ expression: _e, diameter: _d, ...c }) => c) }, bytes = JSON.stringify(old);
  const parsed = parseSketch(bytes); expect(parsed.ok && toJSON(parsed.sketch)).toBe(bytes);
});
