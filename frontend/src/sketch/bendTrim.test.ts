// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { arcGeometry, distance } from './geometry';
import { ArcBodyDrag } from './tools/arcBodyDrag';
import { SketchSession } from './session';
import { createSolver } from './solver/types';
import { trimmedArc } from './bendTestSupport';

describe('bending a trimmed converted stub', () => {
  it.each([0, .01, .3, 1, -1].flatMap(dx => [1, -1].map(direction => ({ dx, direction, radius: 2.57 as number | null }))).concat([1, -1].map(direction => ({ dx: .01, direction, radius: null }))))('follows small steps ($dx, $direction, radius $radius) and keeps the result on release', async ({ dx, direction, radius }) => {
    const { sketch, references, arc } = trimmedArc(dx, radius);
    const session = await SketchSession.create(sketch, { references, solverFactory: () => createSolver('slvs') });
    try {
      expect((await session.solveFull()).status).toBe('ok');
      const start = session.sketch, g = arcGeometry(start, arc), angle = g.start + g.sweep / 2;
      const body = { x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) };
      const drag = new ArcBodyDrag(start, arc.id, 'bend');
      await session.beginDrag([arc.center, arc.start, arc.end]);
      const chord = distance(start.points.find(p => p.id === arc.start)!, start.points.find(p => p.id === arc.end)!);
      const step = direction === 1 ? .02 : Math.min(.005, chord ** 2 / (8 * g.radius) / 60);
      for (let i = 1; i <= 30; i++) {
        const cursor = { x: body.x + direction * step * i * Math.cos(angle), y: body.y + direction * step * i * Math.sin(angle) };
        const targets = drag.targets(session.sketch, cursor, false), result = await session.dragTo(targets);
        expect(result.stopped, `${i}: ${result.reason}`).toBe(false);
        if (!drag.clamped) {
          const bent = arcGeometry(result.sketch, arc);
          expect(Math.abs(distance(bent.center, cursor) - bent.radius)).toBeLessThan(.001);
        }
        for (const t of targets) expect(distance(result.sketch.points.find(p => p.id === t.pointId)!, t)).toBeLessThanOrEqual(.001);
      }
      expect(distance(start.points.find(p => p.id === arc.center)!, session.sketch.points.find(p => p.id === arc.center)!)).toBeGreaterThan(.01);
      const beforeRelease = session.sketch;
      expect((await session.endDrag()).status).toBe('ok');
      expect(distance(beforeRelease.points.find(p => p.id === arc.center)!, session.sketch.points.find(p => p.id === arc.center)!)).toBeLessThan(.001);
      for (const id of [arc.start, arc.end]) expect(distance(start.points.find(p => p.id === id)!, session.sketch.points.find(p => p.id === id)!)).toBeLessThan(.001);
    } finally { session.destroy(); }
  });
});
