import { serializeSourceAuthoring } from './sourceAuthoring';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { hornAssemblyContext, standaloneContext, wooferAssemblyContext } from './sourceContext';
import { lower, rules, seed, withSourceContext, withSourceLimit } from './sourceContour';
import { contextData, geometryFingerprint, materialiseContext } from '../context';
import { entityPoints } from '../model';
import { toSolverSketch } from '../session';
import { SketchController } from '../controller';
import { applySourceCommand } from './sourceContour';
import { INITIAL_ASSEMBLY } from '../../design/SourceAssemblyEditor';
import { INITIAL_SOURCE } from '../../design/sourceContourEditing';
import { sourceEditorApi } from '../../api/sourceEditor';
import { namespaceStorage } from '../../stores/durableSettings';
import { readSourceAuthoring } from '../../design/SourceContourEditor';
import { expandedPreset } from './presetTestSupport';
import { uniqueProblems } from '../adapter';
import { realSessionReady } from '../testSupport';

const d = { ...INITIAL_ASSEMBLY.dimensions, width_mm: 80, height_mm: 100, depth_mm: 60, horn_length_mm: 30, mouth_radius_mm: 20, horn_xy_mm: [28, -7] as [number, number] };
const plug = { id: 'core', z0_mm: 2, z1_mm: 6, inner0_mm: 0, outer0_mm: 2, inner1_mm: 0, outer1_mm: 3 };
const horn = (z = 2) => hornAssemblyContext(d, [{ ...plug, z0_mm: z }], undefined, 12.7);
const storage = namespaceStorage('sourceContourDraft');
afterEach(() => { vi.unstubAllGlobals(); storage.removeItem(''); });

describe('source context frame golden numbers', () => {
  it('places the throat, mouth, back, off-centre walls and plug corners in the horn frame', () => {
    const context = horn(), item = (id: string) => context.items.find(i => i.id === id)!;
    expect(context.rimPlaneLabel).toBe('Throat plane (z = 0.00)');
    expect(item('horn.throat').geometry).toEqual([{ x: 12.7, y: 0 }]);
    expect(item('horn.mouth').geometry).toEqual([{ x: 20, y: 30 }]);
    expect(item('rim-plane').geometry).toEqual([{ x: 0, y: 0 }, { x: 12.7, y: 0 }]);
    expect(item('box.back').geometry).toEqual([{ x: 0, y: -30 }, { x: 68, y: -30 }]);
    expect(item('box.side.near').geometry).toEqual([{ x: 12, y: -30 }, { x: 12, y: 30 }]);
    expect(item('box.side.near').label).toBe('Enclosure side (nearest, +x) 12.00 mm');
    expect(item('box.side.far').geometry).toEqual([{ x: 68, y: -30 }, { x: 68, y: 30 }]);
    expect(item('box.side.far').label).toBe('Enclosure side (farthest, −x) 68.00 mm');
    expect(item('box.side.far').emphasis).toBe('far');
    expect(item('plug/core/inlet').geometry).toEqual([{ x: 0, y: 2 }, { x: 2, y: 2 }]);
    expect(item('plug/core/body').geometry).toEqual([{ x: 2, y: 2 }, { x: 3, y: 6 }, { x: 0, y: 6 }, { x: 0, y: 2 }]);
    expect(item('plug/core/keepout').geometry[2].y).toBe(1.9);
    expect(item('keepout.back').geometry[2].y).toBe(-29.9);
    expect(item('keepout.front').geometry[2].y).toBe(29.9);
  });
  it('formats every numeric context value to two decimals, including the front z', () => {
    const context = hornAssemblyContext({ ...d, horn_length_mm: 119.70664012995216 }, [], undefined, 12.7);
    expect(context.items.find(i => i.id === 'baffle.front')!.label).toBe('Front baffle (z = 119.71)');
    expect(context.items.find(i => i.id === 'keepout.front')!.label).toBe('Minimum clearance 0.10 mm');
    expect(context.items.find(i => i.id === 'horn.mouth')!.label).toBe('Mouth r 20.00');
  });
  it('splits a cone only at the planes used by phase_plug.wall_edges', () => {
    expect(horn().items.find(i => i.id === 'horn.wall')!.geometry).toHaveLength(4);
    expect(hornAssemblyContext(d, [], undefined, 12.7).items.find(i => i.id === 'horn.wall')!.geometry).toEqual([{ x: 12.7, y: 0 }, { x: 20, y: 30 }]);
    const wall: [number, number][] = [[12.7, 0], [18, 12], [20, 30]];
    expect(hornAssemblyContext(d, [plug], wall, 12.7).items.find(i => i.id === 'horn.wall')!.geometry).toEqual(wall.map(([x, y]) => ({ x, y })));
  });
  it('uses the woofer baffle origin, aperture and radial collar', () => {
    const context = wooferAssemblyContext(d, 6), item = (id: string) => context.items.find(i => i.id === id)!;
    expect(context.rimPlaneLabel).toBe('Front baffle (z = 0.00)');
    expect(item('box.back').geometry[0].y).toBe(-60);
    expect(item('woofer.aperture').geometry).toEqual([{ x: 8, y: 0 }]);
    expect(item('woofer.collar').geometry).toEqual([{ x: 6, y: 0 }, { x: 8, y: 0 }]);
  });
  it('converts standalone attachment centres and conical housing exactly once', () => {
    const context = standaloneContext({ kind: 'baffle', dimensions: { width_mm: 80, height_mm: 100, depth_mm: 60, center_mm: [28, -7, 123], aperture_radius_mm: 14 } }, 10);
    expect(context.items.find(i => i.id === 'box.side.near')!.geometry).toEqual([{ x: 12, y: -60 }, { x: 12, y: 0 }]);
    const cone = standaloneContext({ kind: 'horn', dimensions: { length_mm: 30, mouth_radius_mm: 20, housing_radius_mm: 22, backing_depth_mm: 12 } }, 8);
    expect(cone.items.find(i => i.id === 'horn.wall')!.geometry).toEqual([{ x: 8, y: 0 }, { x: 20, y: 30 }]);
    expect(cone.items.find(i => i.id === 'box.back')!.geometry).toEqual([{ x: 22, y: -12 }, { x: 0, y: -12 }]);
    expect(standaloneContext(undefined, 8).rimPlaneLabel).toBe('Rim plane (z = 0.00)');
  });
});

