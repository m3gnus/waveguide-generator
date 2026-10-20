// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { visibleAnchor, fit, gridStep, pan, screenToWorld, worldToScreen, zoomAt } from './view';

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

it('anchors labels to visible geometry, including segments with both endpoints off screen', () => {
  const viewport = { width: 640, height: 360 };
  expect(visibleAnchor([{ x: 10, y: -100 }, { x: 10, y: 500 }], viewport)).toEqual({ x: 10, y: 180 });
  expect(visibleAnchor([{ x: -100, y: 20 }, { x: 800, y: 20 }], viewport)).toEqual({ x: 320, y: 20 });
  expect(visibleAnchor([{ x: 700, y: -100 }, { x: 700, y: 500 }], viewport)).toBeUndefined();
  expect(visibleAnchor([{ x: 20, y: -1 }], viewport)).toBeUndefined();
});

it('anchors hover text at the nearest visible polyline point to the cursor', () => {
  const viewport = { width: 640, height: 360 };
  expect(visibleAnchor([{ x: 10, y: -100 }, { x: 10, y: 500 }], viewport, { x: 12, y: 250 })).toEqual({ x: 10, y: 250 });
  expect(visibleAnchor([{ x: -100, y: 20 }, { x: 800, y: 20 }, { x: 600, y: 100 }], viewport, { x: 600, y: 98 })).toEqual({ x: expect.closeTo(600.6896551724137, 10), y: expect.closeTo(99.72413793103448, 10) });
});

it('classifies trackpad scroll, pinch, and discrete mouse wheels in one policy', async () => {
  const { wheelGesture } = await import('./view');
  const event = (deltaY: number, options = {}) => ({ deltaX: 0, deltaY, deltaMode: 0, ctrlKey: false, ...options });
  expect(wheelGesture(event(2))).toEqual({ kind: 'pan', dx: -0, dy: -2 });
  expect(wheelGesture(event(80, { deltaX: 5 }))).toEqual({ kind: 'pan', dx: -5, dy: -80 });
  expect(wheelGesture(event(-12))).toEqual({ kind: 'pan', dx: -0, dy: 12 });
  expect(wheelGesture(event(100))).toEqual({ kind: 'zoom', factor: Math.exp(-.1) });
  expect(wheelGesture(event(3, { deltaMode: 1 }))).toEqual({ kind: 'zoom', factor: Math.exp(-.048) });
  expect(wheelGesture(event(1, { deltaMode: 2 }))).toEqual({ kind: 'zoom', factor: Math.exp(-.36) });
  expect(wheelGesture(event(-2, { ctrlKey: true }))).toEqual({ kind: 'zoom', factor: Math.exp(.02) });
  expect(wheelGesture(event(0, { ctrlKey: true }))).toEqual({ kind: 'zoom', factor: 1 });
});
