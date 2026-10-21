import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { SketchCanvas } from '../SketchCanvas';
import { sourceContourAdapter as adapter, seed, lower, withSourceContext, withSourceLimit, type SourceMeta } from './sourceContour';
import { serializeSourceAuthoring } from './sourceAuthoring';
import { sourceBounds, sourceInitialView } from './sourceDisplay';
import { editorFixtures } from './sourceContourTestSupport';
import { expandedPreset } from './presetTestSupport';
import { wooferAssemblyContext } from './sourceContext';
import { INITIAL_SOURCE } from '../../design/sourceContourEditing';
import { selectHits } from '../tools/select';
import { sketchGeometry } from '../sketchGeometry';
import { realSessionReady } from '../testSupport';
import { fit, screenToWorld, worldToScreen, type View } from '../view';
import type { Authoring } from '../controller';
import type { Command } from '../commands';
import type { Diagnosis } from '../session';
import type { DisplayItem, WorkspaceSession } from '../workspace/types';
import { sketchViewKey } from '../viewPreferences';

const context = wooferAssemblyContext({ width_mm: 200, height_mm: 300, depth_mm: 80, front_z_mm: 0,
  woofer_xy_mm: [0, 0], horn_xy_mm: [0, 60], horn_length_mm: 40, mouth_radius_mm: 30, aperture_radius_mm: 14 }, 13);
const dimension = seed(INITIAL_SOURCE);
dimension.sketch.constraints.push({ id: 'radius', kind: 'distanceX', a: 'pole', b: 'rim', value: 8, driving: true, label: 'Typed radius' });
const fixtures = [
  { name: 'HF contour', state: withSourceLimit(seed(expandedPreset('dome')), { kind: 'throat', radius: 11, hard: true }) },
  { name: 'woofer with aperture context', state: withSourceContext(withSourceLimit(seed(expandedPreset('cone')), { kind: 'aperture', radius: 14 }), context), context },
  { name: 'conflict state', state: withSourceLimit(structuredClone(dimension), { kind: 'throat', radius: 10, hard: true }), conflict: true },
  { name: 'dimension editing', state: dimension, selection: ['radius'] },
];
beforeAll(realSessionReady);
afterEach(() => { localStorage.removeItem(sketchViewKey); vi.restoreAllMocks(); });

