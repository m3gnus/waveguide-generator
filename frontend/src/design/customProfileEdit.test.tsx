import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily, mergeDesignForFamily, resetDesignStore, serializeDesign, useDesignStore, type DesignDocument } from '../stores/design';
import {
  applyPastedProfile, commitProfileEdit, currentEditOrigin, deleteAnchor, horizontalThroatForVertical, insertAnchorBuilder, moveMouth,
  POSITION_ROUND_TRIP_TOLERANCE, ProfileEditRefusal, separateVerticalBlocker, setAnchorAngle, setAnchorJoin, setAnchorValue,
  setSeparateVertical, stretchLength, useProfileEdit, withInsertedProfile, isUnseparatedVertical, rememberUnseparatedVertical,
} from './customProfileEdit';
import type { CurvesResponse } from '../api/freeformProfile';
import { hydrateDesignDocument } from '../api/designIo';

function custom(): DesignDocument {
  const design = designForFamily('FREEFORM');
  design.profile_h!.points = [
    { t: 0, r: 12.7 },
    { t: .25, r: 30, join: 'corner', angle_deg: 20, handle_in: [-4, -1], handle_out: [6, 3] },
    { t: .5, r: 60, handle_in: [-5, -2], handle_out: [10, 4] },
    { t: .75, r: 90 },
    { t: 1, r: 140 },
  ];
  return design;
}

const angleOf = ([dz, dr]: [number, number], sign = 1) => Math.atan2(sign * dr, sign * dz) * 180 / Math.PI;

