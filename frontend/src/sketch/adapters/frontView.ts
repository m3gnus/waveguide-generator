/**
 * The front view of a design assembly: the horn mouth, the front baffle and
 * the woofers on it, in the horn-throat frame (x right, y up, mm).
 *
 * Solver-free. The state is the design's `assembly` itself, so every edit is
 * one of the identity-preserving operations of `stores/assembly` and lands in
 * project history through `frontViewBinding`. The server, which owns placement
 * (the design mesher's closed form), supplies the context: outlines, mouth
 * edges and the resolved woofer centres for the assembly it was asked about.
 * While an edit is newer than that context, a woofer's centre follows its own
 * placement change exactly (a gap or radius shifts y, a free centre moves, a
 * spacing distance slides along its ray); anything else is shown at its last
 * resolved centre, marked stale, until the next context arrives.
 */
import type { AssemblyFrontContext } from '../../api/assembly';
import {
  DEFAULT_WOOFER_GAP_MM, DEFAULT_WOOFER_RADIUS_MM, addWoofer, deleteWoofer,
  type AssemblyAnchor, type AssemblyPlacement, type DesignAssembly,
} from '../../stores/assembly';
import type { EmbeddedDesignUpdates } from '../../stores/designEdit';
import type { XY } from '../solver/types';
import { arrow } from '../dimensionGraphics';
import { worldToScreen, type Bounds, type View } from '../view';
import type { ProjectBinding } from '../workspace/projectHistoryAuthority';
import type { DisplayItem, DomainAdapter, HitCandidate, PropertiesSection } from '../workspace/types';

/** A server context and the assembly it resolved. */
export interface FrontViewContext {
  context: AssemblyFrontContext;
  assembly: DesignAssembly;
}

/** One woofer edit, built from the `stores/assembly` operations. */
export interface FrontCommand { label: string; edit(assembly: DesignAssembly): DesignAssembly }

export interface WooferView { id: string; label: string; enabled: boolean; center?: XY; radius: number; stale: boolean }

const woofersOf = (assembly: DesignAssembly) => assembly.woofers ?? [];
const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const round = (value: number) => Math.round(value * 10) / 10;

/** The resolved centre of every woofer of `assembly`, given the last server context. */
export function frontWoofers(assembly: DesignAssembly, resolved: FrontViewContext | undefined): WooferView[] {
  const centres = new Map((resolved?.context.woofers ?? []).map(row => [row.radiator_id, { x: row.x_mm, y: row.y_mm }]));
  const before = new Map(woofersOf(resolved?.assembly ?? {}).map(woofer => [woofer.id, woofer]));
  const own = (id: string) => {
    const now = woofersOf(assembly).find(woofer => woofer.id === id), then = before.get(id);
    return !!now && !!then && sameJson(now.placement, then.placement) && now.radius_mm === then.radius_mm;
  };
  return woofersOf(assembly).map(woofer => {
    const view = { id: woofer.id, label: woofer.label, enabled: woofer.enabled !== false, radius: woofer.radius_mm };
    const then = before.get(woofer.id), centre = centres.get(woofer.id);
    if (!then || !centre || !view.enabled) return { ...view, stale: !!centre, ...(centre ? { center: centre } : {}) };
    const now = woofer.placement, old = then.placement;
    const stale = { ...view, center: centre, stale: true };
    if (now.anchor !== old.anchor || now.spacing_to?.radiator_id !== old.spacing_to?.radiator_id) return stale;
    if (now.spacing_to && old.spacing_to) {
      const target = centres.get(now.spacing_to.radiator_id);
      if (!target || !own(now.spacing_to.radiator_id) || now.gap_mm !== old.gap_mm
        || now.x_mm !== old.x_mm || now.y_mm !== old.y_mm) return stale;
      const length = Math.hypot(centre.x - target.x, centre.y - target.y);
      const scale = now.spacing_to.distance_mm / length;
      return { ...view, stale: false, center: { x: target.x + (centre.x - target.x) * scale, y: target.y + (centre.y - target.y) * scale } };
    }
    if (now.anchor === 'free') {
      return { ...view, stale: false, center: { x: centre.x + (now.x_mm! - old.x_mm!), y: centre.y + (now.y_mm! - old.y_mm!) } };
    }
    const shift = now.gap_mm! - old.gap_mm! + woofer.radius_mm - then.radius_mm;
    return { ...view, stale: false, center: { x: centre.x, y: centre.y + (now.anchor === 'above' ? shift : -shift) } };
  });
}

