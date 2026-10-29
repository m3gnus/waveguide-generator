import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily, mergeDesignForFamily, resetDesignStore, serializeDesign, useDesignStore, type DesignDocument } from '../stores/design';
import {
  applyPastedProfile, commitProfileEdit, currentEditOrigin, deleteAnchor, horizontalThroatForVertical, insertAnchorBuilder, moveMouth,
  POSITION_ROUND_TRIP_TOLERANCE, ProfileEditRefusal, separateVerticalBlocker, setAnchorAngle, setAnchorJoin, setAnchorValue,
  setSeparateVertical, stretchLength, useProfileEdit, withInsertedProfile,
} from './customProfileEdit';
import type { CurvesResponse } from '../api/freeformProfile';

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
    const { after } = stretchLength(design, 240);
    expect(after.length).toBe(240);
    expect(after.profile_h!.points.map((point) => point.t)).toEqual(design.profile_h!.points.map((point) => point.t));
    expect(after.profile_h!.points[2].handle_out).toEqual([20, 4]);
    expect(after.profile_h!.points[1].angle_deg).toBeCloseTo(Math.atan(Math.tan(20 * Math.PI / 180) / 2) * 180 / Math.PI, 12);
    expect(after.profile_h!.throat_angle_deg).toBeCloseTo(Math.atan(Math.tan(15.5 * Math.PI / 180) / 2) * 180 / Math.PI, 12);
    // The retained V is transformed too.
    expect(after.profile_v!.points[1].angle_deg).toBeCloseTo(Math.atan(Math.tan(30 * Math.PI / 180) / 2) * 180 / Math.PI, 12);
    expect(after.cross_sections).toEqual(design.cross_sections);
    design._expressions = { 'profile_h.points.1.angle_deg': { value: 20, raw: '10*2' } };
    expect(() => stretchLength(design, 240)).toThrow('expression "10*2"');
  });

  it('moves only the mouth, keeping interior mm within 1e-12, and refuses a crossing', () => {
    const design = custom();
    design.cross_sections = [{ t: 0, shape: 'ellipse' }, { t: .3, shape: 'ellipse' }, { t: 1, shape: 'ellipse' }];
    const { after } = moveMouth(design, 150);
    after.profile_h!.points.slice(1, -1).forEach((point, index) => {
      expect(Math.abs(point.t * 150 - design.profile_h!.points[index + 1].t * 120)).toBeLessThan(POSITION_ROUND_TRIP_TOLERANCE);
    });
    expect(after.profile_h!.points.at(-1)!.t).toBe(1);
    expect(after.profile_h!.points[2].handle_out).toEqual([10, 4]);
    expect(Math.abs(after.cross_sections![1].t * 150 - 36)).toBeLessThan(POSITION_ROUND_TRIP_TOLERANCE);
    expect(() => moveMouth(design, 90)).toThrow('at or beyond the new mouth');
    expect(() => moveMouth(design, 120)).toThrow('unchanged');
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
    const adopted = applyPastedProfile(design, pasted, { target: 'H', adoptLength: true, join: 'smooth' }).after;
    expect(adopted.length).toBe(200);
    expect(adopted.profile_h!.points.some((point) => point.join)).toBe(false);
    // The untouched V is stretched explicitly with the adopted length, not rescaled silently.
    const stretch = (angle: number) => Math.atan2(Math.sin(angle * Math.PI / 180), (200 / 120) * Math.cos(angle * Math.PI / 180)) * 180 / Math.PI;
    expect(adopted.profile_v!.throat_angle_deg).toBeCloseTo(stretch(15.5), 12);
    expect(adopted.profile_v!.mouth_angle_deg).toBeCloseTo(stretch(60), 12);
    const tooMany = { points: Array.from({ length: 65 }, (_value, index) => ({ t: index / 64, r: 10 + index })), importedLength: 120 };
    expect(() => applyPastedProfile(design, tooMany, { target: 'H', adoptLength: false, join: 'corner' })).toThrow('at most 64');
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
