import type { LabelBox } from '../dimensionGraphics';
import type { XY } from '../solver/types';

/** Screen-space label bounds are shared by point picking and the marquee. */
export function selectLabels(labels: { id: string; box: LabelBox }[], start: XY, end = start, crossing = true): string[] {
  const left = Math.min(start.x, end.x), right = Math.max(start.x, end.x);
  const top = Math.min(start.y, end.y), bottom = Math.max(start.y, end.y);
  return labels.filter(({ box: b }) => crossing
    ? b.x <= right && b.x + b.width >= left && b.y <= bottom && b.y + b.height >= top
    : b.x >= left && b.x + b.width <= right && b.y >= top && b.y + b.height <= bottom).map(label => label.id);
}