describe('Custom profile edit transformations', () => {
  it('deletes an interior anchor, releases only the facing neighbour handles and re-keys expressions', () => {
    const design = custom();
    design._expressions = { 'profile_h.points.3.r': { value: 90, raw: '45 * 2' }, 'profile_h.points.$last.r': { value: 140, raw: '70*2' }, 'profile_v.points.1.r': { value: 1, raw: 'x' } };
    design._absent = ['profile_h.points.2.angle_deg'];
    const { after, notes } = deleteAnchor(design, 'H', 2);
    const points = after.profile_h!.points;
    expect(points.map((point) => point.t)).toEqual([0, .25, .75, 1]);
    expect(points[1]).not.toHaveProperty('handle_out');
    expect(points[1].handle_in).toEqual([-4, -1]);
    expect(points[2]).not.toHaveProperty('handle_in');
    expect(after._expressions).toEqual({ 'profile_h.points.2.r': { value: 90, raw: '45 * 2' }, 'profile_h.points.3.r': { value: 140, raw: '70*2' }, 'profile_v.points.1.r': { value: 1, raw: 'x' } });
    expect(after._absent).toBeUndefined();
    expect(notes.join(' ')).toMatch(/Released the facing handles .*H point 1 \(out\)/);
    expect(notes.join(' ')).toMatch(/remain pinned in H: H point 1 \(in\)/);
    expect(after.profile_v).toEqual(design.profile_v);
    expect(() => deleteAnchor(design, 'H', 0)).toThrow(ProfileEditRefusal);
  });

  it('rotates pinned handles to a new angle, keeping their lengths', () => {
    const design = custom();
    const smooth = setAnchorAngle(design, 'H', 2, 'in', 30).after.profile_h!.points[2];
    expect(smooth.angle_deg).toBe(30);
    expect(angleOf(smooth.handle_out!)).toBeCloseTo(30, 12);
    expect(angleOf(smooth.handle_in!, -1)).toBeCloseTo(30, 12);
    expect(Math.hypot(...smooth.handle_out!)).toBeCloseTo(Math.hypot(10, 4), 12);
    expect(Math.hypot(...smooth.handle_in!)).toBeCloseTo(Math.hypot(5, 2), 12);
    // A corner's outgoing angle rotates only the outgoing handle.
    const corner = setAnchorAngle(design, 'H', 1, 'out', 40).after.profile_h!.points[1];
    expect(corner).toMatchObject({ angle_deg: 20, angle_out_deg: 40, handle_in: [-4, -1] });
    expect(angleOf(corner.handle_out!)).toBeCloseTo(40, 12);
    // Clearing keeps a pin and says so.
    const cleared = setAnchorAngle(design, 'H', 1, 'in', undefined);
    expect(cleared.after.profile_h!.points[1]).not.toHaveProperty('angle_deg');
    expect(cleared.after.profile_h!.points[1].handle_out).toEqual([6, 3]);
    expect(cleared.notes[0]).toMatch(/stay pinned/);
    expect(() => setAnchorAngle(design, 'H', 2, 'in', 90)).toThrow('less than 90');
    expect(() => setAnchorAngle(design, 'H', 0, 'in', 10)).toThrow('no incoming side');
  });

  it('makes a pinned corner smooth on the averaged direction and refuses opposed or unknown directions', () => {
    const design = custom();
    design.profile_h!.points[1] = { t: .25, r: 30, join: 'corner', handle_in: [-4, 0], handle_out: [0, 6] };
    const { after } = setAnchorJoin(design, 'H', 1, 'smooth');
    const point = after.profile_h!.points[1];
    expect(point.join).toBe('smooth');
    expect(angleOf(point.handle_out!)).toBeCloseTo(45, 12);
    expect(angleOf(point.handle_in!, -1)).toBeCloseTo(45, 12);
    expect(Math.hypot(...point.handle_in!)).toBeCloseTo(4, 12);
    expect(Math.hypot(...point.handle_out!)).toBeCloseTo(6, 12);
    // An unpinned side takes the resolved server direction.
    design.profile_h!.points[1] = { t: .25, r: 30, join: 'corner', angle_out_deg: 40 };
    expect(() => setAnchorJoin(design, 'H', 1, 'smooth')).toThrow('not available');
    const curves = { H: { anchors: [{}, { angleInDeg: 20, angleOutDeg: 40 }] } } as unknown as CurvesResponse;
    const averaged = setAnchorJoin(design, 'H', 1, 'smooth', curves).after.profile_h!.points[1];
    expect(averaged.angle_deg).toBeCloseTo(30, 12);
    expect(averaged).not.toHaveProperty('angle_out_deg');
    design.profile_h!.points[1] = { t: .25, r: 30, join: 'corner', handle_in: [-1, 0], handle_out: [-1, 0] };
    expect(() => setAnchorJoin(design, 'H', 1, 'smooth')).toThrow('opposed');
    // Nothing pinned: the anchor simply takes an automatic tangent.
    design.profile_h!.points[1] = { t: .25, r: 30, join: 'corner' };
    expect(setAnchorJoin(design, 'H', 1, 'smooth').after.profile_h!.points[1]).toEqual({ t: .25, r: 30, join: 'smooth' });
  });

  it('keeps one circular throat in separate mode but leaves an inactive V alone', () => {
    const separate = custom();
    expect(setAnchorValue(separate, 'H', 0, 'r', 15).after.profile_v!.points[0].r).toBe(15);
    const shared = { ...custom(), shared_profile: true };
    const result = setAnchorValue(shared, 'H', 0, 'r', 15).after;
    expect(result.profile_h!.points[0].r).toBe(15);
    expect(result.profile_v!.points[0].r).toBe(12.7);
    expect(separateVerticalBlocker(result)).toMatch(/Use H throat for V/);
    expect(() => setSeparateVertical(result, true)).toThrow('Use H throat for V');
    const reconciled = horizontalThroatForVertical(result).after;
    expect(reconciled.profile_v!.points[0].r).toBe(15);
    expect(setSeparateVertical(reconciled, true).after.shared_profile).toBe(false);
    expect(() => setAnchorValue(separate, 'H', 2, 'z', 30)).toThrow('between its neighbours');
    expect(() => setAnchorValue(separate, 'H', 0, 'z', 1)).toThrow('Change Length');
  });

  it('stretches as an exact affine axial transform of both profiles', () => {
    const design = { ...custom(), shared_profile: true };
    design.profile_v!.points.splice(1, 0, { t: .4, r: 50, angle_deg: 30 });
    // Resolved handles of the active plane; H point 3 and the endpoints are automatic.
    const resolved = (index: number) => ({ handleIn: index ? [-3, -1] : null, handleOut: index < 4 ? [3, 1] : null });
    const curves = { H: { anchors: design.profile_h!.points.map((_point, index) => resolved(index)) }, V: { anchors: [] } } as unknown as CurvesResponse;
    const { after, notes } = stretchLength(design, 240, curves);
    expect(after.length).toBe(240);
    expect(after.profile_h!.points.map((point) => point.t)).toEqual(design.profile_h!.points.map((point) => point.t));
    expect(after.profile_h!.points[2].handle_out).toEqual([20, 4]);
    // Automatic handles are pinned to the resolved curve, scaled, so the stretch stays exact.
    expect(after.profile_h!.points[3]).toMatchObject({ handle_in: [-6, -1], handle_out: [6, 1] });
    expect(after.profile_h!.points[0].handle_out).toEqual([6, 1]);
    expect(after.profile_h!.points[4].handle_in).toEqual([-6, -1]);
    expect(notes.join(' ')).toContain('no longer refit automatically: H throat (out), H point 3 (in), H point 3 (out), H mouth (in)');
    // The inactive V has no resolved curve of its own: authored values only.
    expect(after.profile_v!.points[1]).not.toHaveProperty('handle_in');
    expect(after.profile_h!.points[1].angle_deg).toBeCloseTo(Math.atan(Math.tan(20 * Math.PI / 180) / 2) * 180 / Math.PI, 12);
    expect(after.profile_h!.throat_angle_deg).toBeCloseTo(Math.atan(Math.tan(15.5 * Math.PI / 180) / 2) * 180 / Math.PI, 12);
    // The retained V is transformed too.
    expect(after.profile_v!.points[1].angle_deg).toBeCloseTo(Math.atan(Math.tan(30 * Math.PI / 180) / 2) * 180 / Math.PI, 12);
    expect(after.cross_sections).toEqual(design.cross_sections);
    design._expressions = { 'profile_h.points.1.angle_deg': { value: 20, raw: '10*2' } };
    expect(() => stretchLength(design, 240, curves)).toThrow('expression "10*2"');
    expect(() => stretchLength({ ...design, _expressions: undefined }, 240, { ...curves, H: { anchors: [] } } as unknown as CurvesResponse)).toThrow('out of date');
  });

  it('moves only the mouth, keeping interior mm within 1e-12, and refuses a crossing', () => {
    const design = custom();
    design.cross_sections = [{ t: 0, shape: 'ellipse' }, { t: .3, shape: 'ellipse' }, { t: 1, shape: 'ellipse' }];
    const anchors = (points: unknown[]) => points.map(() => ({ handleIn: [-2, -1], handleOut: [2, 1] }));
    const curves = { H: { anchors: anchors(design.profile_h!.points) }, V: { anchors: anchors(design.profile_v!.points) } } as unknown as CurvesResponse;
    const { after, notes } = moveMouth(design, 150, curves);
    after.profile_h!.points.slice(1, -1).forEach((point, index) => {
      expect(Math.abs(point.t * 150 - design.profile_h!.points[index + 1].t * 120)).toBeLessThan(POSITION_ROUND_TRIP_TOLERANCE);
    });
    expect(after.profile_h!.points.at(-1)!.t).toBe(1);
    expect(after.profile_h!.points[2].handle_out).toEqual([10, 4]);
    // Spans before the last keep their resolved handles; the last span stays free.
    expect(after.profile_h!.points[0].handle_out).toEqual([2, 1]);
    expect(after.profile_h!.points[3]).toEqual({ t: after.profile_h!.points[3].t, r: 90, handle_in: [-2, -1] });
    expect(after.profile_h!.points[4]).toEqual({ t: 1, r: 140 });
    expect(notes[1]).toContain('H throat (out), H point 3 (in)');
    expect(Math.abs(after.cross_sections![1].t * 150 - 36)).toBeLessThan(POSITION_ROUND_TRIP_TOLERANCE);
    expect(() => moveMouth(design, 90, curves)).toThrow('at or beyond the new mouth');
    expect(() => moveMouth(design, 120, curves)).toThrow('unchanged');
  });

  it('keeps the inserted plane, re-keys its expressions and leaves V, stations and other metadata untouched', () => {
    const design = custom();
    design._expressions = { 'profile_h.points.2.r': { value: 60, raw: '30*2' }, scale: { value: 1, raw: '1' } };
    const inserted = structuredClone(design);
    inserted.profile_h!.points.splice(2, 0, { t: .4, r: 45, join: 'smooth', handle_in: [-1, -1], handle_out: [1, 1], angle_deg: null as unknown as number });
    inserted.profile_v!.points[1] = { t: .5, r: 99 };
    const after = withInsertedProfile(design, inserted, 'H');
    expect(after.profile_h!.points[2]).toEqual({ t: .4, r: 45, join: 'smooth', handle_in: [-1, -1], handle_out: [1, 1] });
    expect(after.profile_v).toEqual(design.profile_v);
    expect(after._expressions).toEqual({ 'profile_h.points.3.r': { value: 60, raw: '30*2' }, scale: { value: 1, raw: '1' } });
  });

  it('pastes lookup-style rows as corners, reconciles one throat and stretches or adopts length', () => {
    const design = custom();
    const pasted = { points: [{ t: 0, r: 10 }, { t: .5, r: 50 }, { t: 1, r: 150 }], importedLength: 200 };
    const kept = applyPastedProfile(design, pasted, { target: 'H', adoptLength: false, join: 'corner' }).after;
    expect(kept.length).toBe(120);
    expect(kept.profile_h!.points.every((point) => point.join === 'corner')).toBe(true);
    expect(kept.profile_v!.points[0].r).toBe(10);
    expect(() => applyPastedProfile(design, pasted, { target: 'H', adoptLength: true, join: 'smooth' })).toThrow('resolved curve');
    const unresolved = { anchors: design.profile_h!.points.map(() => ({ handleIn: null, handleOut: null })) };
    const adopted = applyPastedProfile(design, pasted, { target: 'H', adoptLength: true, join: 'smooth' }, { H: unresolved, V: { anchors: design.profile_v!.points.map(() => ({ handleIn: null, handleOut: null })) } } as unknown as CurvesResponse).after;
    expect(adopted.length).toBe(200);
    expect(adopted.profile_h!.points.some((point) => point.join)).toBe(false);
    // The untouched V is stretched explicitly with the adopted length, not rescaled silently.
    const stretch = (angle: number) => Math.atan2(Math.sin(angle * Math.PI / 180), (200 / 120) * Math.cos(angle * Math.PI / 180)) * 180 / Math.PI;
    expect(adopted.profile_v!.throat_angle_deg).toBeCloseTo(stretch(15.5), 12);
    expect(adopted.profile_v!.mouth_angle_deg).toBeCloseTo(stretch(60), 12);
    const tooMany = { points: Array.from({ length: 65 }, (_value, index) => ({ t: index / 64, r: 10 + index })), importedLength: 120 };
    expect(() => applyPastedProfile(design, tooMany, { target: 'H', adoptLength: false, join: 'corner' })).toThrow('at most 64');
  });

  it('remembers only this document\'s untouched V seed', () => {
    resetDesignStore();
    act(() => useDesignStore.getState().setFamily('FREEFORM'));
    const design = useDesignStore.getState().design;
    expect(isUnseparatedVertical(design)).toBe(false);
    rememberUnseparatedVertical(design);
    expect(isUnseparatedVertical(design)).toBe(true);
    expect(isUnseparatedVertical({ ...design, profile_v: { ...design.profile_v!, mouth_angle_deg: 50 } })).toBe(false);
    act(() => useDesignStore.getState().replaceDocument(design));
    expect(isUnseparatedVertical(useDesignStore.getState().design)).toBe(false);
  });

  it('seeds a new shared design with an inactive V equal to H and never infers shared mode', () => {
    const fresh = mergeDesignForFamily(designForFamily('R-OSSE'), 'FREEFORM');
    expect(fresh.shared_profile).toBe(true);
    expect(fresh.profile_v).toEqual(fresh.profile_h);
    expect(designForFamily('FREEFORM')).not.toHaveProperty('shared_profile');
    const separated = setSeparateVertical(fresh, true);
    expect(separated.after.shared_profile).toBe(false);
    expect(separated.notes[0]).toMatch(/does not change/);
    const off = setSeparateVertical(separated.after, false).after;
    expect(off.shared_profile).toBe(true);
    expect(off.profile_v).toEqual(fresh.profile_v);
    expect(mergeDesignForFamily(fresh, 'OSSE')).not.toHaveProperty('shared_profile');
    // Seeding replaces V with H, so a diverged throat does not block it; a restore does.
    const diverged = setAnchorValue(fresh, 'H', 0, 'r', 20).after;
    expect(() => setSeparateVertical(diverged, true)).toThrow('Use H throat for V');
    expect(setSeparateVertical(diverged, true, true).after.profile_v).toEqual(diverged.profile_h);
  });
});

