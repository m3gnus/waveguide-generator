// @vitest-environment node
import { describe, expect, it } from 'vitest';
import type { SourceDocument, SourcePatch, SourcePoint } from '../../api/sourceEditor';
import { drawnSource, editPatch, editPoint, INITIAL_SOURCE, removePoint } from '../../design/sourceContourEditing';
import { applyCommand } from '../ops';
import { validateSketch } from '../model';
import { applySourceCommand, lower, rules, seed } from './sourceContour';

function document(points: SourcePoint[], segments: SourcePatch[]): SourceDocument {
  return { contour: { version: 1, physical_source_id: 'diaphragm', rim_id: 'horn.throat', points, segments },
    drive: { channel_id: 'motor', motion: 'normal', weights: Object.fromEntries(segments.filter(s => s.role === 'moving').map((s, i) => [s.id, i ? -.5 : 0])) } };
}
const line = (id: string, start: string, end: string, role: SourcePatch['role'] = 'moving'): SourcePatch => ({ id, start, end, role, kind: 'line', center_mm: null, direction: 'ccw' });
function shape(kind: 'dome' | 'cone', width = 2, depth = .5, land = 1): SourceDocument {
  const radius = kind === 'dome' ? 8 : 3, height = kind === 'dome' ? 3 : 2, offset = kind === 'dome' ? 0 : -4;
  const points: SourcePoint[] = [{ id: 'pole', r_mm: 0, z_mm: height + offset }, { id: kind === 'dome' ? 'dome.rim' : 'cap.rim', r_mm: radius, z_mm: offset }];
  const segments: SourcePatch[] = [{ ...line(kind === 'dome' ? 'dome' : 'dust-cap', 'pole', points[1].id), kind: 'arc', center_mm: [0, (height ** 2 - radius ** 2) / (2 * height) + offset], direction: 'cw' }];
  if (kind === 'cone') { points.push({ id: 'cone.rim', r_mm: 10, z_mm: 0 }); segments.push(line('cone', 'cap.rim', 'cone.rim')); }
  if (width) {
    const a = points.at(-1)!; points.push({ id: 'surround.rim', r_mm: a.r_mm + width, z_mm: 0 });
    segments.push({ ...line('surround', a.id, 'surround.rim'), ...(depth ? { kind: 'arc' as const,
      center_mm: [a.r_mm + width / 2, (depth ** 2 - (width / 2) ** 2) / (2 * depth)] as [number, number], direction: depth > 0 ? 'cw' as const : 'ccw' as const } : {}) });
  }
  if (land) { const a = points.at(-1)!; points.push({ id: 'rim', r_mm: a.r_mm + land, z_mm: 0 }); segments.push(line('land', a.id, 'rim', 'rigid')); }
  return document(points, segments);
}
const copy = () => structuredClone(INITIAL_SOURCE);
function editorFixtures(): SourceDocument[] {
  const drawn = drawnSource(INITIAL_SOURCE, [{ id: 'a', r_mm: 3, z_mm: -4 }, { id: 'b', r_mm: 5, z_mm: -2 }, { id: 'c', r_mm: 9, z_mm: 1 }], (() => { let i = 0; return () => `patch-${++i}`; })());
  const merged = drawnSource(INITIAL_SOURCE, [{ id: 'a', r_mm: 0, z_mm: 0 }, { id: 'b', r_mm: 4, z_mm: 1 }, { id: 'c', r_mm: 8, z_mm: 0 }], (() => { let i = 0; return () => `${++i}`; })());
  const exact = copy(); exact.contour.points[1].r_mm = 8.123456789; exact.drive.weights.piston = .123456789;
  exact.contour.segments[0] = { ...exact.contour.segments[0], kind: 'arc', center_mm: [4.0617283945, .000123456], direction: 'ccw' };
  const arc = copy(); arc.contour.segments[0] = { ...arc.contour.segments[0], kind: 'arc', center_mm: [4, 0] };
  const typed = copy(); typed.contour.points[1].r_mm = 9.525;
  typed.contour.segments[0] = { ...arc.contour.segments[0], center_mm: [6.123457, -.000123] };
  const remote = copy(); remote.contour.physical_source_id = 'remote-source'; remote.contour.points[1].r_mm = 24;
  return [INITIAL_SOURCE, drawn, merged, removePoint(merged, 1), exact, arc, typed, remote,
    editPoint(INITIAL_SOURCE, 0, 5, 2), editPoint(INITIAL_SOURCE, 1, 10, 7), editPatch(INITIAL_SOURCE, 0, { kind: 'arc' }),
    { ...INITIAL_SOURCE, drive: { ...INITIAL_SOURCE.drive, weights: { piston: -.5 } } },
    { ...INITIAL_SOURCE, drive: { ...INITIAL_SOURCE.drive, weights: { piston: 0 } } },
    editPatch(INITIAL_SOURCE, 0, { role: 'rigid' })];
}
describe('source contour exact import and lowering', () => {
  it.each(editorFixtures().map((doc, i) => ({ name: `editor fixture ${i}`, doc })))('$name preserves every literal without solving', ({ doc }) => {
    const before = structuredClone(doc), { sketch, meta } = seed(doc);
    expect(validateSketch(sketch)).toEqual({ ok: true });
    expect(lower(sketch, meta)).toEqual({ ok: true, document: doc });
    expect(doc).toEqual(before);
    expect(sketch.constraints.map(c => c.kind)).toEqual(['pointOnLine', 'pointOnLine']);
    expect(sketch.entities.filter(e => !e.construction).map(e => e.id)).toEqual(doc.contour.segments.map(e => e.id));
    expect(sketch.entities.every(e => !e.data || !JSON.stringify(e.data).includes('weight'))).toBe(true);
  });
  it.each(['dome', 'cone'] as const)('round trips mesher %s formulas with signed surround depths and rigid land', kind => {
    for (const [w, d, l] of [[0, 0, 0], [2, 0, 1], [2, .5, 1], [2, -.5, 1], [2, 1, 1], [2, -1, 0]]) {
      const doc = shape(kind, w, d, l), { sketch, meta } = seed(doc);
      expect(lower(sketch, meta)).toEqual({ ok: true, document: doc }); expect(rules(sketch, meta)).toEqual([]);
    }
  });
  it('preserves arbitrary IDs, including references and UUID suffixes, and never allocates during lower', () => {
    const doc = copy(); doc.contour.points[0].id = 'axis'; doc.contour.points[1].id = 'baffle';
    doc.contour.segments[0] = { ...doc.contour.segments[0], id: 'b465ed90-dbd0-4111-b8a9-123456789012', start: 'axis', end: 'baffle' };
    doc.drive.weights = { [doc.contour.segments[0].id]: -0 };
    const { sketch, meta } = seed(doc), before = JSON.stringify(sketch);
    expect(lower(sketch, meta)).toEqual({ ok: true, document: doc }); expect(Object.is(meta.weights[doc.contour.segments[0].id], -0)).toBe(true);
    expect(JSON.stringify(sketch)).toBe(before); expect(meta.references.every(r => !['axis', 'baffle'].includes(r.id))).toBe(true);
  });
  it('orders by connected topology, excludes construction geometry and handles reversed edges', () => {
    const doc = shape('cone'), { sketch, meta } = seed(doc);
    const changed = { ...sketch, entities: [...sketch.entities].reverse().map(e => e.kind === 'line' && !e.construction ? { ...e, p1: e.p2, p2: e.p1 }
      : e.kind === 'arc' ? { ...e, start: e.end, end: e.start, ccw: !e.ccw } : e) };
    expect(lower(changed, meta)).toEqual({ ok: true, document: doc });
  });
  it('refuses branches, disconnected pieces, missing references and cycles with scoped problems', () => {
    const { sketch, meta } = seed(INITIAL_SOURCE);
    const branch = applyCommand(sketch, { kind: 'addLine', from: 'pole', to: { x: 4, y: 2 } });
    const disconnected = applyCommand(sketch, { kind: 'addLine', from: { x: 9, y: 2 }, to: { x: 10, y: 2 } });
    for (const r of [branch, disconnected]) {
      if ('error' in r) throw new Error(r.error);
      const result = lower(r.sketch, meta); expect(result.ok).toBe(false);
      expect(!result.ok && result.problems[0].ids).toContain('piston');
    }
    const broken = { ...sketch, entities: sketch.entities.filter(e => e.id !== 'piston') }; expect(lower(broken, meta).ok).toBe(false);
    expect(lower({ ...sketch, points: sketch.points.filter(p => p.id !== 'pole') }, meta).ok).toBe(false);
    expect(lower({ ...sketch, entities: [...sketch.entities, { kind: 'line', id: 'loop', p1: 'rim', p2: 'pole' }] }, meta).ok).toBe(false);
  });
  it.each([0, -.5])('copies literal weight %s on split, then removes it on deletion', weight => {
    const doc = copy(); doc.drive.weights.piston = weight;
    const { sketch, meta } = seed(doc);
    const split = applySourceCommand(sketch, meta, { kind: 'splitAt', entity: 'piston', x: 4, y: 0 });
    if ('error' in split) throw new Error(split.error);
    const right = split.created[1]; expect(split.meta.weights).toEqual({ piston: weight, [right]: weight });
    const lowered = lower(split.sketch, split.meta); expect(lowered.ok).toBe(true);
    expect(lowered.ok && lowered.document.drive.weights).toEqual(split.meta.weights);
    expect(meta.weights).toEqual({ piston: weight });
    const deleted = applySourceCommand(split.sketch, split.meta, { kind: 'deleteEntity', id: right });
    if ('error' in deleted) throw new Error(deleted.error);
    expect(deleted.meta.weights).toEqual({ piston: weight });
    expect(lower(deleted.sketch, deleted.meta).ok).toBe(false);
  });
  it('lowers a new segment joined by an explicit endpoint snap with one shared output joint', () => {
    const { sketch, meta } = seed(INITIAL_SOURCE);
    const result = applySourceCommand(sketch, meta, { kind: 'addLine', from: { x: 8, y: 0,
      snap: { x: 8, y: 0, glyph: 'endpoint', constraint: { kind: 'coincident', point: 'rim' } } }, to: { x: 12, y: 0, snap: { x: 12, y: 0, glyph: 'baffle', constraint: { kind: 'pointOnLine', line: meta.references[1].id } } } });
    if ('error' in result) throw new Error(result.error);
    const lowered = lower(result.sketch, result.meta);
    expect(lowered.ok).toBe(true);
    if (lowered.ok) {
      expect(lowered.document.contour.points.map(p => p.r_mm)).toEqual([0, 8, 12]);
      expect(lowered.document.contour.segments[1].start).toBe('rim');
      expect(lowered.document.drive.weights).toEqual({ piston: 1, [result.created.at(-1)!]: 1 });
    }
    expect(rules(result.sketch, result.meta)).toEqual([]);
  });
});