/** The placement a drag by `delta` asks for, from the placement at its start. */
export function draggedPlacement(assembly: DesignAssembly, id: string, delta: XY, resolved: FrontViewContext | undefined): AssemblyPlacement {
  const woofer = woofersOf(assembly).find(item => item.id === id);
  if (!woofer) throw new Error('Unknown woofer.');
  const placement = woofer.placement;
  if (placement.spacing_to) {
    const views = frontWoofers(assembly, resolved);
    const centre = views.find(view => view.id === id)?.center, target = views.find(view => view.id === placement.spacing_to!.radiator_id)?.center;
    if (!centre || !target) throw new Error('The front view is updating. Try again in a moment.');
    const length = Math.hypot(centre.x - target.x, centre.y - target.y);
    const along = (delta.x * (centre.x - target.x) + delta.y * (centre.y - target.y)) / length;
    return { ...placement, spacing_to: { ...placement.spacing_to, distance_mm: Math.max(0.1, round(placement.spacing_to.distance_mm + along)) } };
  }
  if (placement.anchor === 'free') return { ...placement, x_mm: round(placement.x_mm! + delta.x), y_mm: round(placement.y_mm! + delta.y) };
  const gap = placement.gap_mm! + (placement.anchor === 'above' ? delta.y : -delta.y);
  return { ...placement, gap_mm: Math.max(0, round(gap)) };
}

/** Switching anchor keeps the woofer where it is drawn whenever that is possible. */
export function anchoredPlacement(assembly: DesignAssembly, id: string, anchor: AssemblyAnchor,
  resolved: FrontViewContext | undefined, defaultGapMm: number): AssemblyPlacement {
  const woofer = woofersOf(assembly).find(item => item.id === id);
  if (!woofer) throw new Error('Unknown woofer.');
  const { spacing_to } = woofer.placement;
  const centre = frontWoofers(assembly, resolved).find(view => view.id === id)?.center;
  const context = resolved?.context;
  if (anchor === 'free') return { anchor, x_mm: round(centre?.x ?? 0), y_mm: round(centre?.y ?? 0), ...(spacing_to ? { spacing_to } : {}) };
  // The mouth edges are the server's; the gap is measured from them to the aperture edge.
  const edge = anchor === 'above' ? context?.mouth_top_mm : context?.mouth_bottom_mm;
  const gap = centre && edge !== undefined ? (anchor === 'above' ? centre.y - edge : edge - centre.y) - woofer.radius_mm : defaultGapMm;
  return { anchor, gap_mm: Math.max(0, round(gap)), ...(spacing_to ? { spacing_to } : {}) };
}

function polyline(id: string, role: string, points: XY[], closed: boolean, stale: boolean): DisplayItem {
  return { id, kind: 'polyline', role, editable: false, points, closed, stale };
}

function rect(x0: number, x1: number, y0: number, y1: number): XY[] {
  return [{ x: x0, y: y0 }, { x: x1, y: y0 }, { x: x1, y: y1 }, { x: x0, y: y1 }];
}

