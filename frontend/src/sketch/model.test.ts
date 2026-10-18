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