describe('context ownership and canonical separation', () => {
  it('materialises a point through its owner once, fixes every reference entity, and never lowers it', () => {
    const initial = seed(INITIAL_SOURCE), context = horn();
    const atPoint = materialiseContext(initial.sketch, context, 'plug/core/inlet.outer');
    const atFace = materialiseContext(atPoint.sketch, context, 'plug/core/inlet');
    const line = atFace.sketch.entities.find(e => e.id === atFace.id)!;
    expect(line.kind).toBe('line'); expect(line.reference).toBe(true); expect(line.construction).toBe(true);
    expect(line.data).toEqual({ context: 'plug/core/inlet', fingerprint: geometryFingerprint(context.items.find(i => i.id === 'plug/core/inlet')!.geometry), label: 'Plug core inlet' });
    expect(entityPoints(line)).toContain(atPoint.id);
    expect(atFace.sketch.entities).toHaveLength(initial.sketch.entities.length + 1);
    const solver = toSolverSketch(atFace.sketch, []);
    expect(solver.entities.filter(e => entityPoints(line).includes(e.id))).toEqual(expect.arrayContaining(entityPoints(line).map(id => expect.objectContaining({ id, fixed: true }))));
    const before = lower(initial.sketch, initial.meta), after = lower(atFace.sketch, withSourceContext(initial, context).meta);
    expect(after).toEqual(before); expect(JSON.stringify(after)).toBe(JSON.stringify(before));
  });
  it('never materialises the dense wall and supports its semantic mouth endpoint', () => {
    const context = horn();
    expect(() => materialiseContext(seed(INITIAL_SOURCE).sketch, context, 'horn.wall')).toThrow('display-only');
    const result = materialiseContext(seed(INITIAL_SOURCE).sketch, context, 'horn.mouth');
    expect(result.sketch.points.find(p => p.id === result.id)).toMatchObject({ x: 20, y: 30 });
    expect(result.sketch.entities.filter(e => contextData(e))).toHaveLength(1);
  });
  it('keeps disappeared plug labels and diagnostics distinct, including IDs containing slashes', () => {
    const initial = seed(INITIAL_SOURCE);
    const context = hornAssemblyContext(d, [plug, { ...plug, id: 'vane/A', inner0_mm: 3, outer0_mm: 4 }], undefined, 12.7);
    let sketch = materialiseContext(initial.sketch, context, 'plug/core/inlet').sketch;
    sketch = materialiseContext(sketch, context, 'plug/vane/A/inlet').sketch;
    const state = withSourceContext({ ...initial, sketch }, hornAssemblyContext(d, [], undefined, 12.7));
    const missing = uniqueProblems(rules(state.sketch, state.meta)).filter(p => p.code === 'missing-context');
    expect(missing).toHaveLength(2);
    expect(missing.map(p => p.message)).toContain('Plug vane/A inlet no longer exists. Re-attach or delete the constraint');
    expect(missing[0].ids).not.toEqual(missing[1].ids);
  });
  it('round-trips v2 materialised references, reads v1, and keeps host context out of storage', () => {
    const initial = seed(INITIAL_SOURCE), context = horn();
    storage.setItem('', JSON.stringify({ version: 'wg-source-authoring-v1', document: INITIAL_SOURCE, ...initial }));
    expect(readSourceAuthoring(INITIAL_SOURCE)).toEqual(initial);
    const result = materialiseContext(initial.sketch, context, 'plug/core/inlet');
    const state = withSourceContext({ ...initial, sketch: result.sketch }, context);
    const envelope = serializeSourceAuthoring(state);
    expect(envelope.version).toBe('wg-source-authoring-v2');
    expect(envelope.meta).not.toHaveProperty('clearances'); expect(envelope).not.toHaveProperty('context');
    expect(context.fingerprint).toMatch(/^[0-9a-f]{16}$/);
    expect(JSON.stringify(envelope)).not.toContain('keepout');
    storage.setItem('', JSON.stringify(envelope));
    expect(readSourceAuthoring(INITIAL_SOURCE).sketch).toEqual(state.sketch);
    const malformed = structuredClone(envelope); malformed.sketch.entities.at(-1)!.construction = false;
    storage.setItem('', JSON.stringify(malformed)); expect(readSourceAuthoring(INITIAL_SOURCE).sketch).toEqual(initial.sketch);
  });
  it('keeps actual validate and all assembly request bodies byte-identical with materialised context', async () => {
    const bodies: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_url, init) => { bodies.push(init.body); return { ok: true, json: async () => ({}), blob: async () => new Blob() }; }));
    const initial = seed(INITIAL_SOURCE), at = materialiseContext(initial.sketch, horn(), 'plug/core/inlet');
    const before = lower(initial.sketch, initial.meta), after = lower(at.sketch, withSourceContext(initial, horn()).meta);
    if (!before.ok || !after.ok) throw new Error('Fixture must lower');
    await sourceEditorApi.validate(before.document); await sourceEditorApi.validate(after.document);
    expect(bodies[0]).toBe(bodies[1]);
    const baseline = { ...structuredClone(INITIAL_ASSEMBLY), horn_config: { formula: 'OSSE' } };
    for (const action of [sourceEditorApi.validateAssembly, sourceEditorApi.exportAssembly, sourceEditorApi.ingestAssembly]) {
      await action({ ...baseline, horn: before.document }); await action({ ...baseline, horn: after.document });
      expect(bodies.at(-1)).toBe(bodies.at(-2));
    }
    expect(bodies.every(body => !body.includes('fingerprint') && !body.includes('context'))).toBe(true);
    await sourceEditorApi.hornWall({ formula: 'OSSE' }); expect(bodies.at(-1)).toBe('{"horn_config":{"formula":"OSSE"}}');
  });
});

