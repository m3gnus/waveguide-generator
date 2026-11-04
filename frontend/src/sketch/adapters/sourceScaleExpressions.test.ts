import { beforeAll, describe, expect, it } from 'vitest';
import { applyCommand } from '../ops';
import { scaleSourceToLimit, fitSourcePreset, lower, sourceScaleInputs } from './sourceContour';
import { seedPreset, presetDefaults } from './sourcePreset';
import { expandedPreset } from './presetTestSupport';
import { sourceScaleLegacy, legacyScaledValues, legacyScaledContour } from './fixtures/sourceScaleLegacy';
import { SketchController } from '../controller';
import { realSessionReady } from '../testSupport';
import { serializeSourceAuthoring, parseSourceAuthoring } from './sourceAuthoring';

beforeAll(realSessionReady);
const limit = { kind: 'throat', radius: 12.7, hard: true } as const;
const value = (state: typeof sourceScaleLegacy, id: string) => {
  const c = state.sketch.constraints.find(c => c.id === id)!;
  if (!('value' in c)) throw new Error('Expected dimension');
  return c.value;
};

describe('source scaling with expressions', () => {
  it('keeps the frozen old design values and contour byte-identical', () => {
    const before = structuredClone(sourceScaleLegacy), output = lower(before.sketch, before.meta);
    if (!output.ok) throw new Error('Expected legacy contour');
    const restored = parseSourceAuthoring(serializeSourceAuthoring(before, false), output.document)!;
    const scaled = scaleSourceToLimit(restored, limit);
    expect(JSON.stringify(scaled.sketch.constraints.flatMap(c => 'value' in c ? [c.value] : []))).toBe(legacyScaledValues);
    const lowered = lower(scaled.sketch, scaled.meta);
    if (!lowered.ok) throw new Error('Expected scaled contour');
    expect(JSON.stringify(lowered.document.contour.points)).toBe(legacyScaledContour);
    expect(sourceScaleLegacy).toEqual(before);
  });

  it('scales inputs, resolves a chain with offsets, preserves expressions and solves geometry in one undo step', async () => {
    const before = structuredClone(sourceScaleLegacy);
    const expression = '[Cap width] + 1 mm';
    const applied = applyCommand(before.sketch, { kind: 'setDimensionExpression', id: 'depth', expression });
    if ('error' in applied) throw new Error(applied.error);
    before.sketch = applied.sketch;
    const controller = new SketchController({ initial: before, references: state => state.meta.references,
      apply: (state, command) => {
        const next = applyCommand(state.sketch, command);
        return 'error' in next ? next : { ...state, sketch: next.sketch };
      }, changed: () => {} });
    try {
      await controller.ready;
      await controller.adopt(current => scaleSourceToLimit(current, limit), 'Scale to fit throat', true, true);
      const scaled = controller.current;
      expect(value(scaled, 'cap-width')).toBe(4.762499999999999);
      expect(value(scaled, 'depth')).toBe(5.762499999999999);
      expect(scaled.sketch.constraints.find(c => c.id === 'depth')!.expression).toBe(expression);
      expect(scaled.sketch.points.find(p => p.id === 'elbow')!.y).toBeCloseTo(-5.7625, 9);
      await controller.undo();
      expect(controller.current).toEqual(before);
      await controller.undo(true);
      expect(controller.current).toEqual(scaled);
    } finally { controller.destroy(); }
  });

  it('resolves chained diameter and angle expressions from scaled driving inputs', () => {
    const before = structuredClone(sourceScaleLegacy);
    before.sketch.constraints.push({ kind: 'angle', id: 'angle', a: 'cap', b: 'cone', value: 1, driving: true, expression: 'Depth * 10' });
    const cap = before.sketch.constraints.find(c => c.id === 'cap-width')!;
    cap.diameter = true;
    const depth = before.sketch.constraints.find(c => c.id === 'depth')!;
    depth.expression = '[Cap width] / 2 + 1';
    const scaled = scaleSourceToLimit(before, { ...limit, radius: 16 });
    expect(value(scaled, 'cap-width')).toBe(6);
    expect(value(scaled, 'depth')).toBe(7);
    expect(value(scaled, 'angle')).toBe(70 * Math.PI / 180);
    expect(scaled.sketch.constraints.find(c => c.id === 'angle')!.expression).toBe('Depth * 10');
  });

  it.each(['1 / ([Cap width] - 6)', 'missing + 1', 'Depth + 1'])('refuses an invalid expression without mutating state: %s', expression => {
    const before = structuredClone(sourceScaleLegacy);
    before.sketch.constraints.find(c => c.id === 'depth')!.expression = expression;
    const snapshot = structuredClone(before);
    expect(() => scaleSourceToLimit(before, { ...limit, radius: 16 })).toThrow(/Expression|dimension|cycle/);
    expect(before).toEqual(snapshot);
  });

  it('measures reference dimensions from the scaled geometry instead of scaling their stale stored values', () => {
    const before = structuredClone(sourceScaleLegacy);
    before.sketch.constraints.push({ kind: 'distanceX', id: 'readout', a: 'pole', b: 'elbow', value: 99, driving: false });
    expect(value(scaleSourceToLimit(before, { ...limit, radius: 16 }), 'readout')).toBe(6);
  });

  it('reports a released preset radius expression and refuses to release an input still used by another formula', () => {
    const before = seedPreset(expandedPreset('cone'), 'cone', presetDefaults.cone);
    const radius = before.meta.preset!.dimensions.radius_mm;
    before.sketch.constraints.find(c => c.id === radius)!.expression = '5 * 2';
    const fitted = fitSourcePreset(before, limit);
    expect(fitted.notice).toContain('Cleared the Radius expression (5 * 2)');
    expect(fitted.sketch.constraints.find(c => c.id === radius)).toMatchObject({ driving: false });
    expect(fitted.sketch.constraints.find(c => c.id === radius)!.expression).toBeUndefined();
    expect(before.sketch.constraints.find(c => c.id === radius)!.expression).toBe('5 * 2');
    before.sketch.constraints.find(c => c.id === before.meta.preset!.dimensions.depth_mm)!.expression = 'Radius / 2';
    expect(() => fitSourcePreset(before, limit)).toThrow('Clear their expressions before making it a reference dimension.');
  });
});

