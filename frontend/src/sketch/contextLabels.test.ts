// @vitest-environment node
import { expect, it } from 'vitest';
import { contextLabelLayout } from './contextLabels';

const viewport = { width: 640, height: 360 };
it('anchors labels six pixels away from a line on the side away from the contour', () => {
  const anchor = { x: 200, y: 150 }, points = [{ x: 200, y: 50 }, { x: 200, y: 250 }];
  for (const x of [100, 300]) {
    const layout = contextLabelLayout(anchor, points, [{ x, y: 150 }], 'Wall', viewport)!;
    expect(Math.hypot(layout.at.x - anchor.x, layout.at.y - anchor.y)).toBe(6);
    expect(x < anchor.x ? layout.box.x > anchor.x : layout.box.x + layout.box.width < anchor.x).toBe(true);
  }
});
it('keeps a limit at the viewport edge anchored, while hiding a line label that has no room', () => {
  const anchor = { x: 608, y: 180 };
  const layout = contextLabelLayout(anchor, [anchor], [{ x: 100, y: 100 }], 'Throat r 12.70 🔒', viewport)!;
  expect(Math.hypot(layout.at.x - anchor.x, layout.at.y - anchor.y)).toBe(6);
  expect(layout.box.x + layout.box.width).toBeLessThanOrEqual(636);
  expect(contextLabelLayout(anchor, [{ x: 608, y: 100 }, { x: 608, y: 200 }], [{ x: 100, y: 100 }], 'Enclosure side', viewport)).toBeUndefined();
});

it('keeps a hovered side-wall label visible on another side, six pixels from the geometry', () => {
  const anchor = { x: 608, y: 180 };
  const layout = contextLabelLayout(anchor, [{ x: 608, y: 100 }, { x: 608, y: 200 }], [{ x: 100, y: 100 }], 'Enclosure side (nearest, +x) 200.00 mm', viewport, true)!;
  expect(Math.hypot(layout.at.x - anchor.x, layout.at.y - anchor.y)).toBe(6);
  expect(layout.box.x).toBeGreaterThanOrEqual(4);
  expect(layout.box.x + layout.box.width).toBeLessThanOrEqual(636);
});

it('renders hover text inside the visible corner even when the preferred margin cannot fit', () => {
  const anchor = { x: 0, y: 0 };
  const layout = contextLabelLayout(anchor, [anchor], [{ x: 100, y: 100 }], 'Corner marker', viewport, true)!;
  expect(Math.hypot(layout.at.x, layout.at.y)).toBe(6);
  expect(layout.box.x).toBeGreaterThanOrEqual(0);
  expect(layout.box.y).toBeGreaterThanOrEqual(0);
});
