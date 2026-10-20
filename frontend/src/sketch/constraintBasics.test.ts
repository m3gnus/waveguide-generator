// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { importedSketch, applyCommand } from './ops';
import { SketchController } from './controller';
import { parseSketch, type NewConstraint, type Sketch } from './model';
import { createSession } from './session';
import { selectionConstraints } from './tools/constraints';
import { candidateDimension, dimensionPicks } from './tools/dimension';
import { materialiseContext } from './context';
import type { SketchContext } from './context';

const initial = () => importedSketch([
  { id: 'p', x: 2, y: -4 }, { id: 'q', x: 10, y: 0 },
  { id: 'r', x: 3, y: 6 }, { id: 's', x: 14, y: 9 },
  { id: 'o', x: 0, y: 0 }, { id: 'up', x: 0, y: 1 }, { id: 'right', x: 1, y: 0 },
], [
  { id: 'cone', kind: 'line', p1: 'p', p2: 'q' },
  { id: 'other', kind: 'line', p1: 'r', p2: 's' },
  { id: 'axis', kind: 'line', p1: 'o', p2: 'up', reference: true, construction: true },
  { id: 'baffle', kind: 'line', p1: 'o', p2: 'right', reference: true, construction: true },
]);
function controller(sketch: Sketch) {
  return new SketchController({ sketch, meta: null }, [], (s, c) => {
    const r = applyCommand(s.sketch, c); return 'error' in r ? r : { ...s, sketch: r.sketch };
  }, () => {});
}
const at = (s: Sketch, id: string) => s.points.find(p => p.id === id)!;
const vector = (s: Sketch, a: string, b: string) => ({ x: at(s, b).x - at(s, a).x, y: at(s, b).y - at(s, a).y });