describe('analytic mesher rule agreement table', () => {
  const cases: { name: string; doc: SourceDocument; code: string }[] = [];
  const add = (name: string, edit: (d: SourceDocument) => void, code: string) => { const doc = copy(); edit(doc); cases.push({ name, doc, code }); };
  add('decreasing radius', d => { d.contour.points[1].r_mm = -1; }, 'radius-order');
  add('vertical output', d => { d.contour.points[1].r_mm = 0; }, 'radius-order');
  add('rim off baffle', d => { d.contour.points[1].z_mm = 2e-9; }, 'rim');
  add('pole off axis', d => { d.contour.points[0].r_mm = 2e-9; }, 'pole');
  add('no moving patch', d => { d.contour.segments[0].role = 'rigid'; d.drive.weights = {}; }, 'moving');
  add('sweep greater than pi', d => { d.contour.points[0].z_mm = 1; d.contour.points[1].r_mm = 1; d.contour.segments[0] = { ...d.contour.segments[0], kind: 'arc', center_mm: [0, 0], direction: 'ccw' }; }, 'arc-fold');
  add('interior radial extremum', d => {
    const a = Math.PI / 4, b = -Math.PI / 6;
    d.contour.points = [{ id: 'pole', r_mm: 0, z_mm: Math.sin(a) - Math.sin(b) }, { id: 'rim', r_mm: Math.cos(b) - Math.cos(a), z_mm: 0 }];
    d.contour.segments[0] = { ...d.contour.segments[0], kind: 'arc', center_mm: [-Math.cos(a), -Math.sin(b)], direction: 'cw' };
  }, 'arc-fold');
  add('different endpoint radii', d => { d.contour.segments[0] = { ...d.contour.segments[0], kind: 'arc', center_mm: [3, 0] }; }, 'circle');
  add('weight missing', d => { d.drive.weights = {}; }, 'weights');
  it.each(cases)('$name is rejected as $code', ({ doc, code }) => {
    const { sketch, meta } = seed(doc); const problems = rules(sketch, meta);
    expect(problems.map(p => p.code)).toContain(code); expect(problems.every(p => p.ids.length > 0)).toBe(true);
  });
  it('matches the circle tolerance just below and above the relative boundary', () => {
    for (const [delta, accepted] of [[.5e-9, true], [2e-9, false]] as const) {
      const doc = document([{ id: 'pole', r_mm: 0, z_mm: 1 }, { id: 'rim', r_mm: 1 + delta, z_mm: 0 }],
        [{ ...line('arc', 'pole', 'rim'), kind: 'arc', center_mm: [0, 0], direction: 'cw' }]);
      const { sketch, meta } = seed(doc); expect(rules(sketch, meta).length === 0).toBe(accepted);
    }
  });
  it('accepts 256 patches and refuses 257', () => {
    for (const n of [256, 257]) {
      const doc = document(Array.from({ length: n + 1 }, (_, i) => ({ id: `point${i}`, r_mm: i, z_mm: 0 })),
        Array.from({ length: n }, (_, i) => line(`segment${i}`, `point${i}`, `point${i + 1}`)));
      const { sketch, meta } = seed(doc); expect(rules(sketch, meta).some(p => p.code === 'patch-count')).toBe(n === 257);
    }
  });
});