describe('Custom profile commit and preview', () => {
  beforeEach(() => resetDesignStore());

  function load(design: DesignDocument) {
    act(() => useDesignStore.getState().replaceDocument(design));
  }

  it('commits one Undo step, restores re-keyed expressions, and Undo/Redo round-trip exactly', () => {
    const design = custom();
    design._expressions = { 'profile_h.points.3.r': { value: 90, raw: '45 * 2' } };
    load(design);
    const origin = currentEditOrigin();
    const before = useDesignStore.getState().design;
    const { after } = deleteAnchor(before, 'H', 2);
    commitProfileEdit(origin, before, after);
    const committed = useDesignStore.getState().design;
    expect(serializeDesign(committed)).toEqual(serializeDesign(after));
    expect(committed._expressions).toEqual({ 'profile_h.points.2.r': { value: 90, raw: '45 * 2' } });
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    act(() => useDesignStore.getState().undo());
    expect(serializeDesign(useDesignStore.getState().design)).toEqual(serializeDesign(design));
    act(() => useDesignStore.getState().redo());
    expect(serializeDesign(useDesignStore.getState().design)).toEqual(serializeDesign(after));
    // A stale origin never writes.
    expect(() => commitProfileEdit(origin, before, after)).toThrow('changed');
  });

  describe('useProfileEdit', () => {
    let host: HTMLDivElement; let root: Root;
    let edit: ReturnType<typeof useProfileEdit>;
    function Probe({ fetcher }: { fetcher: typeof fetch }) { edit = useProfileEdit({ fetcher }); return null; }
    beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
    afterEach(() => { act(() => root.unmount()); host.remove(); });

    const csv = '0;1.27;0\n0;14;12\n\n0;0;1.27\n0;0;12';
    function routes(overrides: Record<string, (body: Record<string, unknown>) => Response> = {}) {
      return vi.fn<typeof fetch>(async (url, init) => {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        const path = String(url);
        for (const [route, handler] of Object.entries(overrides)) if (path.endsWith(route)) return handler(body);
        if (path.endsWith('/curves')) return new Response(JSON.stringify({ content_key: body.content_key, H: { anchors: [], spans: [], samples: [], inflections: [] }, V: { anchors: [], spans: [], samples: [], inflections: [] }, warnings: [] }));
        if (path.endsWith('/compare')) return new Response(JSON.stringify({ content_key: body.content_key, H: { curve_deviation_mm: 1 }, V: { curve_deviation_mm: 0 } }));
        if (path.startsWith('/api/export/profiles')) return new Response(csv);
        throw new Error(`unexpected ${path}`);
      });
    }

    it('previews, cancels without history and accepts as one Undo step', async () => {
      load(custom());
      const fetcher = routes();
      act(() => root.render(<Probe fetcher={fetcher} />));
      await act(async () => { edit.propose('Delete', (design) => deleteAnchor(design, 'H', 2)); });
      await vi.waitFor(() => expect(edit.preview?.generated.status).toBe('ready'));
      expect(edit.preview?.validation.status).toBe('ready');
      expect(edit.preview?.curve.status).toBe('ready');
      act(() => edit.cancel());
      expect(edit.preview).toBeNull();
      expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
      await act(async () => { edit.propose('Delete', (design) => deleteAnchor(design, 'H', 2)); });
      await vi.waitFor(() => expect(edit.preview?.generated.status).toBe('ready'));
      act(() => edit.accept());
      expect(useDesignStore.getState().design.profile_h!.points).toHaveLength(4);
      expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
      expect(edit.notice).toMatch(/Undo restores/);
    });

    it('keeps the old design when the candidate is refused and shows the server reason', async () => {
      load(custom());
      const fetcher = routes({ '/curves': (body) => new Response(JSON.stringify({ content_key: body.content_key, detail: 'FREEFORM profileH span 2 is infeasible' }), { status: 422 }) });
      act(() => root.render(<Probe fetcher={fetcher} />));
      await act(async () => { edit.propose('Angle', (design) => setAnchorAngle(design, 'H', 2, 'in', 80)); });
      await vi.waitFor(() => expect(edit.preview?.validation).toEqual({ status: 'error', message: 'FREEFORM profileH span 2 is infeasible' }));
      act(() => edit.accept());
      expect(useDesignStore.getState().design.profile_h!.points[2].angle_deg).toBeUndefined();
      expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
    });

    it('drops a late insertion after edit, Undo and Open (ABA), never overwriting the newer document', async () => {
      load(custom());
      let release!: (response: Response) => void;
      const fetcher = routes({ '/insert': () => { throw new Error('replaced below'); } });
      fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { release = resolve; }));
      act(() => root.render(<Probe fetcher={fetcher} />));
      await act(async () => { edit.propose('Insert', insertAnchorBuilder('H', 40, 'exact', fetcher)); });
      const requestKey = JSON.parse(String(fetcher.mock.calls[0][1]?.body)).content_key;
      act(() => useDesignStore.getState().updateValue('scale', 2));
      act(() => useDesignStore.getState().undo());
      const reopened = custom();
      load(reopened);
      await act(async () => {
        release(new Response(JSON.stringify({ content_key: requestKey, design: serializeDesign(custom()), deviation: {} })));
      });
      expect(edit.preview).toBeNull();
      expect(edit.notice).toMatch(/discarded/);
      expect(serializeDesign(useDesignStore.getState().design)).toEqual(serializeDesign(reopened));
    });
  });
});

