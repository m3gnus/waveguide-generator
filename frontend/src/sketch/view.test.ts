// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { fit, gridStep, pan, screenToWorld, worldToScreen, zoomAt } from './view';

describe('true-aspect view', () => {
  it('fits all bounds at one scale with z pointing up', () => {
    const v = fit({ minX: -2, minY: -1, maxX: 8, maxY: 4 }, { width: 400, height: 200 }, 20);
    expect(v.scale).toBe(32);
    const a = worldToScreen(v, { x: -2, y: -1 }), b = worldToScreen(v, { x: 8, y: 4 });
    expect(a.x).toBeGreaterThanOrEqual(20); expect(a.y).toBe(180); expect(b.y).toBe(20);
    expect(screenToWorld(v, a)).toEqual({ x: -2, y: -1 });
    expect(fit({ minX: 0, minY: 0, maxX: 0, maxY: 0 }, { width: 100, height: 100 }).scale).toBeGreaterThan(0);
  });
  it('keeps the cursor world coordinate under zoom and pans in screen pixels', () => {
    const v = { scale: 12, offsetX: 25, offsetY: 150 }, cursor = { x: 102, y: 87 };
    const zoomed = zoomAt(v, cursor, 1.7);
    const a = screenToWorld(v, cursor), b = screenToWorld(zoomed, cursor);
    expect(b.x).toBeCloseTo(a.x, 12); expect(b.y).toBeCloseTo(a.y, 12);
    expect(worldToScreen(pan(v, 10, -20), { x: 0, y: 0 })).toEqual({ x: 35, y: 130 });
    expect(v.scale).toBe(12);
  });
  it('chooses the smallest 1/2/5 step with at least eight pixels', () => {
    for (const scale of [.001, .1, 1, 3, 8, 40, 1000]) {
      const step = gridStep(scale), mantissa = step / 10 ** Math.floor(Math.log10(step));
      expect([1, 2, 5].some(v => Math.abs(v - mantissa) < 1e-9)).toBe(true); expect(step * scale).toBeGreaterThanOrEqual(8);
    }
    expect(() => gridStep(0)).toThrow(); expect(() => zoomAt({ scale: 1, offsetX: 0, offsetY: 0 }, { x: 0, y: 0 }, -1)).toThrow();
    expect(() => fit({ minX: 2, maxX: 1, minY: 0, maxY: 1 }, { width: 100, height: 100 })).toThrow();
  });
});