function itemPath(item: DisplayItem, view: View): string {
  if (item.kind === 'polyline') return item.points.map((p, i) => { const at = worldToScreen(view, p); return `${i ? 'L' : 'M'}${at.x},${at.y}`; }).join(' ');
  if (item.kind !== 'arc') throw new Error('Expected a curve');
  const a = worldToScreen(view, item.start), b = worldToScreen(view, item.end);
  const radius = Math.hypot(item.start.x - item.center.x, item.start.y - item.center.y);
  const start = Math.atan2(item.start.y - item.center.y, item.start.x - item.center.x), end = Math.atan2(item.end.y - item.center.y, item.end.x - item.center.x);
  const turn = (angle: number) => (angle % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
  const sweep = item.ccw ? turn(end - start) : -turn(start - end);
  return `M${a.x},${a.y} A${radius * view.scale},${radius * view.scale} 0 ${Math.abs(sweep) > Math.PI ? 1 : 0} ${item.ccw ? 0 : 1} ${b.x},${b.y}`;
}

describe('W1 source workspace adapter', () => {
  it.each(fixtures)('display and hits match the legacy canvas: $name', async fixture => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.removeItem(sketchViewKey);
    const host = document.createElement('div'), root = createRoot(host);
    const { state } = fixture, selection = fixture.selection ?? [];
    const diagnosis = fixture.conflict ? { status: 'failed', constraintStates: { radius: 'conflicting' }, pointDetermined: undefined,
      sketch: state.sketch, points: {}, timings: { totalMs: 0, updateMs: 0, solveMs: 0, readbackMs: 0, marshalMs: 0 } } as Diagnosis : undefined;
    try {
      await act(async () => root.render(<SketchCanvas sketch={state.sketch} references={state.meta.references} problems={[]} context={fixture.context} selection={selection} diagnosis={diagnosis}/>));
      // The legacy canvas deliberately ignores host context on its initial fit.
      const view = sourceInitialView(state.sketch), low = screenToWorld(view, { x: 0, y: 360 }), high = screenToWorld(view, { x: 640, y: 0 });
      const model = adapter.display(state, { selection, highlighted: fixture.conflict ? ['radius'] : undefined, scale: view.scale, view, context: fixture.context,
        bounds: { minX: low.x, minY: low.y, maxX: high.x, maxY: high.y }, viewport: { width: 640, height: 360 } });
      const curves = model.items.filter(item => item.kind === 'arc' || item.kind === 'polyline');
      const paths = [...host.querySelectorAll<SVGPathElement>('.sketch-entity')];
      expect(curves.map(item => item.id)).toEqual(paths.map(path => path.dataset.id));
      curves.forEach((item, i) => {
        expect(itemPath(item, view)).toBe(paths[i].getAttribute('d'));
        expect(item.state?.includes('selected')).toBe(paths[i].classList.contains('selected'));
        expect(item.state?.includes('construction')).toBe(paths[i].classList.contains('construction'));
      });
      const points = model.items.filter(item => item.kind === 'point');
      expect(points.map(item => item.id)).toEqual([...host.querySelectorAll<SVGElement>('.sketch-point')].map(point => point.dataset.id));
      for (const item of points) if (item.kind === 'point') {
        const drawn = host.querySelector<SVGCircleElement>(`circle.sketch-point[data-id="${item.id}"]`);
        if (drawn) { const at = worldToScreen(view, item.at); expect(Number(drawn.getAttribute('cx'))).toBe(at.x); expect(Number(drawn.getAttribute('cy'))).toBe(at.y); }
      }
      for (const item of model.context) expect(host.textContent).toContain(item.label);
      const dimensions = model.items.filter(item => item.kind === 'dimension');
      expect(dimensions.map(item => item.id)).toEqual([...host.querySelectorAll('[data-constraint-id].sketch-dimension')].map(node => node.getAttribute('data-constraint-id')));
      for (const item of dimensions) if (item.kind === 'dimension') {
        expect(host.querySelector(`[data-constraint-id="${item.id}"] .sketch-extension-lines`)?.getAttribute('d')).toBe(item.layout.path);
        expect([...host.querySelector(`[data-constraint-id="${item.id}"] text`)!.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('')).toBe(item.text);
      }
      const geometry = sketchGeometry(state.sketch, state.meta.references, []);
      const hidden = [...geometry.hiddenCentres, ...geometry.helperPoints];
      for (const at of [...state.sketch.points, ...curves.flatMap(item => item.kind === 'polyline' ? item.points : item.kind === 'arc' ? [item.start, item.end] : [])]) {
        const hits = adapter.hits(state, at, 8 / view.scale);
        expect(hits.map(hit => hit.id)).toEqual(selectHits(state.sketch, at, 8 / view.scale, hidden));
        expect(hits.every(hit => hit.kind === 'point' || hit.kind === 'curve')).toBe(true);
      }
    } finally { await act(async () => root.unmount()); }
  });

  it.each(editorFixtures().map((document, i) => ({ document, i })))('wraps seed/lower with unchanged wire bytes for editor fixture $i', ({ document }) => {
    const legacy = seed(document), state = adapter.seed(document, undefined);
    expect(JSON.stringify(adapter.lower(state))).toBe(JSON.stringify(lower(legacy.sketch, legacy.meta)));
    expect(adapter.axes).toEqual({ x: { label: 'x', unit: 'mm' }, y: { label: 'y', unit: 'mm' } });
  });

  it('restores authored dimensions, preserves pending drafts, and omits live context from serialisation', () => {
    const state = adapter.seed(INITIAL_SOURCE, serializeSourceAuthoring(dimension));
    expect(state.sketch.constraints).toContainEqual(dimension.sketch.constraints.at(-1));
    const pending = adapter.apply(state, { kind: 'deleteEntity', id: 'piston' });
    if ('error' in pending) throw new Error(pending.error);
    expect(adapter.status(pending).kind).toBe('unfinished');
    const serialized = adapter.serializeAuthoring(pending) as Record<string, unknown>;
    expect(serialized.pending).toBe(true);
    expect(adapter.seed(INITIAL_SOURCE, { ...serialized, document: INITIAL_SOURCE }).sketch).toEqual(pending.sketch);
    const withContext = withSourceContext(state, context);
    expect((adapter.serializeAuthoring(withContext) as { meta: SourceMeta }).meta.clearances).toBeUndefined();
    expect(adapter.seed(INITIAL_SOURCE, { version: 'future' })).toEqual(seed(INITIAL_SOURCE));
  });

  it('distinguishes topology drafts, invalid geometry and valid contours', () => {
    expect(adapter.status(seed(INITIAL_SOURCE))).toEqual({ kind: 'valid' });
    const state = seed(INITIAL_SOURCE);
    state.meta.weights.piston = Number.NaN;
    expect(adapter.status(state).kind).toBe('invalid');
  });

  it('keeps nearby/Fit all context, finite arc extrema, limits and named-dimension fit padding', () => {
    const state = fixtures[1].state;
    const model = adapter.display(state, { selection: [], scale: 1, context });
    expect(model.bounds(false).minY).toBeGreaterThan(model.bounds(true).minY);
    const arc = seed(editorFixtures()[5]);
    expect(sourceBounds(arc.sketch)).toEqual({ minX: 0, maxX: 8, minY: -4, maxY: 1 });
    const modelWithDimension = adapter.display(dimension, { selection: [], scale: 1 });
    expect(modelWithDimension.fitPadding).toEqual({ bottom: 24, top: 0, right: 170, offsetX: 48, margin: 32 });
    const fitted = fit(modelWithDimension.bounds(false), { width: 470, height: 336 }, 32);
    expect(sourceInitialView(dimension.sketch)).toEqual({ ...fitted, offsetX: fitted.offsetX + 48 });
    const bounded = withSourceLimit(seed(INITIAL_SOURCE), { kind: 'aperture', radius: 20 });
    expect(adapter.display(bounded, { selection: [], scale: 1 }).bounds(false).maxX).toBe(20);
  });

  it('marquees and drags selectable geometry and excludes fixed references', () => {
    const state = seed(INITIAL_SOURCE);
    expect(adapter.marquee(state, { minX: -1, maxX: 9, minY: -1, maxY: 1 }, false)).toEqual(['pole', 'rim', 'piston']);
    expect(adapter.marquee(state, { minX: 3, maxX: 5, minY: -1, maxY: 1 }, true)).toEqual(['piston']);
    expect(adapter.dragSet(state, [], state.meta.references[0].id)).toBeUndefined();
    const drag = adapter.dragSet(state, ['piston'], 'piston')!;
    expect(drag.ids).toEqual(['pole', 'rim']);
    expect(drag.move(state, { x: 2, y: 3 }, { x: 8, y: 0 })).toEqual([
      { kind: 'movePoint', id: 'pole', x: 2, y: 3 }, { kind: 'movePoint', id: 'rim', x: 10, y: 3 },
    ]);
    const commands = adapter.remove(dimension, ['radius', 'piston', 'rim']);
    expect(commands).toEqual([{ kind: 'deleteEntity', id: 'piston' }]);
  });

  it('exposes the existing selection property actions and drawing reducers', () => {
    const command = vi.fn().mockResolvedValue(dimension);
    const session = { command, busy: false } as unknown as WorkspaceSession<Authoring<SourceMeta>, Command>;
    const properties = adapter.properties(dimension, ['radius'], session);
    expect(renderToStaticMarkup(<>{properties[0].render()}</>)).toContain('Make reference');
    expect(adapter.properties(dimension, [], session)).toEqual([]);
    const line = adapter.tools.find(tool => tool.id === 'line')!;
    const start = line.pointer({ phase: 'down', at: { x: 0, y: 0 }, tolerance: 1 }, dimension, undefined);
    const end = line.pointer({ phase: 'down', at: { x: 3, y: 4 }, tolerance: 1 }, dimension, start.local);
    expect(end.commands).toEqual([{ kind: 'addLine', from: { x: 0, y: 0, snap: null }, to: { x: 3, y: 4, snap: null } }]);
    expect(line.key?.(new KeyboardEvent('keydown', { key: 'Escape' }), dimension, end.local).consumed).toBe(true);
    expect(adapter.tools.some(tool => tool.id === 'split')).toBe(true);
    expect(adapter.tools.some(tool => tool.group === 'Constraints')).toBe(true);
  });

  it('binds the real solver for diagnosis, drag and dimension edits', async () => {
    const state = structuredClone(dimension), handle = await adapter.solver!.open(state);
    try {
      expect((await handle.diagnose(state)).diagnosis.status).toBe('ok');
      const resized = await handle.dimension!(state, 'radius', 9);
      expect(resized.sketch.points.find(p => p.id === 'rim')?.x).toBeCloseTo(9);
      await handle.beginDrag(['rim']);
      expect((await handle.dragTo([{ pointId: 'rim', x: 10, y: 0 }])).sketch).toBeDefined();
      expect((await handle.endDrag()).status).toBe('ok');
      await expect(handle.constraint!(resized, { kind: 'distanceX', a: 'pole', b: 'rim', value: 5, driving: true }, { reference: false })).rejects.toThrow();
    } finally { handle.destroy(); }
  });
});

it('keeps line chaining through release and projects split clicks to the curve', () => {
  const state = seed(INITIAL_SOURCE), line = adapter.tools.find(tool => tool.id === 'line')!;
  const down = (x: number, y: number) => ({ phase: 'down' as const, at: { x, y }, tolerance: 1 });
  const first = line.pointer(down(10, 0), state, undefined);
  const second = line.pointer(down(12, 2), state, first.local);
  const released = line.pointer({ ...down(12, 2), phase: 'up' }, state, second.local);
  const next = adapter.apply(state, second.commands![0]);
  if ('error' in next) throw new Error(next.error);
  const third = line.pointer(down(14, 0), next, released.local);
  expect(third.commands?.[0]).toMatchObject({ kind: 'addLine', from: next.sketch.entities.at(-1)?.kind === 'line' ? (next.sketch.entities.at(-1) as { p2: string }).p2 : '' });
  const split = adapter.tools.find(tool => tool.id === 'split')!;
  const result = split.pointer(down(4, .5), state, undefined);
  expect(result.commands).toEqual([{ kind: 'splitAt', entity: 'piston', x: 4, y: 0 }]);
  expect(adapter.apply(state, result.commands![0])).not.toHaveProperty('error');
});

it('keeps the dimension second pick and commits placement only on release', () => {
  const state = seed(INITIAL_SOURCE), tool = adapter.tools.find(tool => tool.id === 'dimension')!;
  const down = { phase: 'down' as const, at: { x: 4, y: 2 }, tolerance: 1 };
  const first = tool.pointer({ ...down, hit: 'piston' }, state, undefined);
  const second = tool.pointer({ ...down, hit: state.meta.references[0].id }, state, first.local);
  expect(second.commands).toBeUndefined();
  expect(second.local).toEqual({ ids: ['piston', state.meta.references[0].id] });
  const placed = tool.pointer(down, state, second.local);
  expect(placed.commands).toBeUndefined();
  const up = tool.pointer({ ...down, phase: 'up', reference: true }, state, placed.local);
  expect(up.commands?.[0]).toMatchObject({ kind: 'addConstraint', constraint: { driving: false } });
});