describe('fit contour to throat modes', () => {
  const withFormula = () => {
    const before = structuredClone(sourceScaleLegacy);
    const applied = applyCommand(before.sketch, { kind: 'setDimensionExpression', id: 'depth', expression: '[Cap width] + 1 mm' });
    if ('error' in applied) throw new Error(applied.error);
    const params = [
      applyCommand(applied.sketch, { kind: 'addParameter', name: 'gap', expression: '2', unit: 'mm' }),
    ][0];
    if ('error' in params) throw new Error(params.error);
    let sketch = params.sketch;
    for (const command of [
      { kind: 'addParameter', name: 'twice', expression: 'gap * 2', unit: 'mm' },
      { kind: 'addParameter', name: 'tilt', expression: '5', unit: 'deg' },
      { kind: 'addParameter', name: 'ratio', expression: '0.5', unit: 'none' },
      { kind: 'addParameter', name: 'offset', expression: '-2', unit: 'mm' },
      { kind: 'addParameter', name: 'turn', expression: 'pi', unit: 'mm' },
    ] as const) { const next = applyCommand(sketch, command); if ('error' in next) throw new Error(next.error); sketch = next.sketch; }
    return { ...before, sketch };
  };
  it('scales an old design without formulas byte-identically in both modes', () => {
    for (const mode of ['exact', 'inputs'] as const) {
      const scaled = scaleSourceToLimit(structuredClone(sourceScaleLegacy), limit, mode);
      expect(JSON.stringify(scaled.sketch.constraints.flatMap(c => 'value' in c ? [c.value] : []))).toBe(legacyScaledValues);
      expect(JSON.stringify(scaled.sketch)).toBe(JSON.stringify(scaleSourceToLimit(structuredClone(sourceScaleLegacy), limit).sketch));
    }
  });
  it('makes an exact similar copy: formulas become scaled values and parameters stay unchanged', () => {
    const before = withFormula(), { scale } = sourceScaleInputs(before, limit);
    const scaled = scaleSourceToLimit(before, limit, 'exact');
    const depth = scaled.sketch.constraints.find(c => c.id === 'depth')!;
    expect(depth.expression).toBeUndefined();
    expect('value' in depth && depth.value).toBeCloseTo((value(before, 'cap-width') + 1) * scale, 9);
    expect(scaled.sketch.parameters).toEqual(before.sketch.parameters);
  });
  it('resizes plain length inputs and sketch-local mm parameters, keeping formulas', () => {
    const before = withFormula(), inputs = sourceScaleInputs(before, limit);
    const scaled = scaleSourceToLimit(before, limit, 'inputs');
    expect(scaled.sketch.constraints.find(c => c.id === 'depth')!.expression).toBe('[Cap width] + 1 mm');
    const byName = Object.fromEntries((scaled.sketch.parameters ?? []).map(p => [p.name, p.expression]));
    expect(Number(byName.gap)).toBeCloseTo(2 * inputs.scale, 9);
    expect(Number(byName.offset)).toBeCloseTo(-2 * inputs.scale, 9);
    expect(byName).toMatchObject({ twice: 'gap * 2', tilt: '5', ratio: '0.5', turn: 'pi' });
    expect(inputs.parameters.map(p => p.name)).toEqual(['gap', 'offset']);
    expect(inputs.dimensions.some(d => d.name.length > 0)).toBe(true);
    expect(inputs.dimensions.every(d => Math.abs(d.to - d.from * inputs.scale) < 1e-9)).toBe(true);
  });
});