describe('mesher clearance golden boundaries', () => {
  it.each([0.899999, 0.9, 0.900001])('warns at the plug threshold for a dome of height %s', height_mm => {
    const state = seed(expandedPreset('dome', { radius_mm: 8, height_mm, surround_width_mm: 0, surround_depth_mm: 0, land_width_mm: 0 }));
    state.meta.clearances = { plugZ0: 1 };
    expect(rules(state.sketch, state.meta).some(p => p.code === 'plug-clearance' && p.severity === 'warning')).toBe(1 - height_mm <= .1);
  });
  it.each([-0.900001, -0.9, -0.899999])('warns at the back threshold for z=%s', z => {
    const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[0].z_mm = z;
    const state = seed(doc); state.meta.clearances = { backZ: -1 };
    expect(rules(state.sketch, state.meta).some(p => p.code === 'back-clearance' && p.severity === 'warning')).toBe(z + 1 <= .1);
  });
  it.each([0.899999, 0.9, 0.900001])('warns at the front threshold for z=%s', z => {
    const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[0].z_mm = z;
    const state = seed(doc); state.meta.clearances = { frontZ: 1 };
    expect(rules(state.sketch, state.meta).some(p => p.code === 'front-clearance' && p.severity === 'warning')).toBe(1 - z <= .1);
  });
});