/** One dimension between two world points, `px` screen pixels to the left of a to b. */
function span(id: string, view: View, a: XY, b: XY, px: number, text: string): DisplayItem {
  const p = worldToScreen(view, a), q = worldToScreen(view, b), length = Math.hypot(q.x - p.x, q.y - p.y) || 1;
  const t = { x: (q.x - p.x) / length, y: (q.y - p.y) / length }, n = { x: t.y, y: -t.x };
  const from = { x: p.x + n.x * px, y: p.y + n.y * px }, to = { x: q.x + n.x * px, y: q.y + n.y * px };
  const extension = px ? `M${p.x},${p.y} L${from.x},${from.y} M${q.x},${q.y} L${to.x},${to.y} ` : '';
  const vertical = Math.abs(t.y) > Math.abs(t.x) && length > 48, sideways = Math.abs(t.y) > Math.abs(t.x) && !vertical;
  // Clear of the line on the side away from the drawing; a dimension on the geometry sits just above it.
  const at = { x: (from.x + to.x) / 2 + (vertical ? 12 : sideways ? 30 : 0), y: (from.y + to.y) / 2 + (vertical ? 0 : sideways ? 4 : px ? 15 : -5) };
  return { id, kind: 'dimension', role: 'front-dimension', editable: false, driving: false, text, layout: {
    at, rotation: vertical ? -90 : 0, placement: { offset: px, along: 0 },
    path: `${extension}M${from.x},${from.y} L${to.x},${to.y}`,
    arrows: `${arrow(from, Math.atan2(t.y, t.x))} ${arrow(to, Math.atan2(-t.y, -t.x))}`,
  } };
}

/** Text on the drawing: a dimension with nothing to draw. */
function caption(id: string, view: View, world: XY, dy: number, text: string, state: string[] = []): DisplayItem {
  const at = worldToScreen(view, world);
  return { id, kind: 'dimension', role: 'front-caption', editable: false, driving: true, text, state,
    layout: { at: { x: at.x, y: at.y + dy }, rotation: 0, placement: { offset: 0, along: 0 }, path: '', arrows: '' } };
}

const number = (value: number) => String(Number(value.toFixed(1)));
const mm = (value: number) => `${number(value)} mm`;

function dimensionItems(state: DesignAssembly, resolved: FrontViewContext, view: View, selected: WooferView | undefined): DisplayItem[] {
  const { baffle, mouth_outline_mm: outline } = resolved.context;
  const items: DisplayItem[] = [];
  if (baffle) {
    const { x0_mm: x0, x1_mm: x1, y0_mm: y0, y1_mm: y1 } = baffle;
    items.push(span('dimension:baffle-width', view, { x: x1, y: y0 }, { x: x0, y: y0 }, 24, mm(x1 - x0)));
    items.push(span('dimension:baffle-height', view, { x: x1, y: y1 }, { x: x1, y: y0 }, 24, mm(y1 - y0)));
  }
  if (outline?.length) {
    const xs = outline.map(([x]) => x), ys = outline.map(([, y]) => y);
    items.push(caption('dimension:mouth', view, { x: (Math.min(...xs) + Math.max(...xs)) / 2, y: Math.max(...ys) }, 22,
      `Mouth ${number(Math.max(...xs) - Math.min(...xs))} × ${number(Math.max(...ys) - Math.min(...ys))} mm`));
  }
  if (selected?.center) {
    const { center, radius } = selected;
    items.push(span('dimension:woofer', view, { x: center.x - radius, y: center.y }, { x: center.x + radius, y: center.y }, 0, `⌀ ${mm(2 * radius)}`));
    const placement = woofersOf(state).find(woofer => woofer.id === selected.id)?.placement, context = resolved.context;
    if (placement && placement.anchor !== 'free' && !placement.spacing_to) {
      const above = placement.anchor === 'above', edge = above ? context.mouth_top_mm : context.mouth_bottom_mm;
      if (edge !== undefined) items.push(span('dimension:gap', view, { x: 0, y: edge }, { x: 0, y: center.y + (above ? -radius : radius) }, 0, mm(placement.gap_mm!)));
    }
  }
  return items;
}

/** The clearances the server reports below its minimum, as what is wrong with each woofer. */
export function placementProblems(state: DesignAssembly, resolved: FrontViewContext | undefined): Map<string, string[]> {
  const context = resolved?.context, problems = new Map<string, string[]>();
  const minimum = context?.minimum_clearance_mm ?? 0, labels = new Map(woofersOf(state).map(woofer => [woofer.id, woofer.label]));
  for (const [id, clearance] of Object.entries(context?.clearances ?? {})) {
    const label = labels.get(id);
    if (label === undefined) continue;
    const found: string[] = [];
    const check = (distance: number | undefined, what: string) => {
      if (distance !== undefined && distance < minimum) found.push(distance < 0 ? `${label} overlaps ${what} by ${Math.abs(distance).toFixed(1)} mm.` : `${label} is too close to ${what}.`);
    };
    check(clearance.mouth_mm, 'the mouth');
    check(clearance.baffle_edge_mm, 'the baffle edge');
    for (const [other, distance] of Object.entries(clearance.woofers)) {
      if (id < other) check(distance, labels.get(other) ?? 'another woofer');
    }
    if (found.length) problems.set(id, found);
  }
  return problems;
}

