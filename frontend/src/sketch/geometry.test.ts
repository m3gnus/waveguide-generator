import { expect, it } from 'vitest';
import * as geometry from './geometry';

const line = (x: number, y: number, dx: number, dy: number) => ({ kind: 'line' as const, origin: { x, y }, direction: { x: dx, y: dy } });
const circle = (x: number, y: number, radius: number) => ({ kind: 'circle' as const, center: { x, y }, radius });
it('projects one locus onto infinite lines, circles and fixed points', () => {
  expect(geometry.projectToLoci({ x: 3, y: 2 }, [line(0, 0, 1, 0)])).toEqual({ x: 3, y: 0 });
  expect(geometry.projectToLoci({ x: 4, y: 3 }, [circle(0, 0, 10)])).toEqual({ x: 8, y: 6 });
  expect(geometry.projectToLoci({ x: 4, y: 3 }, [{ kind: 'point', point: { x: 1, y: 2 } }])).toEqual({ x: 1, y: 2 });
});
it('projects two loci to the nearest line-line, line-circle or circle-circle intersection', () => {
  expect(geometry.projectToLoci({ x: 3, y: 5 }, [line(0, 2, 1, 0), line(1, 0, 0, 1)])).toEqual({ x: 1, y: 2 });
  expect(geometry.projectToLoci({ x: 1, y: -4 }, [line(0, 0, 0, 1), circle(0, 0, 3)])).toEqual({ x: 0, y: -3 });
  expect(geometry.projectToLoci({ x: 3, y: 5 }, [circle(0, 0, 5), circle(6, 0, 5)])).toEqual({ x: 3, y: 4 });
  expect(geometry.projectToLoci({ x: 1, y: 5 }, [line(0, 0, 0, 1), circle(1, 3, 1)])).toEqual({ x: 0, y: 3 });
});
it('leaves disjoint or excessive loci unprojected and handles coincident and degenerate loci', () => {
  const cursor = { x: 1, y: 5 };
  for (const loci of [[line(0, 0, 1, 0), line(0, 1, 1, 0)], [circle(0, 0, 1), circle(5, 0, 1)],
    [line(0, 2, 1, 0), circle(0, 0, 1)], [circle(0, 0, 5), circle(1, 0, 1)],
    [line(0, 0, 1, 0), line(0, 0, 0, 1), circle(0, 0, 1)], [line(0, 0, 0, 0)]]) {
    expect(geometry.projectToLoci(cursor, loci)).toEqual(cursor);
  }
  expect(geometry.projectToLoci(cursor, [circle(0, 0, 5), circle(0, 0, 5)])).toEqual(geometry.projectToLoci(cursor, [circle(0, 0, 5)]));
  expect(geometry.projectToLoci(cursor, [line(0, 0, 1, 0), line(2, 0, -1, 0)])).toEqual({ x: 1, y: 0 });
});
