import { afterEach, describe, expect, it, vi } from 'vitest';
import { applyCommand, importedSketch } from './ops';
import { parseSketch, toJSON, type Constraint, type DimensionPlacement, type Sketch } from './model';
import { dimensionLabel, dimensionLabelBox, dimensionLayout, dimensionLayouts, dragDimensionPlacement, labelsOverlap } from './dimensionGraphics';
import { seedPreset, presetDefaults } from './adapters/sourcePreset';
import { expandedPreset } from './adapters/presetTestSupport';
import { applySourceCommand, lower } from './adapters/sourceContour';
import { SketchController } from './controller';
import { SketchSession, toSolverSketch } from './session';
import { sourceEditorApi } from '../api/sourceEditor';
import { worldToScreen } from './view';

const cone = () => seedPreset(expandedPreset('cone'), 'cone', presetDefaults.cone);
const bounds = { left: 100, right: 360, top: 168, bottom: 260 };
const view = { scale: 20, offsetX: 100, offsetY: 180 };
const screen = (s: Sketch) => (id: string) => worldToScreen(view, s.points.find(p => p.id === id)!);
function move(sketch: Sketch, id: string, placement?: DimensionPlacement) {
  const result = applyCommand(sketch, { kind: 'setDimensionPlacement', id, placement });
  if ('error' in result) throw new Error(result.error);
  return result.sketch;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('dimension placement', () => {
  it('round trips placement, preserves it through dimension/name/driving/geometry edits and validates malformed positions', () => {
    const { sketch, meta } = cone(), id = meta.preset!.dimensions.depth_mm, placement = { offset: -6, along: 1.4 };
    let next = move(sketch, id, placement);
    for (const command of [{ kind: 'setDimension', id, value: 5 }, { kind: 'setDriving', id, driving: false },
      { kind: 'setConstraintLabel', id, label: 'Depth' }, { kind: 'movePoint', id: 'cap.rim', x: 3, y: -5 }] as const) {
      const result = applyCommand(next, command); if ('error' in result) throw new Error(result.error); next = result.sketch;
      expect(next.constraints.find(c => c.id === id)!.placement).toEqual(placement);
    }
    expect(parseSketch(toJSON(next))).toEqual({ ok: true, sketch: next });
    expect(move(next, id).constraints.find(c => c.id === id)).not.toHaveProperty('placement');
    for (const placement of [null, {}, { offset: Infinity, along: .5 }, { offset: 6, along: 'a' }, { offset: 6, angle: 1 }]) {
      const malformed = { ...sketch, constraints: sketch.constraints.map(c => c.id === id ? { ...c, placement } : c) };
      expect(parseSketch(malformed).ok).toBe(false);
      expect(applyCommand(sketch, { kind: 'setDimensionPlacement', id, placement } as never)).toHaveProperty('error');
    }
    expect(applyCommand(sketch, { kind: 'setDimensionPlacement', id: 'missing', placement })).toHaveProperty('error');
    expect(applyCommand(sketch, { kind: 'setDimensionPlacement', id: sketch.constraints.find(c => !('value' in c))!.id, placement })).toHaveProperty('error');
  });
  it('maps vertical text drags to perpendicular offset and signed along, including outside the extension lines', () => {
    const { sketch, meta } = cone(), c = sketch.constraints.find(c => c.id === meta.preset!.dimensions.depth_mm)!;
    const initial = { offset: 6, along: .5 };
    expect(dragDimensionPlacement(sketch, c, initial, { x: 2, y: 0 })).toEqual({ offset: 8, along: .5 });
    expect(dragDimensionPlacement(sketch, c, initial, { x: 0, y: 4 })).toEqual({ offset: 6, along: 1.5 });
    const next = move(sketch, c.id, { offset: 6, along: 1.5 });
    const layout = dimensionLayout(next, next.constraints.find(v => v.id === c.id)!, screen(next), bounds, 0, view.scale)!;
    const b = screen(next)('cone.rim');
    expect(layout.at.y).toBeCloseTo(b.y - 40);
    expect(layout.path).toContain(`L${layout.at.x + 10},${layout.at.y}`);
  });
  it('positions horizontal/general distances and radius labels in world units through zoom and pan', () => {
    const { sketch, meta } = cone();
    const horizontal = sketch.constraints.find(c => c.id === meta.preset!.dimensions.radius_mm)!;
    expect(dragDimensionPlacement(sketch, horizontal, { offset: -6, along: .5 }, { x: 10, y: 2 })).toEqual({ offset: -4, along: 1.5 });
    const general = { ...horizontal, kind: 'distance' } as Constraint;
    const initial = { offset: 4, along: -.5 };
    expect(dragDimensionPlacement(sketch, general, initial, { x: 0, y: 0 })).toEqual(initial);
    const radius: Constraint = { kind: 'radius', id: 'radius', arc: 'dust-cap', value: 3.25, driving: false, placement: { angle: Math.PI / 2, offset: 2 } };
    const layout = dimensionLayout(sketch, radius, screen(sketch), bounds, 0, view.scale)!;
    const nextView = { scale: 40, offsetX: 70, offsetY: 100 };
    const zoomed = dimensionLayout(sketch, radius, id => worldToScreen(nextView, sketch.points.find(p => p.id === id)!), bounds, 0, nextView.scale)!;
    expect((layout.at.x - view.offsetX) / view.scale).toBeCloseTo((zoomed.at.x - nextView.offsetX) / nextView.scale);
    expect((view.offsetY - layout.at.y) / view.scale).toBeCloseTo((nextView.offsetY - zoomed.at.y) / nextView.scale);
    const dragged = dragDimensionPlacement(sketch, radius, radius.placement!, { x: 2, y: 0 });
    expect(dragged).toHaveProperty('angle'); expect(dragged.offset).toBeGreaterThan(2);
    const parsed = parseSketch(toJSON({ ...sketch, constraints: [...sketch.constraints, radius] }));
    expect(parsed.ok).toBe(true);
  });
  it('also drags point-line distances and angular dimensions without touching their values', () => {
    const sketch = importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }, { id: 'c', x: 0, y: 10 }, { id: 'p', x: 5, y: 4 }],
      [{ kind: 'line', id: 'x', p1: 'a', p2: 'b' }, { kind: 'line', id: 'y', p1: 'a', p2: 'c' }]);
    const distance: Constraint = { kind: 'pointLineDistance', id: 'distance', point: 'p', line: 'x', value: 4, driving: false };
    const angle: Constraint = { kind: 'angle', id: 'angle', a: 'x', b: 'y', value: Math.PI / 2, driving: false };
    sketch.constraints.push(distance, angle);
    const initial = { offset: 2, along: .5 };
    expect(dragDimensionPlacement(sketch, distance, initial, { x: 3, y: -2 })).toEqual({ offset: 5, along: 1 });
    const angular = dimensionLayout(sketch, { ...angle, placement: { offset: 4, along: 0 } }, screen(sketch), bounds, 0, view.scale)!;
    const next = dragDimensionPlacement(sketch, angle, angular.placement, { x: -4, y: 6 });
    expect(next).toEqual({ offset: 6, along: 1 });
    const moved = move(sketch, angle.id, next);
    expect(moved.constraints.find(c => c.id === angle.id)).toMatchObject({ value: Math.PI / 2, placement: next });
    expect(dimensionLayout(moved, moved.constraints.find(c => c.id === angle.id)!, screen(moved), bounds, 0, view.scale)!.path).toContain(' A');
  });
  it('keeps manually positioned distance lines in world mm through zoom and pan', () => {
    const { sketch, meta } = cone(), id = meta.preset!.dimensions.depth_mm;
    const moved = move(sketch, id, { offset: 6, along: 1.2 }), c = moved.constraints.find(c => c.id === id)!;
    const layout = dimensionLayout(moved, c, screen(moved), bounds, 0, view.scale)!;
    const nextView = { scale: 40, offsetX: 70, offsetY: 100 };
    const zoomed = dimensionLayout(moved, c, id => worldToScreen(nextView, moved.points.find(p => p.id === id)!), bounds, 0, nextView.scale)!;
    // The text clearance stays in screen pixels; the dimension line itself follows world mm.
    expect((layout.at.x + 10 - view.offsetX) / view.scale).toBeCloseTo((zoomed.at.x + 10 - nextView.offsetX) / nextView.scale);
    expect((view.offsetY - layout.at.y) / view.scale).toBeCloseTo((nextView.offsetY - zoomed.at.y) / nextView.scale);
  });
  it.each(['value', 'name'] as const)('keeps cone %s label boxes separate and vertical text beside its own measured geometry', mode => {
    const { sketch } = cone(), xy = screen(sketch);
    const layouts = dimensionLayouts(sketch, xy, bounds, view.scale, c => dimensionLabel(c, mode));
    const boxes = sketch.constraints.flatMap(c => {
      if (!('value' in c)) return [];
      const layout = layouts.get(c.id)!;
      if (c.kind === 'distanceY') {
        const xs = [xy(c.a).x, xy(c.b).x];
        expect(layout.at.x).toBeGreaterThanOrEqual(Math.min(...xs) - 40);
        expect(layout.at.x).toBeLessThanOrEqual(Math.max(...xs) + 40);
        expect(layout.at.y).toBeCloseTo((xy(c.a).y + xy(c.b).y) / 2);
      }
      return [dimensionLabelBox(layout, dimensionLabel(c, mode))];
    });
    for (const [i, box] of boxes.entries()) for (const other of boxes.slice(i + 1)) expect(labelsOverlap(box, other)).toBe(false);
  });
  it('is one metadata-only undo step and keeps lowering and actual /validate request bytes unchanged', async () => {
    const initial = cone(), id = initial.meta.preset!.dimensions.depth_mm;
    const controller = new SketchController(initial, initial.meta.references, (s, c) => {
      const result = applySourceCommand(s.sketch, s.meta, c);
      return 'error' in result ? result : { sketch: result.sketch, meta: result.meta };
    }, () => {});
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => {
      requests.push(init.body);
      return new Response(JSON.stringify({ document: JSON.parse(init.body), geometry_sha256: 'same' }), { status: 200 });
    }));
    try {
      // Wait for opening diagnosis before instrumenting the placement command.
      await controller.command({ kind: 'setConstraintLabel', id, label: 'Cone depth' });
      const before = controller.current, solve = vi.spyOn(SketchSession.prototype, 'solveFull');
      const lowered = lower(before.sketch, before.meta); if (!lowered.ok) throw new Error('Invalid fixture');
      await sourceEditorApi.validate(lowered.document);
      await controller.command({ kind: 'setDimensionPlacement', id, placement: { offset: 6, along: 1.2 } });
      expect(solve).not.toHaveBeenCalled();
      const after = controller.current, loweredAfter = lower(after.sketch, after.meta);
      expect(loweredAfter).toEqual(lowered);
      expect(toSolverSketch(after.sketch, after.meta.references)).toEqual(toSolverSketch(before.sketch, before.meta.references));
      expect(after.sketch.points).toEqual(before.sketch.points);
      if (!loweredAfter.ok) throw new Error('Invalid placed fixture');
      await sourceEditorApi.validate(loweredAfter.document);
      expect(requests).toHaveLength(2); expect(requests[1]).toBe(requests[0]);
      await controller.undo(); expect(controller.current).toBe(before);
      await controller.undo(true); expect(controller.current).toBe(after);
    } finally { controller.destroy(); }
  });
});