describe('associative dimensions with the real solver', () => {
  beforeAll(realSessionReady);
  it('moves a plug face, re-solves the pole dimension once, notices the update and keeps undo/redo usable', async () => {
    const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[0].z_mm = 1;
    const context = horn();
    const initial = withSourceContext(seed(doc), context);
    const controller = new SketchController(initial, s => s.meta.references, (s, c) => {
      const result = applySourceCommand(s.sketch, s.meta, c); return 'error' in result ? result : result;
    }, () => {});
    try {
      const face = await controller.materialiseContext(context, 'plug/core/inlet');
      await controller.constraint({ kind: 'pointLineDistance', point: 'pole', line: face.id, value: 1, driving: true });
      await controller.metadata(meta => ({ ...meta, channel_id: 'changed' }));
      await controller.replaceContext(s => withSourceContext(s, horn(3)), 'Reference context changed; contour updated');
      expect(controller.current.sketch.points.find(p => p.id === 'pole')!.y).toBeCloseTo(2, 8);
      expect(controller.notice).toBe('Reference context changed; contour updated');
      await controller.undo(); expect(controller.current.meta.channel_id).toBe(INITIAL_SOURCE.drive.channel_id);
      expect(controller.current.sketch.points.find(p => p.id === 'pole')!.y).toBeCloseTo(2, 8);
      await controller.undo(); expect(controller.current.sketch.constraints.some(c => c.kind === 'pointLineDistance')).toBe(false);
      await controller.undo(); expect(controller.current.sketch.entities.some(e => contextData(e))).toBe(false);
      await controller.undo(true); expect(controller.current.sketch.entities.some(e => contextData(e))).toBe(true);
      const old = controller.current.sketch;
      await controller.replaceContext(s => withSourceContext(s, hornAssemblyContext(d, [], undefined, 12.7)));
      expect(controller.current.sketch.points.filter(p => entityPoints(old.entities.find(e => contextData(e))!).includes(p.id))).toEqual(old.points.filter(p => entityPoints(old.entities.find(e => contextData(e))!).includes(p.id)));
      expect(rules(controller.current.sketch, controller.current.meta)).toContainEqual(expect.objectContaining({ code: 'missing-context', severity: 'warning', message: 'Plug core inlet no longer exists. Re-attach or delete the constraint' }));
    } finally { controller.destroy(); }
  });
  it('keeps exact canonical bytes through an unused display and clearance refresh', async () => {
    const document = expandedPreset('dome', { height_mm: 2.3 });
    const initial = withSourceContext(seed(document), horn());
    const controller = new SketchController(initial, s => s.meta.references, (s, c) => {
      const result = applySourceCommand(s.sketch, s.meta, c); return 'error' in result ? result : result;
    }, () => {});
    try {
      const before = JSON.stringify(lower(initial.sketch, initial.meta));
      const changed = hornAssemblyContext({ ...d, depth_mm: 61 }, [plug], undefined, 12.7);
      await controller.replaceContext(s => withSourceContext(s, changed));
      expect(JSON.stringify(lower(controller.current.sketch, controller.current.meta))).toBe(before);
    } finally { controller.destroy(); }
  });
  it('keeps the hard throat following its host and preserves canonical lower coordinates', async () => {
    const initial = withSourceLimit(seed(INITIAL_SOURCE), { kind: 'throat', radius: 12.7, hard: true });
    const next = withSourceLimit(initial, { kind: 'throat', radius: 13, hard: true });
    expect(next.sketch.points.find(p => p.id === 'rim')!.x).toBe(13);
    expect(lower(next.sketch, next.meta)).toMatchObject({ ok: true, document: { contour: { points: [{ r_mm: 0 }, { r_mm: 13 }] } } });
  });
});

