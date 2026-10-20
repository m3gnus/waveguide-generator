import type { XY } from './solver/types';
import { visibleAnchor, type Viewport } from './view';
import type { LabelBox } from './dimensionGraphics';

/** Keep labels at their geometry, preferring the side away from the contour. */
export function contextLabelLayout(anchor: XY, points: XY[], contour: XY[], label: string, viewport: Viewport, hovered = false) {
  const nearest = contour.reduce<XY | undefined>((best, p) => !best || Math.hypot(p.x - anchor.x, p.y - anchor.y) < Math.hypot(best.x - anchor.x, best.y - anchor.y) ? p : best, undefined);
  let away = nearest ? { x: anchor.x - nearest.x, y: anchor.y - nearest.y } : { x: 1, y: 0 };
  if (Math.hypot(away.x, away.y) < 1) {
    away = { x: anchor.x - contour.reduce((v, p) => v + p.x, 0) / (contour.length || 1), y: anchor.y - contour.reduce((v, p) => v + p.y, 0) / (contour.length || 1) };
  }
  if (points.length > 1) {
    // Use the local segment's normal, including on sampled horn walls.
    let normal = { x: 1, y: 0 }, distance = Infinity;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i], dx = b.x - a.x, dy = b.y - a.y;
      const p = visibleAnchor([a, b], viewport, anchor);
      if (!p) continue;
      const d = Math.hypot(anchor.x - p.x, anchor.y - p.y);
      if (d < distance && Math.hypot(dx, dy) > 0) { distance = d; normal = { x: -dy, y: dx }; }
    }
    const sign = normal.x * away.x + normal.y * away.y < 0 ? -1 : 1;
    away = { x: normal.x * sign, y: normal.y * sign };
  }
  const width = label.length * 6.4 + 4, height = 14;
  const horizontal = Math.abs(away.x) >= Math.abs(away.y);
  // Hover may use another side when the preferred side has no room.
  // Every candidate stays six pixels from the same geometry anchor.
  const preferred = { side: horizontal, positive: (horizontal ? away.x : away.y) >= 0 };
  const directions = hovered ? [preferred, { ...preferred, positive: !preferred.positive },
    { side: !horizontal, positive: true }, { side: !horizontal, positive: false }]
    : (points.length === 1 ? [horizontal, !horizontal] : [horizontal]).map(side => ({ side, positive: (side ? away.x : away.y) >= 0 }));
  let fallback, leastOverflow = Infinity;
  for (const { side, positive } of directions) {
    const at = { x: anchor.x + (side ? positive ? 6 : -6 : 0), y: anchor.y + (side ? 0 : positive ? 6 : -6) };
    const alignments = side ? [positive ? 'start' : 'end'] as const : ['middle', 'start', 'end'] as const;
    const baseline = side ? 'central' : positive ? 'text-before-edge' : 'text-after-edge';
    for (const textAnchor of alignments) {
      const box: LabelBox = { x: at.x - (textAnchor === 'middle' ? width / 2 : textAnchor === 'end' ? width : 0), y: at.y - (side ? height / 2 : positive ? 0 : height), width, height };
      const overflow = Math.max(0, -box.x) + Math.max(0, -box.y)
        + Math.max(0, box.x + width - viewport.width) + Math.max(0, box.y + height - viewport.height);
      if (overflow < leastOverflow) { leastOverflow = overflow; fallback = { at, textAnchor, baseline, box } as const; }
      if (box.x >= 4 && box.y >= 4 && box.x + box.width <= viewport.width - 4 && box.y + box.height <= viewport.height - 4) return { at, textAnchor, baseline, box } as const;
    }
  }
  // Hover always exposes the name, even if the viewport cannot fit the full text.
  return hovered ? fallback : undefined;
}
