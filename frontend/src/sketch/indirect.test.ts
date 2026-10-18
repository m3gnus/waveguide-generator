// @vitest-environment node
import { describe, expect, it } from 'vitest';
import type { SourceDocument } from '../api/sourceEditor';
import { rules, seed } from './adapters/sourceContour';
import { applyCommand } from './ops';
import type { Sketch } from './model';
import type { Command } from './commands';
import { createSession } from './session';

const run = (s: Sketch, c: Command) => { const r = applyCommand(s, c); if ('error' in r) throw new Error(r.error); return r.sketch; };
const at = (s: Sketch, id: string) => s.points.find(p => p.id === id)!;
// Cone with a dust cap, surround and rigid land: the land is held horizontal, so the
// surround/land joint is constrained only indirectly (no direct locus to project onto).
function cone(): ReturnType<typeof seed> {
  const doc: SourceDocument = {
    contour: { version: 1, physical_source_id: 'diaphragm', rim_id: 'source.rim', points: [
      { id: 'pole', r_mm: 0, z_mm: -2 }, { id: 'cap', r_mm: 3, z_mm: -4 }, { id: 'cone', r_mm: 10, z_mm: 0 },
      { id: 'sr', r_mm: 12, z_mm: 0 }, { id: 'rim', r_mm: 13, z_mm: 0 }],
    segments: [
      { id: 'dustcap', start: 'pole', end: 'cap', role: 'moving', kind: 'arc', center_mm: [0, -2 - Math.hypot(3, 2) ** 2 / 4], direction: 'cw' },
      { id: 'body', start: 'cap', end: 'cone', role: 'moving', kind: 'line', center_mm: null, direction: 'ccw' },
      { id: 'surround', start: 'cone', end: 'sr', role: 'moving', kind: 'arc', center_mm: [11, -0.75], direction: 'cw' },
      { id: 'land', start: 'sr', end: 'rim', role: 'rigid', kind: 'line', center_mm: null, direction: 'ccw' }] },
    drive: { channel_id: 'motor', motion: 'normal', weights: { dustcap: 1, body: 1, surround: 1 } },
  };
  // Make the dust-cap arc exact: centre on the axis, equidistant from pole and cap.
  const [px, pz] = [0, -2], [cx, cz] = [3, -4], k = (cx * cx + cz * cz - px * px - pz * pz) / (2 * (cz - pz));
  doc.contour.segments[0].center_mm = [0, k];
  return seed(doc);
}

describe('indirectly constrained drags', () => {
  it.each([0.1, 0.5])('dragging the surround/land joint with cursor %s above the baffle leaves the cone still', async offset => {
    const seeded = cone(); const sketch = run(seeded.sketch, { kind: 'addConstraint', constraint: { kind: 'horizontal', line: 'land' } });
    const session = await createSession(sketch, { references: seeded.meta.references });
    const cone0 = at(sketch, 'cone');
    try {
      await session.beginDrag(['sr']);
      for (const x of [...Array.from({ length: 31 }, (_, i) => 12 - i / 30), ...Array.from({ length: 31 }, (_, i) => 11 + i / 30)])
        await session.dragTo({ pointId: 'sr', x, y: offset });
      await session.endDrag();
      const s = session.sketch;
      expect(Math.hypot(at(s, 'cone').x - cone0.x, at(s, 'cone').y - cone0.y)).toBeLessThan(1e-6);
      expect(Math.abs(at(s, 'sr').x - 12)).toBeLessThan(2e-3);
      expect(rules(s, seeded.meta)).toEqual([]);
    } finally { session.destroy(); }
  }, 30000);

  it.each([0.05, 0.5])('a rim held level by a construction line does not invert the dome (cursor %s off)', async offset => {
    // Rim kept at z=0 through horizontal(rim, anchor) with a fixed anchor point instead of a baffle lock.
    const doc: SourceDocument = {
      contour: { version: 1, physical_source_id: 'diaphragm', rim_id: 'source.rim', points: [{ id: 'pole', r_mm: 0, z_mm: 3 }, { id: 'rim', r_mm: 8, z_mm: 0 }],
        segments: [{ id: 'piston', start: 'pole', end: 'rim', role: 'moving', kind: 'arc', center_mm: [0, (9 - 64) / 6], direction: 'cw' }] },
      drive: { channel_id: 'motor', motion: 'normal', weights: { piston: 1 } },
    };
    const seeded = seed(doc);
    let sketch = seeded.sketch;
    const baffleLock = sketch.constraints.find(c => c.kind === 'pointOnLine' && c.point === 'rim')!;
    sketch = run(sketch, { kind: 'removeConstraint', id: baffleLock.id });
    const added = applyCommand(sketch, { kind: 'addPoint', x: 20, y: 0 }); if ('error' in added) throw new Error(added.error);
    const anchor = added.created[0]; sketch = added.sketch;
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'fix', point: anchor } });
    // A construction line rim-anchor held horizontal: the rim's locus is indirect (line form).
    const link = applyCommand(sketch, { kind: 'addLine', from: 'rim', to: anchor }); if ('error' in link) throw new Error(link.error);
    const linkId = link.created.find(id => link.sketch.entities.some(e => e.id === id))!; sketch = link.sketch;
    sketch = run(sketch, { kind: 'setConstruction', id: linkId, construction: true });
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'horizontal', line: linkId } });
    const session = await createSession(sketch, { references: seeded.meta.references });
    try {
      await session.beginDrag(['rim']);
      for (const x of [...Array.from({ length: 26 }, (_, i) => 8 + i / 20), ...Array.from({ length: 26 }, (_, i) => 9.25 - i / 20)])
        await session.dragTo({ pointId: 'rim', x, y: offset });
      await session.endDrag();
      expect(Math.abs(at(session.sketch, 'pole').y - 3)).toBeLessThan(1e-6);
    } finally { session.destroy(); }
  }, 30000);
});
