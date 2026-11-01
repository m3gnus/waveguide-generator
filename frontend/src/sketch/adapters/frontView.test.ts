import { describe, expect, it } from 'vitest';
import type { AssemblyFrontContext } from '../../api/assembly';
import type { DesignAssembly } from '../../stores/assembly';
import { addWooferCommand, anchoredPlacement, draggedPlacement, frontViewAdapter, frontWoofers, placementProblems, type FrontViewContext } from './frontView';
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
    expect(problems.get(W1)).toEqual(['Woofer 1 overlaps the mouth by 4.0 mm.', 'Woofer 1 is too close to the baffle edge.']);
    const display = frontViewAdapter(() => tight).display(before.assembly, { selection: [], scale: 1 });
    expect(display.items.find(item => item.id === `${W1}#aperture`)!.state).toContain('problem');
    expect(display.items.find(item => item.id === `${W2}#aperture`)!.state).not.toContain('problem');
  });

  it('names one overlap between two woofers once', () => {
    const tight: FrontViewContext = { ...before, context: { ...before.context, minimum_clearance_mm: 0.1, clearances: {
      [W1]: { mouth_mm: 10, woofers: { [W2]: -2 } }, [W2]: { mouth_mm: 10, woofers: { [W1]: -2 } },
    } } };
    expect([...placementProblems(before.assembly, tight).values()].flat()).toEqual(['Woofer 1 overlaps Woofer 2 by 2.0 mm.']);
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
