import { describe, expect, it } from 'vitest';
import { emptyAssembly } from '../../stores/assembly';
import type { AssemblyFrontContext } from '../../api/assembly';
import type { DesignAssembly } from '../../stores/assembly';
import { addWooferCommand, anchoredPlacement, draggedPlacement, frontViewAdapter, freePlacementClosed, frontWoofers, noRoomForWoofer, placementProblems, radiusProblem, type FrontViewContext } from './frontView';
import fixture from './frontView.fixture.json';

const before: FrontViewContext = { assembly: fixture.before.assembly as DesignAssembly, context: fixture.before.context as AssemblyFrontContext };
const [W1, W2, W3, W4] = before.assembly.woofers!.map(woofer => woofer.id);
const centre = (assembly: DesignAssembly, id: string, resolved: FrontViewContext | undefined = before) =>
  frontWoofers(assembly, resolved).find(view => view.id === id)!;

describe('front view preview against the placement owner', () => {
  for (const [name, edited] of Object.entries(fixture.cases)) {
    it(`draws ${name} where the server resolves it, or marks it stale`, () => {
      const assembly = edited.assembly as DesignAssembly;
      const server = new Map(edited.context.woofers.map(row => [row.radiator_id, row]));
      for (const view of frontWoofers(assembly, before)) {
        if (view.stale) continue;
        expect(view.center!.x).toBeCloseTo(server.get(view.id)!.x_mm, 9);
        expect(view.center!.y).toBeCloseTo(server.get(view.id)!.y_mm, 9);
      }
    });
  }

  it('previews every own edit live and marks only dependents of a moved target stale', () => {
    const live = (name: keyof typeof fixture.cases) => frontWoofers(fixture.cases[name].assembly as DesignAssembly, before)
      .filter(view => !view.stale).map(view => view.id);
    expect(live('gap-and-radius')).toEqual([W1, W2, W4]);
    expect(live('spacing-distance')).toEqual([W1, W2, W3, W4]);
    expect(live('free-centre')).toEqual([W1, W2, W3, W4]);
  });
});