describe('fix-round reference lifecycle', () => {
  it('drops a materialised line and its missing warning with its last constraint, including endpoint constraints', () => {
    for (const endpoint of [false, true]) {
      const initial = seed(INITIAL_SOURCE), context = horn();
      const picked = materialiseContext(initial.sketch, context, endpoint ? 'plug/core/inlet.outer' : 'plug/core/inlet');
      const result = applySourceCommand(picked.sketch, initial.meta, { kind: 'addConstraint', constraint: endpoint
        ? { kind: 'distanceY', a: 'pole', b: picked.id, value: 2, driving: true }
        : { kind: 'pointLineDistance', point: 'pole', line: picked.id, value: 2, driving: true } });
      if ('error' in result) throw new Error(result.error);
      const missing = withSourceContext(result, hornAssemblyContext(d, [], undefined, 12.7));
      expect(rules(missing.sketch, missing.meta).some(p => p.code === 'missing-context')).toBe(true);
      const constraint = result.sketch.constraints.at(-1)!;
      const cleaned = applySourceCommand(missing.sketch, missing.meta, { kind: 'removeConstraint', id: constraint.id });
      if ('error' in cleaned) throw new Error(cleaned.error);
      expect(cleaned.sketch.entities.some(e => contextData(e))).toBe(false);
      expect(rules(cleaned.sketch, cleaned.meta).some(p => p.code === 'missing-context')).toBe(false);
      expect(cleaned.sketch.points).toHaveLength(initial.sketch.points.length);
    }
  });
  it('keeps a disappeared enclosure item label instead of showing its raw id', () => {
    const initial = seed(INITIAL_SOURCE), context = horn();
    const picked = materialiseContext(initial.sketch, context, 'box.side.near');
    const changed = withSourceContext({ ...initial, sketch: picked.sketch }, standaloneContext(undefined, 8));
    expect(rules(changed.sketch, changed.meta)).toContainEqual(expect.objectContaining({ message: `${context.items.find(i => i.id === 'box.side.near')!.label} no longer exists. Re-attach or delete the constraint` }));
  });
  it.each([-1e-6, 0, 1e-6])('uses zero strict margin for conical front/back clearance at delta %s', delta => {
    for (const front of [true, false]) {
      const document = structuredClone(INITIAL_SOURCE); document.contour.points[0].z_mm = front ? 1 : -1;
      const context = standaloneContext({ kind: 'horn', dimensions: { length_mm: front ? 1 + delta : 30, backing_depth_mm: front ? 12 : 1 + delta, mouth_radius_mm: 20, housing_radius_mm: 22 } }, 8);
      const state = withSourceContext(seed(document), context);
      expect(state.meta.clearances?.margin).toBe(0);
      expect(rules(state.sketch, state.meta).some(p => p.code === (front ? 'front-clearance' : 'back-clearance'))).toBe(delta <= 0);
      if (delta <= 0) expect(rules(state.sketch, state.meta).find(p => p.code === (front ? 'front-clearance' : 'back-clearance'))?.message).toBe('Source must stay inside the housing');
    }
  });
});