describe('opened documents keep every value an edit writes', () => {
  // A saved file opened through the server: every optional point field is wire null.
  function opened(): DesignDocument {
    const point = (t: number, r: number) => ({ t, r, angle_deg: null, angle_out_deg: null, join: null, handle_in: null, handle_out: null });
    const profile = () => ({ points: [point(0, 12.7), point(.5, 60), point(1, 140)], throat_angle_deg: 15.5, mouth_angle_deg: 60 });
    return hydrateDesignDocument({
      formula: 'FREEFORM', length: 120, profile_h: profile(), profile_v: profile(),
      cross_sections: [{ t: 0, shape: 'ellipse' }, { t: 1, shape: 'ellipse' }],
    });
  }
  const resolved = (design: DesignDocument) => {
    const anchors = (points: unknown[]) => points.map((_point, index) => ({ handleIn: index ? [-5, -2] : null, handleOut: index < points.length - 1 ? [10, 4] : null }));
    return { H: { anchors: anchors(design.profile_h!.points) }, V: { anchors: anchors(design.profile_v!.points) } } as unknown as CurvesResponse;
  };
  /** Save as the wire carries it, reopen, and return the reopened wire. */
  const reopen = (design: DesignDocument) => serializeDesign(hydrateDesignDocument(JSON.parse(JSON.stringify(serializeDesign(design)))));
  const wirePoints = (wire: Record<string, unknown>, plane: 'profile_h' | 'profile_v') => (wire[plane] as { points: Record<string, unknown>[] }).points;

  it('has the omissions this test relies on', () => {
    expect(opened()._absent).toContain('profile_h.points.0.handle_out');
    expect(wirePoints(serializeDesign(opened()), 'profile_h')[0].handle_out).toBeNull();
  });

  it('serializes the handles Stretch pins, in the preview and after save/reopen', () => {
    const design = opened();
    const { after } = stretchLength(design, 240, resolved(design));
    expect(after.profile_h!.points[0].handle_out).toEqual([20, 4]);
    for (const wire of [serializeDesign(after), reopen(after)]) {
      expect(wirePoints(wire, 'profile_h')[0].handle_out).toEqual([20, 4]);
      expect(wirePoints(wire, 'profile_h')[1]).toMatchObject({ handle_in: [-10, -2], handle_out: [20, 4] });
      expect(wirePoints(wire, 'profile_h')[2].handle_in).toEqual([-10, -2]);
      // Untouched omissions stay null.
      expect(wirePoints(wire, 'profile_h')[1].angle_deg).toBeNull();
    }
  });

  it('serializes the handles Move mouth pins, in the preview and after save/reopen', () => {
    const design = opened();
    const { after } = moveMouth(design, 150, resolved(design));
    for (const wire of [serializeDesign(after), reopen(after)]) {
      expect(wirePoints(wire, 'profile_h')[0].handle_out).toEqual([10, 4]);
      expect(wirePoints(wire, 'profile_h')[1].handle_in).toEqual([-5, -2]);
      // The last span stays free.
      expect(wirePoints(wire, 'profile_h')[1].handle_out).toBeNull();
      expect(wirePoints(wire, 'profile_h')[2].handle_in).toBeNull();
    }
  });

  it.each(['exact', 'refit'] as const)('serializes a %s insertion with its pinned neighbours and keeps other omissions', (mode) => {
    const design = opened();
    const inserted = structuredClone(design);
    const pins = mode === 'exact';
    inserted.profile_h!.points = [
      { t: 0, r: 12.7, ...(pins ? { handle_out: [3, 1] as [number, number] } : {}) },
      { t: .25, r: 35, join: 'smooth', ...(pins ? { handle_in: [-3, -1] as [number, number], handle_out: [3, 1] as [number, number] } : {}) },
      { t: .5, r: 60, ...(pins ? { handle_in: [-3, -1] as [number, number] } : {}) },
      { t: 1, r: 140 },
    ];
    const after = withInsertedProfile(design, inserted, 'H');
    for (const wire of [serializeDesign(after), reopen(after)]) {
      const points = wirePoints(wire, 'profile_h');
      expect(points).toHaveLength(4);
      expect(points[0].handle_out).toEqual(pins ? [3, 1] : null);
      expect(points[1].join).toBe('smooth');
      // A new point has no recorded omissions: unpinned handles are simply absent.
      expect(points[1].handle_in ?? null).toEqual(pins ? [-3, -1] : null);
      expect(points[1].handle_out ?? null).toEqual(pins ? [3, 1] : null);
      expect(points[2].handle_in).toEqual(pins ? [-3, -1] : null);
      // The shifted mouth keeps its own omissions; V is untouched.
      expect(points[3].angle_deg).toBeNull();
      expect(wire.profile_v).toEqual(serializeDesign(design).profile_v);
    }
  });

  it('serializes angles, joins and pins written by table edits on an opened file', () => {
    const design = opened();
    const angled = setAnchorAngle(design, 'H', 1, 'in', 25).after;
    expect(wirePoints(reopen(angled), 'profile_h')[1].angle_deg).toBe(25);
    const corner = setAnchorJoin(angled, 'H', 1, 'corner').after;
    expect(wirePoints(reopen(corner), 'profile_h')[1].join).toBe('corner');
    const out = setAnchorAngle(corner, 'H', 1, 'out', 40).after;
    expect(wirePoints(reopen(out), 'profile_h')[1]).toMatchObject({ angle_deg: 25, angle_out_deg: 40, join: 'corner' });
    const pasted = applyPastedProfile(design, { points: [{ t: 0, r: 12.7 }, { t: .5, r: 40 }, { t: 1, r: 140 }], importedLength: 120 }, { target: 'H', adoptLength: false, join: 'corner' }).after;
    expect(wirePoints(reopen(pasted), 'profile_h').map((point) => point.join)).toEqual(['corner', 'corner', 'corner']);
  });
});
