import { renderToStaticMarkup } from 'react-dom/server';
import { SVGLoader } from 'three/addons/loaders/SVGLoader.js';
import { describe, expect, it } from 'vitest';
import { Icon } from './icons';

// Sample actual curves: arc radii and control points are not their extents.
const viewportIcons = ['clay', 'wire', 'xray', 'zebra', 'curve', 'section', 'field-plane', 'microphone-arc', 'settings', 'reset'] as const;

describe('viewport toolbar icon geometry', () => {
  it.each(viewportIcons)('%s fills and stays centred in its 16px canvas', (name) => {
    const svg = renderToStaticMarkup(<Icon name={name}/>);
    const parsed = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement;
    expect(parsed.getAttribute('viewBox')).toBe('0 0 16 16');
    expect(parsed.getAttribute('stroke-width')).toBe('1.3');
    expect(parsed.getAttribute('stroke-linecap')).toBe('round');
    const points = new SVGLoader().parse(svg).paths.flatMap((path) => path.subPaths.flatMap((part) => part.getPoints(100)));
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    const left = Math.min(...xs), right = Math.max(...xs);
    const top = Math.min(...ys), bottom = Math.max(...ys);
    expect(right - left).toBeGreaterThanOrEqual(9);
    expect(Math.abs((left + right) / 2 - 8)).toBeLessThan(1);
    expect(Math.abs((top + bottom) / 2 - 8)).toBeLessThan(1);
    // Include half the stroke when checking for clipping.
    expect(left - 0.65).toBeGreaterThanOrEqual(0);
    expect(top - 0.65).toBeGreaterThanOrEqual(0);
    expect(right + 0.65).toBeLessThanOrEqual(16);
    expect(bottom + 0.65).toBeLessThanOrEqual(16);
  });

  it('distinguishes the source and three measurement positions', () => {
    const svg = renderToStaticMarkup(<Icon name="microphone-arc"/>);
    const contours = new SVGLoader().parse(svg).paths.flatMap((path) => path.subPaths);
    const markers = contours.filter((part) => part.autoClose && part.getLength() < 6);
    expect(markers).toHaveLength(4);
    const centres = markers.map((part) => {
      const points = part.getPoints(100);
      return [(Math.min(...points.map((p) => p.x)) + Math.max(...points.map((p) => p.x))) / 2,
        (Math.min(...points.map((p) => p.y)) + Math.max(...points.map((p) => p.y))) / 2];
    }).sort((a, b) => a[0] - b[0]);
    expect(centres[0][0]).toBeLessThan(4);
    expect(centres.slice(1).every(([x]) => x > 7)).toBe(true);
    expect(Math.min(...centres.map(([, y]) => y))).toBeLessThan(4);
    expect(Math.max(...centres.map(([, y]) => y))).toBeGreaterThan(12);
  });
});
