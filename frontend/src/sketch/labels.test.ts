import { afterEach, describe, expect, it, vi } from 'vitest';
import { applyCommand } from './ops';
import type { Command } from './commands';
import type { Sketch } from './model';
import { displayName } from './displayName';
import { dimensionLabel, dimensionName } from './dimensionGraphics';
import { applySourceCommand, lower } from './adapters/sourceContour';
import { seedPreset, presetDefaults } from './adapters/sourcePreset';
import { expandedPreset } from './adapters/presetTestSupport';
import { presetDimensionActive, presetDimensionProblem } from './adapters/sourcePresetMapping';
import { SketchController } from './controller';
import { sourceEditorApi } from '../api/sourceEditor';
import { readSketchView, saveSketchView, sketchViewKey, defaultSketchView } from './viewPreferences';

const cone = () => seedPreset(expandedPreset('cone'), 'cone', presetDefaults.cone);
function run(sketch: Sketch, command: Command) {
  const result = applyCommand(sketch, command);
  if ('error' in result) throw new Error(result.error);
  return result.sketch;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.removeItem(sketchViewKey); });

describe('sketch names', () => {
  it('formats values, reference parentheses, radius symbols and degrees independently of names', () => {
    const c = { kind: 'radius' as const, arc: 'arc', id: 'r', value: 5, driving: true, label: 'Cap' };
    expect(dimensionLabel(c, 'value')).toBe('R 5.00 mm');
    expect(dimensionLabel(c, 'name')).toBe('Cap R 5.00 mm');
    expect(dimensionLabel({ ...c, driving: false }, 'value')).toBe('(R 5.00 mm)');
    expect(dimensionLabel({ kind: 'angle', id: 'angle', a: 'a', b: 'b', value: Math.PI / 2, driving: false, label: 'Opening' }, 'value')).toBe('(90.00°)');
    expect(dimensionName({ ...c, label: '' })).toBe('Radius');
  });
  it('trims and clears dimension names, preserves the constraint, and validates its command', () => {
    const { sketch, meta } = cone(), id = meta.preset!.dimensions.depth_mm;
    const renamed = run(sketch, { kind: 'setConstraintLabel', id, label: '  Cone depth adjusted  ' });
    const c = sketch.constraints.find(c => c.id === id)!;
    expect(renamed.constraints.find(c => c.id === id)).toEqual({ ...c, label: 'Cone depth adjusted' });
    const cleared = run(renamed, { kind: 'setConstraintLabel', id, label: '  ' }).constraints.find(c => c.id === id)!;
    expect(cleared.label).toBeUndefined(); expect(dimensionName(cleared)).toBe('Vertical distance');
    expect(run(sketch, { kind: 'setConstraintLabel', id, label: 'a'.repeat(40) }).constraints.find(c => c.id === id)!.label).toHaveLength(40);
    for (const label of ['a'.repeat(41), null, 123]) expect(applyCommand(sketch, { kind: 'setConstraintLabel', id, label } as Command)).toHaveProperty('error');
    expect(applyCommand(sketch, { kind: 'setConstraintLabel', id: 'missing', label: 'Name' })).toHaveProperty('error');
    expect(applyCommand(sketch, { kind: 'setConstraintLabel', id: sketch.constraints.find(c => !('value' in c))!.id, label: 'Name' })).toHaveProperty('error');
    expect(sketch.constraints.find(v => v.id === id)).toBe(c);
  });
  it('prefers user names to preset and generic names; role and name edits preserve each other in both orders', () => {
    const { sketch } = cone();
    let renamed = run(sketch, { kind: 'setEntityName', id: 'dust-cap', name: '  Phase cap  ' });
    expect(displayName(renamed, 'dust-cap')).toBe('Phase cap');
    renamed = run(renamed, { kind: 'setEntityData', id: 'dust-cap', data: { role: 'rigid' } });
    expect(renamed.entities.find(e => e.id === 'dust-cap')!.data).toEqual({ name: 'Phase cap', role: 'rigid' });
    renamed = run(renamed, { kind: 'setEntityName', id: 'dust-cap', name: 'New cap' });
    expect(renamed.entities.find(e => e.id === 'dust-cap')!.data).toEqual({ name: 'New cap', role: 'rigid' });
    renamed = run(renamed, { kind: 'setEntityName', id: 'dust-cap', name: '  ' });
    expect(displayName(renamed, 'dust-cap')).toBe('Dust cap');
    expect(renamed.entities.find(e => e.id === 'dust-cap')!.data).toEqual({ role: 'rigid' });
    const generic = { ...sketch, entities: sketch.entities.map(e => e.id === 'dust-cap' ? { ...e, id: 'unknown' } : e) };
    expect(displayName(generic, 'unknown')).toBe('Arc 1');
    expect(displayName(run(generic, { kind: 'setEntityName', id: 'unknown', name: 'Custom arc' }), 'unknown')).toBe('Custom arc');
    expect(run(sketch, { kind: 'setEntityName', id: 'dust-cap', name: 'a'.repeat(60) }).entities.find(e => e.id === 'dust-cap')!.data).toHaveProperty('name', 'a'.repeat(60));
    for (const name of ['a'.repeat(61), null, 123]) expect(applyCommand(sketch, { kind: 'setEntityName', id: 'dust-cap', name } as Command)).toHaveProperty('error');
    expect(applyCommand(sketch, { kind: 'setEntityName', id: 'missing', name: 'Name' })).toHaveProperty('error');
    expect(applyCommand(sketch, { kind: 'setEntityName', id: 'axis', name: 'Name' })).toHaveProperty('error');
  });
  it('keeps lowering, IDs, literal weights, and actual /validate request bytes identical through undo/redo', async () => {
    const initial = cone(); initial.meta.weights['dust-cap'] = 0; initial.meta.weights.cone = -.5;
    const controller = new SketchController(initial, initial.meta.references, (s, c) => {
      const result = applySourceCommand(s.sketch, s.meta, c);
      return 'error' in result ? result : { sketch: result.sketch, meta: result.meta };
    }, () => {});
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
      expect(url).toBe('/api/source-editor/validate'); requests.push(init.body);
      return new Response(JSON.stringify({ document: JSON.parse(init.body), geometry_sha256: 'same-geometry' }), { status: 200 });
    }));
    try {
      const before = lower(initial.sketch, initial.meta); if (!before.ok) throw new Error('Invalid fixture');
      const validated = await sourceEditorApi.validate(before.document), id = initial.meta.preset!.dimensions.depth_mm;
      await controller.command({ kind: 'setEntityName', id: 'dust-cap', name: 'Phase cap' });
      await controller.command({ kind: 'setConstraintLabel', id, label: 'Custom cone depth' });
      const renamed = controller.current;
      const after = lower(renamed.sketch, renamed.meta); expect(after).toEqual(before);
      expect(renamed.sketch.points).toEqual(initial.sketch.points);
      expect(renamed.sketch.entities.map(e => e.id)).toEqual(initial.sketch.entities.map(e => e.id));
      expect(renamed.sketch.constraints.map(c => c.id)).toEqual(initial.sketch.constraints.map(c => c.id));
      expect(renamed.meta.weights).toEqual(initial.meta.weights);
      expect(renamed.meta.preset!.dimensions).toEqual(initial.meta.preset!.dimensions);
      expect(presetDimensionActive(renamed.sketch, renamed.meta, 'depth_mm')).toBe(true);
      expect(presetDimensionProblem(renamed.sketch, renamed.meta, id, 5)).toBeUndefined();
      if (!after.ok) throw new Error('Rename failed');
      expect((await sourceEditorApi.validate(after.document)).geometry_sha256).toBe(validated.geometry_sha256);
      expect(requests).toHaveLength(2); expect(requests[1]).toBe(requests[0]);
      await controller.undo(); expect(controller.current.sketch.constraints.find(c => c.id === id)!.label).toBe('Cone depth');
      expect(displayName(controller.current.sketch, 'dust-cap')).toBe('Phase cap');
      await controller.undo(); expect(controller.current).toBe(initial);
      await controller.undo(true); await controller.undo(true); expect(controller.current).toBe(renamed);
      await controller.dimension(id, 5);
      const c = controller.current.sketch.constraints.find(c => c.id === id)!;
      expect(c).toMatchObject({ value: 5, label: 'Custom cone depth', driving: true });
      expect(controller.current.meta.preset!.dimensions.depth_mm).toBe(id);
    } finally { controller.destroy(); }
  });
  it('falls back from malformed preferences and blocked storage without breaking the viewer', () => {
    localStorage.setItem(sketchViewKey, '{'); expect(readSketchView()).toEqual(defaultSketchView);
    localStorage.setItem(sketchViewKey, JSON.stringify({ names: true, dimensions: 'bad' }));
    expect(readSketchView()).toEqual({ ...defaultSketchView, names: true });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(readSketchView()).toEqual(defaultSketchView);
    expect(() => saveSketchView({ ...defaultSketchView, names: true })).not.toThrow();
  });
});