describe('constraint basics with the active solver', () => {
  it('derives selections from capabilities, including supported point-on-arc only', async () => {
    const sketch = initial(), session = await createSession(sketch, { references: [] });
    try {
      const capabilities = session.capabilities;
      const menu = (ids: string[]) => selectionConstraints(sketch, ids, capabilities).map(c => c.kind);
      expect(menu(['cone', 'other'])).toEqual(expect.arrayContaining(['parallel', 'perpendicular']));
      expect(menu(['p', 'other'])).toEqual(expect.arrayContaining(['midpoint', 'pointOnLine']));
      expect(menu(['p', 'q'])).not.toContain('parallel');
      expect(menu(['cone'])).not.toContain('midpoint');
      sketch.entities.push({ id: 'arc', kind: 'arc', center: 'o', start: 'up', end: 'right', ccw: true });
      expect(menu(['p', 'arc'])).not.toContain('pointOnArc');
      capabilities.constraints.pointOnArc = { support: 'native', detail: 'test alternate solver' };
      expect(menu(['p', 'arc'])).toContain('pointOnArc');
      capabilities.constraints.parallel = { support: 'unsupported', detail: 'test alternate solver' };
      expect(menu(['cone', 'other'])).not.toContain('parallel');
      expect(selectionConstraints(sketch, ['cone', 'other'])).toEqual([]);
    } finally { session.destroy(); }
  });
  it('never offers Equal or Midpoint for reference lines, while retaining directional constraints', async () => {
    const context: SketchContext = { fingerprint: '1', rimPlaneLabel: 'Rim plane', items: [{ id: 'wall', kind: 'segment', geometry: [{ x: 5, y: 0 }, { x: 5, y: 1 }], role: 'wall', label: 'Wall', pickable: true }] };
    const picked = materialiseContext(initial(), context, 'wall');
    const session = await createSession(picked.sketch, { references: [] });
    try {
      const menu = (ids: string[]) => selectionConstraints(picked.sketch, ids, session.capabilities).map(c => c.kind);
      for (const id of ['axis', 'baffle', picked.id]) {
        for (const ids of [['cone', id], [id, 'cone']]) {
          expect(menu(ids)).not.toContain('equal');
          expect(menu(ids)).toEqual(expect.arrayContaining(['parallel', 'perpendicular']));
        }
        for (const ids of [['p', id], [id, 'p']]) {
          expect(menu(ids)).not.toContain('midpoint');
          expect(menu(ids)).toContain('pointOnLine');
        }
      }
      expect(menu(['cone', 'other'])).toContain('equal');
      expect(menu(['p', 'other'])).toContain('midpoint');
    } finally { session.destroy(); }
  });
  it.each(['parallel', 'perpendicular', 'midpoint', 'pointOnLine'] as const)('applies %s and checks solved geometry', async kind => {
    const c = controller(initial());
    try {
      await c.command({ kind: 'setEntityName', id: 'cone', name: 'Cone' }); // await initialization
      const ids = kind === 'midpoint' || kind === 'pointOnLine' ? ['p', 'other'] : ['cone', 'other'];
      const offered = selectionConstraints(c.current.sketch, ids, c.capabilities).find(v => v.kind === kind)!;
      expect(offered).toBeDefined();
      await c.constraint(offered.constraint);
      const s = c.current.sketch, a = vector(s, 'p', 'q'), b = vector(s, 'r', 's');
      if (kind === 'parallel') expect(a.x * b.y - a.y * b.x).toBeCloseTo(0, 7);
      if (kind === 'perpendicular') expect(a.x * b.x + a.y * b.y).toBeCloseTo(0, 7);
      if (kind === 'pointOnLine') expect((at(s, 'p').x - at(s, 'r').x) * b.y - (at(s, 'p').y - at(s, 'r').y) * b.x).toBeCloseTo(0, 7);
      if (kind === 'midpoint') {
        expect(at(s, 'p').x).toBeCloseTo((at(s, 'r').x + at(s, 's').x) / 2, 8);
        expect(at(s, 'p').y).toBeCloseTo((at(s, 'r').y + at(s, 's').y) / 2, 8);
      }
    } finally { c.destroy(); }
  });
  it.each(['parallel', 'perpendicular', 'pointOnLine'] as const)('uses %s against materialised axis context without moving it', async kind => {
    const context: SketchContext = { fingerprint: 'axis-1', rimPlaneLabel: 'Rim plane', items: [{ id: 'host.axis', kind: 'segment', geometry: [{ x: 0, y: -20 }, { x: 0, y: 20 }], role: 'axis', label: 'Host axis', pickable: true }] };
    const picked = materialiseContext(initial(), context, 'host.axis'), c = controller(picked.sketch);
    const reference = picked.sketch.entities.find(e => e.id === picked.id)!;
    if (reference.kind !== 'line') throw new Error('Expected line');
    const fixed = [at(picked.sketch, reference.p1), at(picked.sketch, reference.p2)];
    try {
      await c.constraint(kind === 'pointOnLine' ? { kind, point: 'p', line: picked.id } : { kind, a: 'cone', b: picked.id });
      expect([at(c.current.sketch, reference.p1), at(c.current.sketch, reference.p2)]).toEqual(fixed);
      if (kind === 'parallel') expect(at(c.current.sketch, 'p').x).toBeCloseTo(at(c.current.sketch, 'q').x, 8);
      if (kind === 'perpendicular') expect(at(c.current.sketch, 'p').y).toBeCloseTo(at(c.current.sketch, 'q').y, 8);
      if (kind === 'pointOnLine') expect(at(c.current.sketch, 'p').x).toBeCloseTo(0, 8);
    } finally { c.destroy(); }
  });
  it('toggles both ways in single undo steps, preserving the dimension ID and placement', async () => {
    const c = controller(initial());
    try {
      await c.constraint({ kind: 'distance', a: 'p', b: 'q', value: Math.sqrt(80), driving: true, placement: { offset: 3, along: .4 } });
      const before = c.current, id = before.sketch.constraints.at(-1)!.id;
      await c.command({ kind: 'setDriving', id, driving: false });
      expect(c.current.sketch.constraints.at(-1)).toMatchObject({ id, driving: false, placement: { offset: 3, along: .4 } });
      await c.undo(); expect(c.current).toBe(before);
      await c.undo(true); const reference = c.current;
      await c.command({ kind: 'setDriving', id, driving: true });
      expect(c.current.sketch.constraints.at(-1)).toMatchObject({ id, driving: true });
      await c.undo(); expect(c.current).toBe(reference);
    } finally { c.destroy(); }
  });
  it('refuses a redundant reference-to-driving toggle and leaves history untouched', async () => {
    const c = controller(initial());
    try {
      await c.constraint({ kind: 'distance', a: 'p', b: 'q', value: Math.sqrt(80), driving: true, label: 'Held length' });
      const held = c.current;
      await c.constraint({ kind: 'distance', a: 'p', b: 'q', value: 1, driving: false, label: 'Measured length' });
      const before = c.current, id = before.sketch.constraints.at(-1)!.id;
      await expect(c.command({ kind: 'setDriving', id, driving: true })).rejects.toThrow(/Conflicts with.*length/);
      expect(c.current).toBe(before);
      await c.undo(); expect(c.current).toBe(held);
    } finally { c.destroy(); }
  });
  it.each(['axis', 'baffle', 'context'].flatMap(referenceId => [false, true].flatMap(reverseCone => [false, true].map(reverseReference => ({ referenceId, reverseCone, reverseReference })))))('sets a cone angle to 30 degrees against $referenceId (cone reversed: $reverseCone, reference reversed: $reverseReference)', async ({ referenceId, reverseCone, reverseReference }) => {
    let sketch = initial();
    if (referenceId === 'context') {
      sketch = { ...sketch, entities: [...sketch.entities, { kind: 'line', id: 'context', p1: 'r', p2: 's', reference: true, construction: true, data: { context: 'wall', fingerprint: '1', label: 'Wall' } }] };
    }
    sketch.entities = sketch.entities.map(e => e.kind === 'line' && (e.id === 'cone' && reverseCone || e.id === referenceId && reverseReference) ? { ...e, p1: e.p2, p2: e.p1 } : e);
    const references = [{ id: 'axis', kind: 'axis' as const }, { id: 'baffle', kind: 'baffle' as const }];
    const ref = sketch.entities.find(e => e.id === referenceId)!;
    if (ref.kind !== 'line') throw new Error('Expected line');
    const fixed = [at(sketch, ref.p1), at(sketch, ref.p2)], c = controller(sketch);
    try {
      const candidate = candidateDimension(dimensionPicks(sketch, ['cone', referenceId], references), { x: 4, y: 8 });
      expect(candidate?.kind).toBe('angle');
      expect(candidate!.value).toBeLessThanOrEqual(Math.PI / 2);
      await c.constraint(candidate as NewConstraint);
      const id = c.current.sketch.constraints.at(-1)!.id;
      await c.dimension(id, Math.PI / 6);
      const s = c.current.sketch, a = vector(s, 'p', 'q'), b = vector(s, ref.p1, ref.p2);
      const angle = Math.acos(Math.abs(a.x * b.x + a.y * b.y) / (Math.hypot(a.x, a.y) * Math.hypot(b.x, b.y)));
      expect(angle).toBeCloseTo(Math.PI / 6, 8);
      expect(s.points).not.toEqual(sketch.points);
      expect([at(s, ref.p1), at(s, ref.p2)]).toEqual(fixed);
      // The branch is authoring metadata and survives persistence and both toggles.
      const parsed = parseSketch(JSON.parse(JSON.stringify(s)));
      expect(parsed.ok).toBe(true);
      expect(parsed.ok && parsed.sketch.constraints.at(-1)).toEqual(s.constraints.at(-1));
      await c.command({ kind: 'setDriving', id, driving: false });
      expect(c.current.sketch.constraints.at(-1)).toMatchObject({ kind: 'angle', value: expect.closeTo(Math.PI / 6, 8), driving: false });
      await c.command({ kind: 'setDriving', id, driving: true });
      expect(c.current.sketch.constraints.at(-1)).toMatchObject({ kind: 'angle', value: expect.closeTo(Math.PI / 6, 8), driving: true });
    } finally { c.destroy(); }
  });
});