describe('front view edits', () => {
  it('turns a drag into the placement edit of the dragged woofer', () => {
    expect(draggedPlacement(before.assembly, W1, { x: 30, y: -12.04 }, before)).toEqual({ anchor: 'below', gap_mm: 22 });
    expect(draggedPlacement(before.assembly, W2, { x: 0, y: -50 }, before)).toEqual({ anchor: 'above', gap_mm: 0 });
    expect(draggedPlacement(before.assembly, W4, { x: 14.5, y: 11 }, before)).toMatchObject({ x_mm: -205.5, y_mm: 271 });
    // A spaced woofer slides along its ray: the edit is its distance only.
    const spaced = draggedPlacement(before.assembly, W3, { x: 10, y: -10 }, before);
    const ray = centre(before.assembly, W3), target = centre(before.assembly, W1);
    const unit = { x: ray.center!.x - target.center!.x, y: ray.center!.y - target.center!.y };
    const along = (10 * unit.x - 10 * unit.y) / Math.hypot(unit.x, unit.y);
    expect(spaced.spacing_to!.distance_mm).toBeCloseTo(190 + along, 1);
    expect(spaced).toMatchObject({ anchor: 'free', x_mm: 120, y_mm: -320 });
  });

  it('keeps a woofer where it is drawn when its anchor changes', () => {
    expect(anchoredPlacement(before.assembly, W1, 'free', before, 10)).toEqual({ anchor: 'free', x_mm: 0, y_mm: -190 });
    expect(anchoredPlacement(before.assembly, W4, 'above', before, 10)).toEqual({ anchor: 'above', gap_mm: 120 });
    expect(anchoredPlacement(before.assembly, W4, 'below', undefined, 10)).toEqual({ anchor: 'below', gap_mm: 10 });
  });

  it('selects woofers by aperture, deletes by identity and adds on an anchor', () => {
    const adapter = frontViewAdapter(() => before);
    expect(adapter.hits(before.assembly, { x: 0, y: -190 }, 2, { selection: [] })).toEqual([{ id: W1, kind: 'point', gap: 0 }]);
    expect(adapter.hits(before.assembly, { x: 50, y: -190 }, 2, { selection: [] })).toEqual([{ id: W1, kind: 'region', gap: 0 }]);
    expect(adapter.marquee(before.assembly, { minX: -100, maxX: 100, minY: -300, maxY: 300 }, false, { selection: [] })).toEqual([W1, W2]);
    const [remove] = adapter.remove(before.assembly, [W1]);
    const removed = adapter.apply(before.assembly, remove) as DesignAssembly;
    expect(removed.woofers!.map(woofer => woofer.id)).toEqual([W2, W3, W4]);
    // The spacing relation to the deleted woofer goes with it.
    expect(removed.woofers!.find(woofer => woofer.id === W3)!.placement.spacing_to).toBeUndefined();
    let added = '';
    const next = adapter.apply(before.assembly, addWooferCommand('free', before, id => { added = id; })) as DesignAssembly;
    expect(next.woofers!.at(-1)).toMatchObject({ id: added, placement: { anchor: 'free', x_mm: 0, y_mm: -190 } });
    expect(next.signal_groups!.at(-1)!.members).toEqual([{ radiator_id: added }]);
    const display = adapter.display(next, { selection: [W1], scale: 1 });
    expect(display.items.find(item => item.id === W1)).toMatchObject({ kind: 'point', editable: true, state: ['selected'] });
    // A woofer newer than the context has no resolved centre yet.
    expect(display.items.some(item => item.id === added)).toBe(false);
  });

  it('turns clearances below the minimum into what is wrong, and marks the woofer', () => {
    const tight: FrontViewContext = { ...before, context: { ...before.context, minimum_clearance_mm: 0.1, clearances: {
      [W1]: { mouth_mm: -4, baffle_edge_mm: 0.05, woofers: { [W2]: 30 } },
      [W2]: { mouth_mm: 10, woofers: { [W1]: 30 } },
    } } };
    const problems = placementProblems(before.assembly, tight);
    expect([...problems.keys()]).toEqual([W1]);
    expect(problems.get(W1)).toEqual(['Woofer 1 overlaps the mouth by 4 mm. Raise Gap to mouth, or move it.', 'Woofer 1 is too close to the edge of the baffle. Reduce Radius, move it, or raise the margins under Baffle.']);
    const display = frontViewAdapter(() => tight).display(before.assembly, { selection: [], scale: 1 });
    expect(display.items.find(item => item.id === `${W1}#aperture`)!.state).toContain('problem');
    expect(display.items.find(item => item.id === `${W2}#aperture`)!.state).not.toContain('problem');
  });

  it('never takes a failed search as proof: free placement stays open while the woofer can fit the baffle', () => {
    const ring = Array.from({ length: 64 }, (_, index): [number, number] => [140 * Math.cos(index / 32 * Math.PI), 140 * Math.sin(index / 32 * Math.PI)]);
    const woofers = [[1, 1], [1, -1], [-1, 1], [-1, -1]].map(([x, y], index) => ({ radiator_id: `w${index}`, x_mm: 310 * x, y_mm: 95 * y, radius_mm: 50 }));
    const crowded: FrontViewContext = { ...before, context: { ...before.context, woofers, clearances: {}, mouth_outline_mm: ring, mouth_top_mm: 140, mouth_bottom_mm: -140,
      minimum_clearance_mm: 0.1, baffle: { x0_mm: -400, x1_mm: 400, y0_mm: -165, y1_mm: 165, edge_mm: 18 } } };
    // A radius 80 woofer fits at (220.2, 0), a place the search does not land on:
    // the guidance stays, and Free position stays open for the user to find it.
    const guidance = noRoomForWoofer('free', crowded, () => 25);
    expect(guidance?.reason).toContain('or use Free position to place it yourself');
    expect(guidance?.reason).not.toContain('does not fit anywhere');
    expect(freePlacementClosed(crowded)).toBe(false);
  });

  it('starts a free woofer where it fits, and refuses only a woofer larger than the baffle', () => {
    const circle = Array.from({ length: 64 }, (_, index) => {
      const angle = index / 64 * 2 * Math.PI;
      return [140 * Math.cos(angle), 140 * Math.sin(angle)] as [number, number];
    });
    const context = { ...before.context, woofers: [], clearances: {}, mouth_outline_mm: circle, mouth_top_mm: 140, mouth_bottom_mm: -140, minimum_clearance_mm: 0.1 };
    const wide: FrontViewContext = { ...before, context: { ...context, baffle: { x0_mm: -400, x1_mm: 400, y0_mm: -165, y1_mm: 165, edge_mm: 18 } } };
    const margin = () => 25;
    // Beside the mouth: no room above or below, yet (260, 0) clears everything.
    expect(noRoomForWoofer('free', wide, margin)).toBeNull();
    expect(noRoomForWoofer('below', wide, margin)).not.toBeNull();
    const added = addWooferCommand('free', wide).edit(emptyAssembly()).woofers!.at(-1)!;
    const { x_mm: x, y_mm: y } = added.placement;
    expect(Math.hypot(x!, y!)).toBeGreaterThanOrEqual(140 + 80 + 0.1);
    expect(Math.abs(x!)).toBeLessThanOrEqual(400 - 18 - 80 - 0.1);
    expect(Math.abs(y!)).toBeLessThanOrEqual(165 - 18 - 80 - 0.1);
    // Only a baffle smaller than the woofer closes free placement.
    const small: FrontViewContext = { ...wide, context: { ...wide.context, baffle: { x0_mm: -400, x1_mm: 400, y0_mm: -90, y1_mm: 90, edge_mm: 18 } } };
    expect(noRoomForWoofer('free', small, margin)?.reason).toContain('taller than');
    expect(freePlacementClosed(small)).toBe(true);
  });

  it('says why a new woofer has no room, what to change, and never changes it', () => {
    const ring = Array.from({ length: 64 }, (_, index): [number, number] => [140 * Math.cos(index / 64 * 2 * Math.PI), 140 * Math.sin(index / 64 * 2 * Math.PI)]);
    const roomy = { baffle: { x0_mm: -165, x1_mm: 165, y0_mm: -340, y1_mm: 165, edge_mm: 18 }, mouth_outline_mm: ring, woofers: [], clearances: {}, mouth_top_mm: 140, mouth_bottom_mm: -140, minimum_clearance_mm: 0.1 };
    const tight: FrontViewContext = { ...before, context: { ...before.context, ...roomy, baffle: { ...roomy.baffle, y0_mm: -165 } } };
    const margin = () => 25;
    expect(noRoomForWoofer('below', { ...before, context: { ...before.context, ...roomy } }, margin)).toBeNull();
    const full = noRoomForWoofer('below', tight, margin)!;
    expect(full).toMatchObject({ updates: { 'enclosure.space_b': 189 }, action: 'Set Bottom margin to 189 mm' });
    expect(full.reason).toBe('A 160 mm woofer needs 170.1 mm below the mouth, and this enclosure has 7 mm. Raise Bottom margin to 189 mm under Baffle, or use Free position.');
    expect(noRoomForWoofer('above', tight, margin)?.updates).toHaveProperty(['enclosure.space_t']);
    expect(noRoomForWoofer('free', tight, margin)).toMatchObject({ updates: { 'enclosure.space_b': 189 }, action: 'Set Bottom margin to 189 mm' });
    expect(freePlacementClosed(tight)).toBe(false);
    // Nowhere found: a free woofer starts on the baffle, as clear as it can get, not at the unfit place below the mouth.
    const start = addWooferCommand('free', tight).edit(emptyAssembly()).woofers!.at(-1)!.placement;
    expect(Math.abs(start.x_mm!)).toBeCloseTo(67, 0);
    expect(Math.abs(start.y_mm!)).toBeCloseTo(67, 0);
    // Another woofer in the anchored place: margins would not help, so no margin is offered.
    const roomyBelow: FrontViewContext = { ...tight, context: { ...tight.context, baffle: { ...tight.context.baffle!, y0_mm: -340 }, woofers: [{ radiator_id: 'w', x_mm: 0, y_mm: -230, radius_mm: 80 }] } };
    expect(noRoomForWoofer('below', roomyBelow, margin)).toMatchObject({ updates: {}, action: null });
    // A baffle too narrow for the woofer needs its width too, or the offer would not work.
    const narrow: FrontViewContext = { ...tight, context: { ...tight.context, baffle: { x0_mm: -70, x1_mm: 70, y0_mm: -165, y1_mm: 165, edge_mm: 18 } } };
    expect(noRoomForWoofer('free', narrow, margin)).toMatchObject({ updates: { 'enclosure.space_b': 189, 'enclosure.space_l': 54, 'enclosure.space_r': 54 } });
    expect(noRoomForWoofer('free', narrow, margin)?.reason).toContain('wider than');
    // Room above is enough for a free woofer, and it starts there.
    const above: FrontViewContext = { ...tight, context: { ...tight.context, baffle: { ...roomy.baffle, y0_mm: -165, y1_mm: 340 } } };
    expect(noRoomForWoofer('free', above, margin)).toBeNull();
    expect(noRoomForWoofer('below', above, margin)).not.toBeNull();
    expect(addWooferCommand('free', above).edit(before.assembly).woofers!.at(-1)!.placement).toMatchObject({ y_mm: 230 });
    // With no baffle there is nothing to measure.
    expect(noRoomForWoofer('below', undefined, margin)).toBeNull();
  });

  it('refuses a radius the enclosure cannot hold, and leaves free and spaced woofers to the clearances', () => {
    const baffle = { x0_mm: -165, x1_mm: 165, y0_mm: -340, y1_mm: 165, edge_mm: 18 };
    const fitted: FrontViewContext = { ...before, context: { ...before.context, baffle, mouth_top_mm: 140, mouth_bottom_mm: -140, minimum_clearance_mm: 0.1 } };
    const below = before.assembly.woofers!.find(woofer => woofer.placement.anchor === 'below' && !woofer.placement.spacing_to)!;
    const gap = below.placement.gap_mm ?? 0;
    const longest = (182 - gap - 0.1) / 2;
    expect(radiusProblem(before.assembly, below.id, longest, fitted)).toBeUndefined();
    expect(radiusProblem(before.assembly, below.id, longest + 1, fitted)).toBe(`Too large for this enclosure: at most ${Math.floor(longest * 10) / 10} mm here. Reduce Radius, or raise Bottom margin under Baffle.`);
    const spaced = before.assembly.woofers!.find(woofer => woofer.placement.spacing_to);
    if (spaced) expect(radiusProblem(before.assembly, spaced.id, 900, fitted)).toBeUndefined();
    const lone = emptyAssembly();
    const placed = addWooferCommand('free', fitted).edit(lone).woofers!.at(-1)!;
    const atOrigin: FrontViewContext = { ...fitted, context: { ...fitted.context, woofers: [{ radiator_id: placed.id, x_mm: 0, y_mm: -230, radius_mm: 80 }] } };
    expect(radiusProblem({ ...lone, woofers: [placed] }, placed.id, 500, atOrigin)).toContain('raise the margins under Baffle');
  });

  it('accepts a boundary fit the mesher accepts', () => {
    // 100.1 - 100 is 0.09999999999999432: within the mesher's 1e-8 of 0.1 mm.
    const boundary: FrontViewContext = { ...before, context: { ...before.context, minimum_clearance_mm: 0.1, clearances: {
      [W1]: { mouth_mm: 100.1 - 100, baffle_edge_mm: 100.1 - 100, woofers: {} },
    } } };
    expect(placementProblems(before.assembly, boundary).size).toBe(0);
    const context = { ...before.context, woofers: [], minimum_clearance_mm: 0.1, mouth_top_mm: 100, mouth_bottom_mm: -100,
      baffle: { x0_mm: -500, x1_mm: 500, y0_mm: -290.1, y1_mm: 200, edge_mm: 0 } };
    // The woofer's lowest point lands 100.1 - 100 above the edge once its gap and diameter are placed.
    expect(noRoomForWoofer('below', { ...before, context }, () => 25)).toBeNull();
  });

  it('names one overlap between two woofers once', () => {
    const tight: FrontViewContext = { ...before, context: { ...before.context, minimum_clearance_mm: 0.1, clearances: {
      [W1]: { mouth_mm: 10, woofers: { [W2]: -2 } }, [W2]: { mouth_mm: 10, woofers: { [W1]: -2 } },
    } } };
    expect([...placementProblems(before.assembly, tight).values()].flat()).toEqual(['Woofer 1 overlaps Woofer 2 by 2 mm. Move one of them, or reduce Radius.']);
  });

  it('draws dimensions only when asked, from the same server context', () => {
    const adapter = frontViewAdapter(() => ({ ...before, context: { ...before.context, mouth_outline_mm: [[-100, 0], [100, 0], [0, 100], [0, -100]],
      baffle: { x0_mm: -300, x1_mm: 300, y0_mm: -400, y1_mm: 400, edge_mm: 18 } } }));
    const view = { scale: 1, offsetX: 500, offsetY: 500 };
    const texts = (dimensions: boolean) => adapter.display(before.assembly, { selection: [W1], scale: 1, view, dimensions }).items
      .flatMap(item => item.kind === 'dimension' ? [item.text] : []);
    expect(texts(true)).toEqual(expect.arrayContaining(['600 mm', '800 mm', 'Mouth 200 × 200 mm', '⌀ 160 mm', '10 mm', 'Woofer 1']));
    expect(texts(false)).toEqual(['Woofer 1']);
  });
});
