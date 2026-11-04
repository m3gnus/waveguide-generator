import { describe, expect, it } from 'vitest';
import { INITIAL_SOURCE } from '../../design/sourceContourEditing';
import { materialiseContext } from '../context';
import { hornAssemblyContext } from './sourceContext';
import { INITIAL_ASSEMBLY } from '../../design/sourceContextTestSupport';
import { lower, seed, withSourceContext } from './sourceContour';
import { newerEnvelope, parseSourceAuthoring, serializeSourceAuthoring, SOURCE_AUTHORING_VERSIONS } from './sourceAuthoring';

describe('shared source authoring envelope', () => {
  it.each(SOURCE_AUTHORING_VERSIONS)('reads %s with all authoring data and preserves document bytes', version => {
    const state = seed(INITIAL_SOURCE);
    state.sketch.entities.find(e => e.id === 'piston')!.data = { name: 'Diaphragm' };
    state.sketch.points.push({ id: 'guide-a', x: 1, y: 1 }, { id: 'guide-b', x: 2, y: 1 });
    state.sketch.entities.push({ id: 'guide', kind: 'line', p1: 'guide-a', p2: 'guide-b', construction: true });
    state.sketch.constraints.push({ id: 'width', kind: 'distance', a: 'pole', b: 'rim', value: 8, label: 'Width', placement: { offset: 3, along: 4 }, driving: true });
    const document = JSON.stringify(lower(state.sketch, state.meta));
    const envelope = { ...serializeSourceAuthoring(state), version };
    const restored = parseSourceAuthoring(JSON.parse(JSON.stringify(envelope)), INITIAL_SOURCE)!;
    expect(restored).toEqual(state);
    expect(JSON.stringify(lower(restored.sketch, restored.meta))).toBe(document);
  });
  it('accepts pending authoring only for its paired document, even when lowering matches another document', () => {
    const envelope = serializeSourceAuthoring(seed(INITIAL_SOURCE));
    const changed = structuredClone(INITIAL_SOURCE); changed.drive.motion = 'axial';
    expect(parseSourceAuthoring(envelope, changed)).toBeNull();
    expect(parseSourceAuthoring({ ...envelope, pending: true }, changed)).toBeNull();
    expect(parseSourceAuthoring({ ...envelope, pending: true }, INITIAL_SOURCE)).toBeNull();
    const pending = { ...envelope, pending: true, document: changed };
    expect(parseSourceAuthoring(pending, changed)?.sketch).toEqual(envelope.sketch);
    expect(parseSourceAuthoring(pending, INITIAL_SOURCE)).toBeNull();
    expect(parseSourceAuthoring({ ...pending, document: { drive: changed.drive, contour: changed.contour } }, changed)).not.toBeNull();
  });
  it.each([
    { version: 'wg-source-authoring-v3', opaque: { keep: true } },
    { version: 'wg-source-authoring-v1', sketch: { version: 'wg-sketch-v99', opaque: true } },
  ])('protects unknown formats without interpreting them: %j', envelope => {
    expect(newerEnvelope(envelope)).toBe(envelope);
    expect(parseSourceAuthoring(envelope, INITIAL_SOURCE)).toBeNull();
  });
  it('does not protect malformed sketches in a supported format as newer data', () => {
    expect(newerEnvelope(serializeSourceAuthoring(seed(INITIAL_SOURCE)))).toBeUndefined();
    expect(newerEnvelope({ version: 'wg-source-authoring-v2', sketch: { version: 'wg-sketch-v1' } })).toBeUndefined();
  });
  it('retains materialised context and preset mappings but never persists live context', () => {
    const context = hornAssemblyContext(INITIAL_ASSEMBLY.dimensions, [], undefined, 8);
    let state = seed(INITIAL_SOURCE);
    state.sketch = materialiseContext(state.sketch, context, 'horn.mouth').sketch;
    state = withSourceContext(state, context);
    state.meta.preset = { shape: 'flat', dimensions: { radius_mm: 'radius' }, values: { radius_mm: 8 } };
    const envelope = serializeSourceAuthoring(state);
    expect(envelope.meta).not.toHaveProperty('clearances');
    expect(envelope.meta).not.toHaveProperty('missingContext');
    expect(envelope).not.toHaveProperty('document');
    expect(envelope).not.toHaveProperty('context');
    expect(parseSourceAuthoring(envelope, INITIAL_SOURCE)?.sketch).toEqual(state.sketch);
    expect(parseSourceAuthoring(envelope, INITIAL_SOURCE)?.meta.preset).toEqual(state.meta.preset);
    expect(lower(state.sketch, state.meta)).toEqual(lower(seed(INITIAL_SOURCE).sketch, seed(INITIAL_SOURCE).meta));
    const bad = structuredClone(envelope); bad.sketch.entities.at(-1)!.construction = false;
    expect(parseSourceAuthoring(bad, INITIAL_SOURCE)).toBeNull();
  });
  it.each([null, {}, [], { version: 'wg-source-authoring-v99' }, { version: 'wg-source-authoring-v2', meta: {} }])('rejects unsafe or unknown input %j', raw => {
    expect(parseSourceAuthoring(raw, INITIAL_SOURCE)).toBeNull();
  });
});

