import { describe, expect, it } from 'vitest';
import { seed, withSourceLimit, rules, lower, applySourceCommand, type SourceLimit } from './sourceContour';
import { fitPresetDimensions, presetDefaults, seedPreset } from './sourcePreset';
import { expandedPreset } from './presetTestSupport';
import { createSession } from '../session';
import { findSnap, snapLimitTarget } from '../snap';

describe('source context limits', () => {
  it('installs a fixed aperture line, warns past it, and keeps lowering soft violations', () => {
    const state = withSourceLimit(seed(expandedPreset('flat', { radius_mm: 15 })), { kind: 'aperture', radius: 14 });
    expect(state.meta.references.at(-1)).toMatchObject({ kind: 'limit', radius: 14, label: 'Aperture r 14.00', locked: false });
    expect(rules(state.sketch, state.meta)).toEqual([expect.objectContaining({ code: 'rim-limit', severity: 'warning', message: 'Rim exceeds the baffle aperture' })]);
    expect(lower(state.sketch, state.meta).ok).toBe(true);
    const moved = applySourceCommand(state.sketch, state.meta, { kind: 'movePoint', id: 'rim', x: 14, y: 0 });
    if ('error' in moved) throw new Error(moved.error);
    expect(rules(moved.sketch, moved.meta)).toEqual([]);
  });
  it.each([
    ['aperture', 14, 13.95, 'Aperture gap'], ['mouth', 20, 20, 'Horn mouth must'], ['mouth', 20, 21, 'Rim exceeds the horn mouth'],
  ] as const)('mirrors %s collar or mouth clearances', (kind, radius, rim, message) => {
    const state = withSourceLimit(seed(expandedPreset('flat', { radius_mm: rim })), { kind, radius });
    expect(rules(state.sketch, state.meta)).toEqual([expect.objectContaining({ code: 'rim-limit', severity: 'warning', message: expect.stringContaining(message) })]);
  });
  it('snaps a woofer rim within eight pixels and allows crossing further away', () => {
    const state = withSourceLimit(seed(expandedPreset('flat')), { kind: 'aperture', radius: 14 });
    expect(snapLimitTarget(state.meta.references, 'rim', { x: 14.07, y: 0 }, .08)).toEqual({ x: 14, y: 0 });
    expect(snapLimitTarget(state.meta.references, 'rim', { x: 15, y: 0 }, .08)).toEqual({ x: 15, y: 0 });
    expect(snapLimitTarget(state.meta.references, 'pole', { x: 14.07, y: 0 }, .08)).toEqual({ x: 14.07, y: 0 });
    const snap = findSnap(state.sketch, state.meta.references, { x: 14.05, y: 2 }, .01);
    expect(snap).toMatchObject({ glyph: 'limit', x: 14, y: 2 });
    const changed = withSourceLimit(state, { kind: 'aperture', radius: 16 });
    expect(findSnap(changed.sketch, changed.meta.references, { x: 16.05, y: 2 }, .01, snap)).toMatchObject({ glyph: 'limit', x: 16 });
  });
  it.each(['flat', 'dome', 'cone'] as const)('locks the %s rim through real solver drags and lowers bit-exact throat radii', async shape => {
    const throat = 12.7;
    const dimensions = fitPresetDimensions(presetDefaults[shape], throat);
    const state = withSourceLimit(seedPreset(expandedPreset(shape, dimensions), shape, dimensions), { kind: 'throat', radius: throat, hard: true });
    const session = await createSession(state.sketch, { references: state.meta.references });
    try {
      expect((await session.solveFull()).status).toBe('ok');
      expect(rules(session.sketch, state.meta)).toEqual([]);
      await session.beginDrag(['rim']);
      await session.dragTo({ pointId: 'rim', x: 25, y: 2 });
      expect((await session.endDrag()).status).toBe('ok');
      const output = lower(session.sketch, state.meta);
      if (!output.ok) throw new Error(JSON.stringify(output.problems));
      expect(output.document.contour.points.at(-1)!.r_mm).toBe(throat);
      const lock = state.sketch.constraints.find(c => c.kind === 'pointOnLine' && state.meta.references.some(r => r.kind === 'limit' && c.line === r.id))!;
      expect(applySourceCommand(state.sketch, state.meta, { kind: 'removeConstraint', id: lock.id })).toEqual({ error: 'Set by the horn design' });
    } finally { session.destroy(); }
  });
  it.each(['flat', 'dome', 'cone'] as const)('fits the %s rim, including surround and land, to the limit', shape => {
    const dimensions = fitPresetDimensions(presetDefaults[shape], 14);
    const state = seedPreset(expandedPreset(shape, dimensions), shape, dimensions);
    const output = lower(state.sketch, state.meta);
    if (!output.ok) throw new Error('Expected contour');
    expect(output.document.contour.points.at(-1)!.r_mm).toBe(14);
    expect(dimensions.radius_mm).toBe(shape === 'flat' ? 14 : 11);
  });
  it('rejects an unusably small preset limit and preserves the original state on context changes', () => {
    expect(() => fitPresetDimensions(presetDefaults.cone, 5)).toThrow('too small');
    const original = seed(expandedPreset('cone')), snapshot = structuredClone(original);
    const limit: SourceLimit = { kind: 'throat', radius: 12.7, hard: true };
    const attached = withSourceLimit(original, limit);
    expect(original).toEqual(snapshot);
    expect(lower(attached.sketch, attached.meta)).toMatchObject({ ok: true });
    expect(rules(attached.sketch, attached.meta)).toEqual([]);
    expect(withSourceLimit(attached, { kind: 'aperture', radius: 14 }).meta.references).toHaveLength(3);
  });
});
