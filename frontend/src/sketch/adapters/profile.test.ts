import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixtures from '../../../../shared/profile-inspection/mesher-profiles.json';
import { profileSamples } from '../../api/profileSamples';
import { queryFreeformCurves, type CurvesResponse } from '../../api/freeformProfile';
import { designForFamily, resetDesignStore, useDesignStore, type DesignDocument } from '../../stores/design';
import * as owner from '../../design/customProfileEdit';
import { PlainController } from '../workspace/plainController';
import { ProfileController } from '../profileController';
import { ProjectHistoryAuthority } from '../workspace/projectHistoryAuthority';
import { displayView } from '../workspace/view';
import { worldToScreen } from '../view';
import { profileServer, sampledCurve } from '../profileCanvasTestSupport';
import { anchorId, createProfileAdapter, equalProfiles, profileBinding, profileCommandBuilder, type ProfilePresentation } from './profile';

let design: DesignDocument, curves: CurvesResponse, presentation: ProfilePresentation;
beforeEach(async () => {
  resetDesignStore(); design = designForFamily('FREEFORM'); design.shared_profile = false;
  design.profile_h!.points.splice(1, 0, { t: 1 / 3, r: 30, join: 'corner' });
  useDesignStore.getState().replaceDocument(design); useDesignStore.temporal.getState().clear();
  curves = await queryFreeformCurves(design, 'fixture', undefined, profileServer());
  presentation = { curves };
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const adapter = () => createProfileAdapter(() => presentation);

describe('profile adapter', () => {
  it('draws precisely the mesher response samples, within 0.01 mm, including the steep span', () => {
    const display = adapter().display(design, { selection: [], scale: 1 });
    const curve = display.items.find(item => item.id === 'H:curve');
    expect(curve?.kind).toBe('polyline');
    if (curve?.kind !== 'polyline') throw Error('Missing curve');
    expect(curve.points).toEqual(sampledCurve.map(([x, y]) => ({ x, y })));
    curve.points.forEach((p, i) => expect(Math.hypot(p.x - curves.H.samples[i][0], p.y - curves.H.samples[i][1])).toBeLessThan(.01));
    expect(display.items.find(item => item.id === anchorId('H', 1))).toMatchObject({ kind: 'point', glyph: 'corner', at: { x: 40, y: 30 } });
    expect(display.items.find(item => item.id === anchorId('H', 0))).toMatchObject({ glyph: 'smooth' });
    expect(display.items.find(item => item.id === anchorId('H', 2))).toBeTruthy();
  });

  it.each(fixtures)('ports FV: preserves physical generated $name coordinates and equal-plane collapse', fixture => {
    const samples = profileSamples(fixture.csv);
    for (const axis of ['H', 'V'] as const) samples[axis].forEach((p, i) => {
      expect(p.z).toBeCloseTo(fixture.expected[axis][i].z, 4); expect(p.r).toBeCloseTo(fixture.expected[axis][i].r, 4);
    });
    presentation = { generated: samples, scaled: true, plane: 'both' };
    const traces = adapter().display(design, { selection: [], scale: 1 }).items.filter(item => item.role.includes('profile-generated'));
    expect(traces).toHaveLength(equalProfiles(samples) ? 1 : 2);
    expect(traces[0].label).toBe(equalProfiles(samples) ? 'Generated H = V' : 'Generated H');
    expect(traces[0]).toMatchObject({ points: samples.H.map(({ z: x, r: y }) => ({ x, y })) });
    for (const plane of ['H', 'V'] as const) {
      presentation.plane = plane;
      const single = adapter().display(design, { selection: [], scale: 1 }).items.filter(item => item.role.includes('profile-generated'));
      expect(single).toHaveLength(1); expect(single[0]).toMatchObject({ points: samples[plane].map(({ z: x, r: y }) => ({ x, y })) });
    }
  });

  it('uses equal mm on both screen axes, labels scaled mm and maps both layers to the same units', () => {
    design.scale = 1.7; presentation.generated = profileSamples(fixtures[0].csv);
    const domain = adapter(), display = domain.display(design, { selection: [], scale: 1 });
    const view = displayView(display, { width: 420, height: 320 });
    const origin = worldToScreen(view, { x: 0, y: 0 }), x = worldToScreen(view, { x: 10, y: 0 }), y = worldToScreen(view, { x: 0, y: 10 });
    expect(x.x - origin.x).toBeCloseTo(origin.y - y.y, 12);
    expect(display.items.find(item => item.id === 'H:generated')).toMatchObject({ points: presentation.generated.H.map(p => ({ x: p.z / 1.7, y: p.r / 1.7 })) });
    presentation.scaled = true;
    const scaled = domain.display(design, { selection: [], scale: view.scale, view, viewport: { width: 420, height: 320 } });
    expect(scaled.items.find(item => item.id === 'H:curve')).toMatchObject({ points: sampledCurve.map(([x, y]) => ({ x: x * 1.7, y: y * 1.7 })) });
    expect(scaled.items.find(item => item.id === 'axis-z-label')).toMatchObject({ text: 'z (scaled mm)' });
    expect(scaled.items.findIndex(item => item.id === 'H:generated')).toBeLessThan(scaled.items.findIndex(item => item.id === 'H:curve'));
    presentation.showGenerated = false; expect(domain.display(design, { selection: [], scale: 1 }).items.some(item => item.id.endsWith(':generated'))).toBe(false);
  });

  it('hides retained inactive V, overlays separate V in its own role, and keeps handles display-only', () => {
    design.shared_profile = true;
    expect(adapter().display(design, { selection: [], scale: 1 }).items.some(item => item.role === 'profile-V')).toBe(false);
    design.shared_profile = false; curves.H.anchors[1].handleOut = [5, 3];
    const items = adapter().display(design, { selection: [anchorId('H', 1)], scale: 1 }).items;
    expect(items.find(item => item.id === 'V:curve')?.role).toContain('profile-V');
    expect(items.find(item => item.kind === 'handle')).toMatchObject({ editable: false });
  });

  it('keeps last-valid generated coordinates in their export units while a Scale change is pending', () => {
    const generated = profileSamples(fixtures[0].csv); design.scale = 2;
    presentation = { generated, generatedScale: 1, generatedStale: true };
    expect(adapter().display(design, { selection: [], scale: 1 }).items.find(item => item.id === 'H:generated'))
      .toMatchObject({ points: generated.H.map(({ z: x, r: y }) => ({ x, y })), stale: true });
    presentation.scaled = true;
    expect(adapter().display(design, { selection: [], scale: 1 }).items.find(item => item.id === 'H:generated'))
      .toMatchObject({ points: generated.H.map(({ z: x, r: y }) => ({ x, y })) });
  });

  it.each([{ x: 0, y: 35 }, { x: 120, y: 35 }, { x: 40, y: 0 }, { x: 40, y: -2 }])('refuses an invalid drag with the owner reason at $x, $y', at => {
    const result = adapter().apply(design, { kind: 'move', plane: 'H', index: 1, at });
    expect(result).toHaveProperty('error');
    expect((result as { error: string }).error).toMatch(/strictly between|greater than 0/);
    expect(design.profile_h!.points[1]).toMatchObject({ t: 1 / 3, r: 30 });
  });

  it('fixes throat and mouth z, delegates coordinates to setAnchorValue and scales drag input back to design mm', () => {
    const spy = vi.spyOn(owner, 'setAnchorValue');
    const domain = adapter();
    const throat = domain.apply(design, { kind: 'move', plane: 'H', index: 0, at: { x: 17, y: 14 } }) as DesignDocument;
    expect(throat.profile_h!.points[0]).toMatchObject({ t: 0, r: 14 }); expect(throat.profile_v!.points[0].r).toBe(14);
    expect(spy).toHaveBeenCalledWith(design, 'H', 0, 'r', 14);
    design.scale = 2; presentation.scaled = true;
    const commands = domain.dragSet(design, [], anchorId('H', 1))!.move(design, { x: 0, y: 0 }, { x: 100, y: 80 });
    expect(commands).toEqual([{ kind: 'move', plane: 'H', index: 1, at: { x: 50, y: 40 } }]);
  });

  it('delegates delete, join and angle edits to the existing owner and exposes server span labels and refusals', () => {
    const del = vi.spyOn(owner, 'deleteAnchor'), join = vi.spyOn(owner, 'setAnchorJoin'), angle = vi.spyOn(owner, 'setAnchorAngle');
    const domain = adapter();
    domain.apply(design, { kind: 'delete', plane: 'H', index: 1 });
    domain.apply(design, { kind: 'join', plane: 'H', index: 1, join: 'smooth' });
    domain.apply(design, { kind: 'angle', plane: 'H', index: 1, side: 'out', value: 25 });
    expect(del).toHaveBeenCalledWith(design, 'H', 1); expect(join).toHaveBeenCalledWith(design, 'H', 1, 'smooth', curves); expect(angle).toHaveBeenCalledWith(design, 'H', 1, 'out', 25);
    curves.H.spans[0].straight = true; design.profile_h!.points[1].angle_deg = 20;
    presentation.error = 'profileH span 2 from 40 to 120 mm reverses z';
    const display = domain.display(design, { selection: [], scale: 1, view: { scale: 1, offsetX: 0, offsetY: 150 } });
    expect(display.items.find(item => item.id === 'H:span:0')).toMatchObject({ text: 'H · straight' });
    expect(display.items.find(item => item.id === 'H:span:1')).toMatchObject({ text: 'H · curved, angle set' });
    expect(display.items.find(item => item.id === 'H:span-problem:1')).toMatchObject({ state: ['problem'] });
    expect(display.items.find(item => item.id === 'V:span-problem:1')).toBeUndefined();
    expect(domain.status(design).kind).toBe('invalid');
  });

  it.each(['exact', 'refit'] as const)('inserts %s through the owner server route with a profileRequestKey', async mode => {
    const server = profileServer();
    const insert = vi.spyOn(owner, 'insertAnchorBuilder');
    const result = await profileCommandBuilder([{ kind: 'insert', plane: 'H', z: 21.407, mode }], curves)(design, { origin: owner.currentEditOrigin(), signal: new AbortController().signal });
    expect(result.after.profile_h!.points).toHaveLength(4);
    expect(insert).toHaveBeenCalledWith('H', 21.407, mode);
    const body = JSON.parse(String(server.mock.calls.find(([url]) => String(url).endsWith('/insert'))![1]?.body));
    expect(body).toMatchObject({ z_mm: 21.407, plane: 'H', mode, content_key: expect.stringMatching(/^cp:/) });
  });
});

describe('legacy LOOKUP adapter', () => {
  it('uses the exact native polyline with no 64-point cap, no curve query and no join/angle edits', async () => {
    const lookup = designForFamily('LOOKUP');
    lookup.lookup_profile = Array.from({ length: 80 }, (_, i) => [i * 2, 12.7 + i]);
    useDesignStore.getState().replaceDocument(lookup);
    const domain = createProfileAdapter(() => ({}), 'lookup');
    const items = domain.display(lookup, { selection: [], scale: 1 }).items;
    expect(items.find(item => item.id === 'H:curve')).toMatchObject({ points: lookup.lookup_profile.map(([x, y]) => ({ x, y })) });
    expect(items.filter(item => item.kind === 'point')).toHaveLength(80);
    expect(items.filter(item => item.kind === 'point').every(item => item.kind === 'point' && item.glyph === 'corner')).toBe(true);
    expect(domain.tools).toHaveLength(1);
    expect(domain.apply(lookup, { kind: 'join', plane: 'H', index: 1, join: 'smooth' })).toHaveProperty('error');
    const authority = new ProjectHistoryAuthority(profileBinding(vi.fn(), 'lookup'));
    const controller = new ProfileController({ initial: authority.current, authority, adapter: domain, presentation: () => ({}), blocked: () => false, drafting: vi.fn(), propose: vi.fn() });
    const insert = vi.spyOn(owner, 'editLookupAnchor');
    await controller.command({ kind: 'insert', plane: 'H', z: 3, mode: 'exact' }); await controller.enqueue(async () => {});
    expect(insert).toHaveBeenCalled(); expect(controller.current.lookup_profile).toHaveLength(81);
    expect(controller.current.lookup_profile![2]).toEqual([3, 14.2]);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    await controller.undo(); await controller.enqueue(async () => {}); expect(controller.current.lookup_profile).toEqual(lookup.lookup_profile);
    controller.destroy();
  });
});

describe('native profile project bridge', () => {
  it('records one drag step, preserves untouched expressions, and interleaves table edits in the same Undo/Redo history', async () => {
    const store = useDesignStore.getState();
    design._expressions = { 'profile_h.points.2.r': { raw: '70*2', value: 140 } };
    store.replaceDocument(design); useDesignStore.temporal.getState().clear();
    const authority = new ProjectHistoryAuthority(profileBinding(vi.fn()));
    const controller = new PlainController({ adapter: adapter(), authority, initial: authority.current });
    await controller.beginDrag([anchorId('H', 1)]);
    for (let r = 35; r <= 45; r += 1) await controller.dragTo([{ id: anchorId('H', 1), at: { x: 45, y: r } }]);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
    await controller.endDrag(); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    expect(useDesignStore.getState().design._expressions).toEqual(design._expressions);
    const moved = structuredClone(useDesignStore.getState().design);
    const after = owner.setAnchorValue(moved, 'H', 1, 'r', 55).after;
    owner.commitProfileEdit(owner.currentEditOrigin(), moved, after);
    await controller.enqueue(async () => {});
    expect(controller.current.profile_h!.points[1].r).toBe(55); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(2);
    await controller.undo(); await controller.enqueue(async () => {}); expect(controller.current).toEqual(moved);
    await controller.undo(); await controller.enqueue(async () => {}); expect(controller.current.profile_h).toEqual(design.profile_h);
    await controller.undo(true); await controller.enqueue(async () => {}); expect(controller.current).toEqual(moved);
    controller.destroy();
  });

  it('cancels a drag without a step and lets external Undo/Open win', async () => {
    const missing = vi.fn(), authority = new ProjectHistoryAuthority(profileBinding(missing));
    const controller = new PlainController({ adapter: adapter(), authority, initial: authority.current });
    await controller.beginDrag([anchorId('H', 1)]); await controller.dragTo([{ id: anchorId('H', 1), at: { x: 45, y: 45 } }]);
    useDesignStore.getState().undo(); await controller.enqueue(async () => {});
    expect(controller.current.profile_h).toEqual(design.profile_h); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
    useDesignStore.getState().replaceDocument(designForFamily('OSSE')); await controller.enqueue(async () => {}); expect(missing).toHaveBeenCalled();
    controller.destroy();
  });
});