it('preserves expressions and diameter in the authoring envelope while lowering to identical document bytes', () => {
  const state = seed(INITIAL_SOURCE), oldBytes = JSON.stringify(lower(state.sketch, state.meta));
  const axis = state.sketch.entities.find(e => e.id === 'axis'); if (axis?.kind !== 'line') throw new Error('axis');
  state.sketch.constraints.push({ id: 'size', kind: 'distanceX', a: axis.p1, b: 'rim', value: 8, driving: true, expression: '8 * 2', diameter: true });
  const envelope = JSON.parse(JSON.stringify(serializeSourceAuthoring(state)));
  const restored = parseSourceAuthoring(envelope, INITIAL_SOURCE)!;
  expect(restored).toEqual(state); expect(restored.sketch.constraints.at(-1)).toMatchObject({ expression: '8 * 2', diameter: true, value: 8 });
  expect(JSON.stringify(lower(restored.sketch, restored.meta))).toBe(oldBytes);
});

it('accepts diameter dimensions measured from the source pole on the axis', async () => {
  const { seedPreset, presetDefaults } = await import('./sourcePreset');
  const { expandedPreset } = await import('./presetTestSupport');
  const { applySourceCommand } = await import('./sourceContour');
  const state = seedPreset(expandedPreset('flat'), 'flat', presetDefaults.flat), id = state.meta.preset!.dimensions.radius_mm;
  const result = applySourceCommand(state.sketch, state.meta, { kind: 'setDiameter', id, diameter: true });
  if ('error' in result) throw new Error(result.error);
  expect(result.sketch.constraints.find(c => c.id === id)).toMatchObject({ diameter: true, value: presetDefaults.flat.radius_mm });
});

it('freezes expressions when source preset cleanup removes an invalidated mapped dimension', async () => {
  const { seedPreset, presetDefaults } = await import('./sourcePreset');
  const { expandedPreset } = await import('./presetTestSupport');
  const { applySourceCommand } = await import('./sourceContour');
  const state = seedPreset(expandedPreset('flat'), 'flat', presetDefaults.flat), input = state.meta.preset!.dimensions.radius_mm;
  state.sketch.constraints.push({ id: 'dependent', kind: 'distanceX', a: 'pole', b: 'rim', value: 8, driving: true, expression: `[${input}]` });
  const result = applySourceCommand(state.sketch, state.meta, { kind: 'splitAt', entity: 'piston', x: 4, y: 0 });
  if ('error' in result) throw new Error(result.error);
  expect(result.sketch.constraints.find(c => c.id === 'dependent')).not.toHaveProperty('expression');
  expect(result.notice).toContain('Split kept Horizontal distance at its last value because Radius was removed; its expression was cleared.');
});