function contextItems(resolved: FrontViewContext | undefined, stale: boolean): DisplayItem[] {
  const context = resolved?.context;
  if (!context) return [];
  const items: DisplayItem[] = [];
  const baffle = context.baffle;
  if (baffle) {
    items.push(polyline('context:baffle', 'front-baffle', rect(baffle.x0_mm, baffle.x1_mm, baffle.y0_mm, baffle.y1_mm), true, stale));
    if (baffle.edge_mm > 0) {
      const inset = baffle.edge_mm;
      items.push(polyline('context:baffle-edge', 'front-baffle-edge',
        rect(baffle.x0_mm + inset, baffle.x1_mm - inset, baffle.y0_mm + inset, baffle.y1_mm - inset), true, stale));
    }
  }
  if (context.mouth_outline_mm?.length) {
    items.push(polyline('context:mouth', 'front-mouth', context.mouth_outline_mm.map(([x, y]) => ({ x, y })), true, stale));
  }
  return items;
}

/** The front view's adapter. `resolved` reads the latest server context. */
export function frontViewAdapter(resolved: () => FrontViewContext | undefined,
  properties: DomainAdapter<DesignAssembly, DesignAssembly, FrontCommand>['properties'] = () => [] as PropertiesSection[],
): DomainAdapter<DesignAssembly, DesignAssembly, FrontCommand> {
  const visible = (state: DesignAssembly) => frontWoofers(state, resolved()).filter(view => view.enabled && view.center);
  return {
    id: 'assembly-front',
    axes: { x: { label: 'x', unit: 'mm' }, y: { label: 'y', unit: 'mm' } },
    labels: { drawing: 'Front view', tools: 'Front view tools' },
    viewControls: { fitAll: false, legend: false },
    statusLine: (state, cursor, message) => {
      const hint = message || (woofersOf(state).length ? 'Drag a woofer to move it · Delete removes the selection' : '');
      return `x ${cursor.x.toFixed(1)}, y ${cursor.y.toFixed(1)} mm${hint && ` · ${hint}`}`;
    },
    seed: document => document,
    lower: state => ({ ok: true, document: state }),
    status: () => ({ kind: 'valid' }),
    serializeAuthoring: () => undefined,
    apply: (state, command) => {
      try { return command.edit(state); } catch (reason) { return { error: reason instanceof Error ? reason.message : String(reason) }; }
    },
    remove: (state, selection) => woofersOf(state).filter(woofer => selection.includes(woofer.id))
      .map(woofer => ({ label: `Delete ${woofer.label}`, edit: assembly => deleteWoofer(assembly, woofer.id) })),
    display: (state, ui) => {
      const current = resolved();
      const items = contextItems(current, !!current && !sameJson(current.assembly, state));
      items.push({ id: 'horn-throat', kind: 'point', role: 'front-hf', editable: false, glyph: 'centre', label: 'HF', at: { x: 0, y: 0 } });
      const problems = placementProblems(state, current), woofers = visible(state);
      for (const view of woofers) {
        const flags = [...ui.selection.includes(view.id) ? ['selected'] : [], ...view.stale ? ['stale'] : [], ...problems.has(view.id) ? ['problem'] : []];
        items.push({ id: `${view.id}#aperture`, kind: 'circle', role: 'front-woofer', editable: false, state: flags, center: view.center!, radius: view.radius });
        items.push({ id: view.id, kind: 'point', role: 'front-woofer-centre', editable: true, glyph: 'centre', state: flags, label: view.label, at: view.center! });
      }
      // Screen-placed marks need a view; the first fit is measured without them.
      if (ui.view) {
        const chosen = ui.selection.length === 1 ? woofers.find(view => view.id === ui.selection[0]) : undefined;
        if (ui.dimensions && current) items.push(...dimensionItems(state, current, ui.view, chosen));
        if (ui.names) items.push(caption('name:hf', ui.view, { x: 0, y: 0 }, -10, 'HF'));
        for (const view of woofers) {
          // Inside the rim, clear of the centre mark and the diameter on the centre line.
          if (ui.names || view === chosen) items.push(caption(`name:${view.id}`, ui.view, { x: view.center!.x, y: view.center!.y + view.radius }, 18, view.label, ui.selection.includes(view.id) ? ['selected'] : []));
        }
      }
      return {
        items, context: [], fitPadding: { top: 0, bottom: 0, right: 0, offsetX: 0, margin: 56 },
        bounds: () => {
          const points: XY[] = items.flatMap(item => item.kind === 'polyline' ? item.points
            : item.kind === 'circle' ? [{ x: item.center.x - item.radius, y: item.center.y - item.radius }, { x: item.center.x + item.radius, y: item.center.y + item.radius }]
              : item.kind === 'point' ? [item.at] : []);
          const xs = points.map(point => point.x), ys = points.map(point => point.y);
          const bounds: Bounds = { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
          return bounds.maxX - bounds.minX > 1 && bounds.maxY - bounds.minY > 1 ? bounds : { minX: -200, maxX: 200, minY: -200, maxY: 200 };
        },
      };
    },
    hits: (state, at, tolerance) => visible(state).flatMap((view): HitCandidate[] => {
      const distance = Math.hypot(at.x - view.center!.x, at.y - view.center!.y);
      if (distance <= tolerance) return [{ id: view.id, kind: 'point', gap: distance }];
      return distance <= view.radius ? [{ id: view.id, kind: 'region', gap: 0 }] : [];
    }),
    marquee: (state, box, crossing) => visible(state).filter(view => {
      const { x, y } = view.center!, r = view.radius;
      return crossing
        ? x + r >= box.minX && x - r <= box.maxX && y + r >= box.minY && y - r <= box.maxY
        : x - r >= box.minX && x + r <= box.maxX && y - r >= box.minY && y + r <= box.maxY;
    }).map(view => view.id),
    dragSet: (state, _selection, hit) => {
      // One woofer per drag: its placement is the one edit, even when others are selected.
      if (!visible(state).some(view => view.id === hit)) return undefined;
      return { ids: [hit], move: (start, delta) => [{ label: 'Move woofer', edit: assembly => {
        const woofer = woofersOf(assembly).find(item => item.id === hit)!;
        const placement = draggedPlacement(start, hit, delta, resolved());
        return { ...assembly, woofers: woofersOf(assembly).map(item => item === woofer ? { ...item, placement } : item) };
      } }] };
    },
    tools: [],
    properties,
  };
}

/**
 * The workspace's binding to the design's `assembly`; `blank` stands in while it
 * has none. Deleting the last woofer leaves nothing an ordinary horn does not
 * have, so the design goes back to having no assembly and to ordinary Solve.
 */
export function frontViewBinding(blank: DesignAssembly): ProjectBinding<DesignAssembly> {
  return {
    paths: ['assembly'], onMissing: () => {},
    read: values => (values.assembly as DesignAssembly | null | undefined) ?? blank,
    write: (state): EmbeddedDesignUpdates => ({ assembly: woofersOf(state).length ? state : undefined }),
  };
}

/** Add a woofer on its anchor in its own signal group; `added` learns its identity. */
export function addWooferCommand(anchor: AssemblyAnchor, resolved: FrontViewContext | undefined,
  added: (id: string) => void = () => {}): FrontCommand {
  const bottom = resolved?.context.mouth_bottom_mm;
  // A free woofer starts where a new "below" one would, then moves freely.
  const y = bottom !== undefined ? round(bottom - DEFAULT_WOOFER_GAP_MM - DEFAULT_WOOFER_RADIUS_MM) : -200;
  return { label: 'Add woofer', edit: assembly => {
    const next = addWoofer(assembly, anchor === 'free' ? { anchor, xMm: 0, yMm: y } : { anchor });
    added(next.id);
    return next.assembly;
  } };
}
