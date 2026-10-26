// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { editRecord } from './editRecord';
import { sourceContourAdapter as adapter } from '../adapters/sourceContour';
import { parseSourceAuthoring } from '../adapters/sourceAuthoring';
import { INITIAL_SOURCE } from '../../design/sourceContourEditing';

it('retains exact valid document bytes and the authored sidecar together', () => {
  const state = adapter.seed(INITIAL_SOURCE, undefined), record = editRecord(adapter, state, null);
  expect(record.status).toBe('valid');
  const lowered = adapter.lower(state);
  if (!lowered.ok) throw new Error('Expected valid fixture');
  expect(JSON.stringify(record.document)).toBe(JSON.stringify(lowered.document));
  expect(record.authoring).toEqual(adapter.serializeAuthoring(state));
  expect(record.draftKey).toBe(JSON.stringify(record.authoring));
});
it('keeps unfinished authoring with the last valid document, including a never-valid object', () => {
  const state = adapter.seed(INITIAL_SOURCE, undefined);
  state.sketch.entities = state.sketch.entities.filter(entity => entity.construction);
  const record = editRecord(adapter, state, INITIAL_SOURCE);
  expect(record.status).toBe('unfinished'); expect(record.document).toBe(INITIAL_SOURCE);
  expect(record.authoring).toEqual(adapter.serializeAuthoring(state));
  expect(editRecord(adapter, state, null).document).toBeNull();
});
it('keeps the last valid document when completed authoring violates a rule', () => {
  const state = adapter.seed(INITIAL_SOURCE, undefined); state.meta.weights.piston = NaN;
  const record = editRecord(adapter, state, INITIAL_SOURCE);
  expect(record.status).toBe('invalid'); expect(record.document).toBe(INITIAL_SOURCE);
});

it.each(['rigid', 'radius-order', 'circle'] as const)('round-trips a rule-invalid %s draft with constraints and names', kind => {
  const state = adapter.seed(INITIAL_SOURCE, undefined);
  state.sketch.entities[0].data = { name: 'Named diaphragm', role: kind === 'rigid' ? 'rigid' : 'moving' };
  state.sketch.constraints.push({ id: 'width', kind: 'distance', a: 'pole', b: 'rim', value: 8, driving: false, label: 'Width' });
  if (kind === 'rigid') state.meta.weights = {};
  if (kind === 'radius-order') state.sketch.points.find(p => p.id === 'rim')!.x = -1;
  if (kind === 'circle') {
    state.sketch.points.push({ id: 'center', x: 3, y: 2 });
    state.sketch.entities[0] = { ...state.sketch.entities[0], kind: 'arc', start: 'pole', end: 'rim', center: 'center', ccw: true };
  }
  expect(adapter.lower(state).ok).toBe(true);
  const record = editRecord(adapter, state, INITIAL_SOURCE);
  expect(record.status).toBe('invalid');
  expect(record.authoring).toHaveProperty('pending', true);
  const envelope = JSON.parse(JSON.stringify({ ...record.authoring as object, document: record.document }));
  expect(parseSourceAuthoring(envelope, INITIAL_SOURCE)).toEqual(state);
  expect(adapter.seed(INITIAL_SOURCE, envelope)).toEqual(state);
});
it('shares lowering and status with serialization once per record', () => {
  const state = adapter.seed(INITIAL_SOURCE, undefined);
  const lower = vi.fn(adapter.lower), status = vi.fn(adapter.status), serializeAuthoring = vi.fn(adapter.serializeAuthoring);
  editRecord({ ...adapter, lower, status, serializeAuthoring }, state, INITIAL_SOURCE);
  expect(lower).toHaveBeenCalledTimes(1);
  expect(status).toHaveBeenCalledExactlyOnceWith(state, lower.mock.results[0].value);
  expect(serializeAuthoring).toHaveBeenCalledExactlyOnceWith(state, status.mock.results[0].value);
});
