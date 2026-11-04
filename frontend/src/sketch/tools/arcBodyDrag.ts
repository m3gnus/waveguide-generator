import { arcBendPlan } from '../bend';
import { arcGeometry, distance } from '../geometry';
import type { Sketch } from '../model';
import type { XY } from '../solver/types';

export const arcBodyHint = 'Drag to bend · Alt: radius · Shift: move';

/** Only a lone authored arc bends; group and point gestures keep their usual drag. */
export function bendableArc(sketch: Sketch, id: string | undefined, selection: string[]) {
  return selection.length <= 1 && sketch.entities.some(e => e.id === id && e.kind === 'arc' && !e.reference) ? id : undefined;
}

/** Gesture-local anchors only. The existing session owns geometry, solving and history. */
export class ArcBodyDrag {
  clamped = false;
  private offset: XY = { x: 0, y: 0 };
  constructor(private sketch: Sketch, readonly id: string, private mode: 'bend' | 'radius') {}

  targets(current: Sketch, cursor: XY, alt: boolean) {
    const mode = alt ? 'radius' : 'bend';
    if (mode !== this.mode) {
      this.sketch = current;
      this.mode = mode;
      const arc = current.entities.find(e => e.id === this.id)!;
      if (arc.kind !== 'arc') throw new Error('Bending needs an arc. Select an arc body.');
      const g = arcGeometry(current, arc), length = distance(g.center, cursor);
      // An interior anchor avoids the minimum-sagitta clamp at a projected endpoint.
      const angle = g.start + g.sweep / 2;
      const anchor = mode === 'bend' ? { x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) }
        : length > 1e-9 ? { x: g.center.x + (cursor.x - g.center.x) * g.radius / length, y: g.center.y + (cursor.y - g.center.y) * g.radius / length }
        : current.points.find(p => p.id === arc.start)!;
      // Rebase at the solved shape, even when constraints held it short of the pointer.
      this.offset = { x: anchor.x - cursor.x, y: anchor.y - cursor.y };
    }
    const plan = arcBendPlan(this.sketch, this.id, { x: cursor.x + this.offset.x, y: cursor.y + this.offset.y }, mode);
    this.clamped = plan.clamped;
    return plan.targets;
  }
}
