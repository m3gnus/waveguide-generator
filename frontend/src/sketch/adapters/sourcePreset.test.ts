import { describe, expect, it } from 'vitest';
import { presetDefaults, presetLabels, seedPreset, type PresetShape } from './sourcePreset';
import { expandedPreset } from './presetTestSupport';
import { applySourceCommand, lower, rules } from './sourceContour';
import { createSession } from '../session';
import { applyCommand } from '../ops';
import { parseSketch, toJSON } from '../model';
import { arcGeometry } from '../geometry';
import type { Command } from '../commands';

function expectGeometry(actual: ReturnType<typeof lower>, expected: ReturnType<typeof expandedPreset>) {
  expect(actual.ok).toBe(true); if (!actual.ok) throw new Error('Lower failed');
  for (const [i, p] of expected.contour.points.entries()) {
    expect(actual.document.contour.points[i].r_mm).toBeCloseTo(p.r_mm, 6);
    expect(actual.document.contour.points[i].z_mm).toBeCloseTo(p.z_mm, 6);
  }
  for (const [i, e] of expected.contour.segments.entries()) {
    const got = actual.document.contour.segments[i];
    expect({ ...got, center_mm: null }).toEqual({ ...e, center_mm: null });
    if (e.center_mm) e.center_mm.forEach((value, axis) => expect(got.center_mm![axis]).toBeCloseTo(value, 6));
  }
  expect(actual.document.drive).toEqual(expected.drive);
}
describe('named parametric presets', () => {
  it.each(['convertEntity', 'deleteEntity', 'splitAt'] as const)('%s invalidates surround mappings and reports removed crown constraints', kind => {
    const before = seedPreset(expandedPreset('cone'), 'cone', presetDefaults.cone);
    const surround = before.sketch.entities.find(e => e.id === 'surround')!;
    if (surround.kind !== 'arc') throw new Error('Expected surround arc');
    const g = arcGeometry(before.sketch, surround), angle = g.start + g.sweep / 2;
    const command: Command = kind === 'splitAt' ? { kind, entity: surround.id, x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) }
      : kind === 'convertEntity' ? { kind, id: surround.id, to: 'line' } : { kind, id: surround.id };
    const depth = before.sketch.constraints.find(c => c.id === before.meta.preset!.dimensions.surround_depth_mm)!;
    if (depth.kind !== 'distanceY') throw new Error('Expected depth dimension');
    const crownConstraints = before.sketch.constraints.filter(c => 'a' in c && (c.a === depth.b || 'b' in c && c.b === depth.b));
    const result = applySourceCommand(before.sketch, before.meta, command);
    if ('error' in result) throw new Error(result.error);
    expect(result.meta.preset!.dimensions.surround_depth_mm).toBeUndefined();
    expect(result.meta.preset!.dimensions.surround_width_mm).toBeUndefined();
    expect(result.meta.preset!.dimensions.depth_mm).toBe(before.meta.preset!.dimensions.depth_mm);
    expect(result.sketch.entities.some(e => e.id.startsWith('preset-surround-crown'))).toBe(false);
    expect(result.sketch.points.some(p => p.id === depth.b)).toBe(false);
    for (const c of crownConstraints) expect(result.removedConstraints).toContain(c.id);
    expect(result.removedConstraints).toContain(before.meta.preset!.dimensions.surround_width_mm);
    expect(before.meta.preset!.dimensions.surround_depth_mm).toBe(depth.id);
    expect(before.sketch.points.some(p => p.id === depth.b)).toBe(true);
  });
  it.each([
    ['flat', 'piston', ['radius_mm']],
    ['cone', 'dust-cap', ['cap_radius_mm', 'cap_height_mm']],
    ['cone', 'land', ['land_width_mm']],
  ] as const)('invalidates %s dimensions when %s is converted', (shape, id, parameters) => {
    const before = seedPreset(expandedPreset(shape), shape, presetDefaults[shape]);
    const entity = before.sketch.entities.find(e => e.id === id)!;
    const result = applySourceCommand(before.sketch, before.meta, { kind: 'convertEntity', id, to: entity.kind === 'arc' ? 'line' : 'arc' });
    if ('error' in result) throw new Error(result.error);
    for (const parameter of parameters) {
      expect(result.meta.preset!.dimensions[parameter]).toBeUndefined();
      expect(result.removedConstraints).toContain(before.meta.preset!.dimensions[parameter]);
    }
  });

  it('removes a deleted depth dimension and its crown without invalidating the surround width', () => {
    const before = seedPreset(expandedPreset('cone'), 'cone', presetDefaults.cone);
    const result = applySourceCommand(before.sketch, before.meta, { kind: 'removeConstraint', id: before.meta.preset!.dimensions.surround_depth_mm });
    if ('error' in result) throw new Error(result.error);
    expect(result.meta.preset!.dimensions.surround_depth_mm).toBeUndefined();
    expect(result.sketch.entities.some(e => e.id.startsWith('preset-surround-crown'))).toBe(false);
    const next = applySourceCommand(result.sketch, result.meta, { kind: 'setEntityData', id: 'surround', data: { role: 'moving' } });
    if ('error' in next) throw new Error(next.error);
    expect(next.meta.preset!.dimensions.surround_width_mm).toBe(before.meta.preset!.dimensions.surround_width_mm);
  });
  for (const shape of ['flat', 'dome', 'cone'] as PresetShape[]) {
    it.each<Record<string, number>>([{}, { radius_mm: 12, height_mm: 4, depth_mm: 5, cap_radius_mm: 4, cap_height_mm: 1.5, surround_width_mm: 4, surround_depth_mm: .8, land_width_mm: 2 },
      { radius_mm: 15.123, height_mm: 2.3, depth_mm: 3.1, cap_radius_mm: 2.4, cap_height_mm: 1.1, surround_width_mm: 3, surround_depth_mm: -.7, land_width_mm: .75 }])(`${shape} keeps the exact expanded document and names its parameters (%j)`, async overrides => {
      const dims = { ...presetDefaults[shape], ...overrides }, document = expandedPreset(shape, overrides), before = structuredClone(document);
      const { sketch, meta } = seedPreset(document, shape, dims);
      expect(lower(sketch, meta)).toEqual({ ok: true, document }); expect(document).toEqual(before);
      expect(sketch.constraints.filter(c => c.label).map(c => c.label).sort()).toEqual(Object.keys(presetDefaults[shape]).map(key => presetLabels[key]).sort());
      expect(parseSketch(toJSON(sketch))).toEqual({ ok: true, sketch });
      expect(rules(sketch, meta)).toEqual([]);
      expect(sketch.constraints.filter(c => c.kind === 'tangent')).toEqual([]);
      const session = await createSession(sketch, { references: meta.references });
      try { const diagnosis = await session.solveFull(); expect(diagnosis.status).toBe('ok'); expect(diagnosis.dof).toBe(0); expect(diagnosis.hasRedundancy).toBe(false); }
      finally { session.destroy(); }
    });
    it(`${shape} continues every named dimension to the matching builder geometry`, async () => {
      const targets = { radius_mm: 11, height_mm: 4, depth_mm: 5, cap_radius_mm: 3.5, cap_height_mm: 1.5, surround_width_mm: 3, surround_depth_mm: 1, land_width_mm: 1.5 };
      const initial = seedPreset(expandedPreset(shape), shape, presetDefaults[shape]);
      const session = await createSession(initial.sketch, { references: initial.meta.references });
      const changed: Record<string, number> = {};
      try {
        for (const [parameter, id] of Object.entries(initial.meta.preset!.dimensions)) {
          const value = targets[parameter as keyof typeof targets]; changed[parameter] = value;
          const result = await session.setDimensionContinuation(id, value);
          expect(result.ok, result.reason).toBe(true);
          expectGeometry(lower(result.sketch, initial.meta), expandedPreset(shape, changed));
          expect(result.sketch.constraints.find(c => c.id === id)?.label).toBe(presetLabels[parameter]);
        }
      } finally { session.destroy(); }
    });
  }
  it.each(['dome', 'cone'] as const)('%s changes surround depth directly from 0.6 to 1.0', async shape => {
    const initial = seedPreset(expandedPreset(shape, { surround_depth_mm: .6 }), shape, { ...presetDefaults[shape], surround_depth_mm: .6 });
    const session = await createSession(initial.sketch, { references: initial.meta.references });
    try {
      const result = await session.setDimensionContinuation(initial.meta.preset!.dimensions.surround_depth_mm, 1);
      expect(result.ok, result.reason).toBe(true);
      expectGeometry(lower(result.sketch, initial.meta), expandedPreset(shape, { surround_depth_mm: 1 }));
      const boundary = session.sketch;
      expect((await session.setDimensionContinuation(initial.meta.preset!.dimensions.surround_depth_mm, 1.1)).ok).toBe(false);
      expect(session.sketch).toEqual(boundary);
      const back = await session.setDimensionContinuation(initial.meta.preset!.dimensions.surround_depth_mm, .6);
      expect(back.ok, back.reason).toBe(true);
      expectGeometry(lower(back.sketch, initial.meta), expandedPreset(shape, { surround_depth_mm: .6 }));
    } finally { session.destroy(); }
  });
  it.each(['dome', 'cone'] as const)('%s preserves omitted and flat optional patches without changing topology', async shape => {
    const cases: Record<string, number>[] = [{ surround_width_mm: 0, surround_depth_mm: 0, land_width_mm: 0 }, { surround_depth_mm: 0 }];
    for (const overrides of cases) {
      const document = expandedPreset(shape, overrides), state = seedPreset(document, shape, { ...presetDefaults[shape], ...overrides });
      expect(lower(state.sketch, state.meta)).toEqual({ ok: true, document });
      const session = await createSession(state.sketch, { references: state.meta.references });
      try { const result = await session.solveFull(); expect(result.status).toBe('ok'); expect(result.dof).toBe(0); }
      finally { session.destroy(); }
    }
  });
  it('holds a fully dimensioned dome pole and restores freedom when its height is removed', async () => {
    const initial = seedPreset(expandedPreset('dome'), 'dome', presetDefaults.dome);
    const session = await createSession(initial.sketch, { references: initial.meta.references });
    try {
      await session.beginDrag(['pole']); const drag = await session.dragTo({ pointId: 'pole', x: 1, y: 4 }); await session.endDrag();
      expect(drag.stopped).toBe(true); expectGeometry(lower(session.sketch, initial.meta), expandedPreset('dome'));
      const removed = applyCommand(initial.sketch, { kind: 'removeConstraint', id: initial.meta.preset!.dimensions.height_mm });
      if ('error' in removed) throw new Error(removed.error);
      session.sketch = removed.sketch; expect((await session.solveFull()).dof).toBe(1);
      await session.beginDrag(['pole']); await session.dragTo({ pointId: 'pole', x: 0, y: 4 }); await session.endDrag();
      expect(session.sketch.points.find(p => p.id === 'pole')!.y).toBeCloseTo(4, 6);
      expectGeometry(lower(session.sketch, initial.meta), expandedPreset('dome', { height_mm: 4 }));
    } finally { session.destroy(); }
  });
});
