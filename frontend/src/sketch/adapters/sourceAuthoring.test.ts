import { describe, expect, it } from 'vitest';
import { INITIAL_SOURCE } from '../../design/sourceContourEditing';
import { materialiseContext } from '../context';
import { hornAssemblyContext } from './sourceContext';
import { INITIAL_ASSEMBLY } from '../../design/SourceAssemblyEditor';
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
